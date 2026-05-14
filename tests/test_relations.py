from __future__ import annotations

import pytest

from capture_to_notion.relations import resolve_record_relations


class FakeRelationAdapter:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def query_database_title_exact(self, database_id, title):
        self.calls.append((database_id, title))
        if self.error is not None:
            raise self.error
        return self.responses.get((database_id, title), [])


def target_structure(author_field={"type": "relation", "target_database_id": "db-authors"}, podcast_field={"type": "relation", "target_database_id": "db-podcasts"}):
    return {
        "data_sources": {
            "db-books": {
                "data_source_id": "db-books",
                "schema": {
                    "作者": author_field,
                    "播客": podcast_field,
                    "备注作者": {"type": "rich_text"},
                },
            }
        },
        "relations": [
            {"data_source_id": "db-books", "field": "作者", "target_database_id": "db-authors"},
            {"data_source_id": "db-books", "field": "播客", "target_database_id": "db-podcasts"},
        ],
    }


def test_resolves_author_by_exact_title_without_mutating_record():
    record = {"title": "书", "author": "刘慈欣"}
    adapter = FakeRelationAdapter({("db-authors", "刘慈欣"): [{"id": "author-page-1"}]})

    resolved, warnings = resolve_record_relations(
        record,
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] == "author-page-1"
    assert warnings == []
    assert record["author"] == "刘慈欣"
    assert adapter.calls == [("db-authors", "刘慈欣")]


def test_resolves_podcast_by_exact_title():
    adapter = FakeRelationAdapter({("db-podcasts", "忽左忽右"): [{"id": "podcast-page-1"}]})

    resolved, warnings = resolve_record_relations(
        {"podcast": "忽左忽右"},
        {"podcast": "播客"},
        target_structure(),
        adapter,
    )

    assert resolved["podcast"] == "podcast-page-1"
    assert warnings == []
    assert adapter.calls == [("db-podcasts", "忽左忽右")]


def test_resolves_schema_driven_relation_key_without_hardcoded_allowlist():
    adapter = FakeRelationAdapter({("db-contributors", "张三"): [{"id": "contributor-page-1"}]})
    structure = target_structure(
        podcast_field={"type": "rich_text"},
    )
    structure["data_sources"]["db-books"]["schema"]["贡献者"] = {
        "type": "relation",
        "target_database_id": "db-contributors",
    }
    structure["relations"].append(
        {"data_source_id": "db-books", "field": "贡献者", "target_database_id": "db-contributors"}
    )

    resolved, warnings = resolve_record_relations(
        {"contributor": "张三"},
        {"contributor": "贡献者"},
        structure,
        adapter,
    )

    assert resolved["contributor"] == "contributor-page-1"
    assert warnings == []
    assert adapter.calls == [("db-contributors", "张三")]


def test_unresolved_relation_warns_and_clears_value():
    adapter = FakeRelationAdapter()

    resolved, warnings = resolve_record_relations(
        {"author": "不存在"},
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_unresolved:author:不存在"]


def test_ambiguous_relation_warns_and_clears_value():
    adapter = FakeRelationAdapter({("db-authors", "重名"): [{"id": "a1"}, {"id": "a2"}]})

    resolved, warnings = resolve_record_relations(
        {"author": "重名"},
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_ambiguous:author:重名"]


def test_mapped_non_relation_field_preserves_value_and_does_not_query():
    adapter = FakeRelationAdapter()

    resolved, warnings = resolve_record_relations(
        {"author": "纯文本作者"},
        {"author": "备注作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] == "纯文本作者"
    assert warnings == []
    assert adapter.calls == []


def test_existing_page_id_preserves_value_and_does_not_query():
    adapter = FakeRelationAdapter()
    page_id = "12345678-1234-1234-1234-123456789abc"

    resolved, warnings = resolve_record_relations(
        {"author": page_id},
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] == page_id
    assert warnings == []
    assert adapter.calls == []


def test_existing_32_character_hex_page_id_preserves_value_and_does_not_query():
    adapter = FakeRelationAdapter()
    page_id = "12345678123412341234123456789abc"

    resolved, warnings = resolve_record_relations(
        {"author": page_id},
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] == page_id
    assert warnings == []
    assert adapter.calls == []


def test_missing_relation_target_warns_and_clears_value():
    adapter = FakeRelationAdapter()

    resolved, warnings = resolve_record_relations(
        {"author": "刘慈欣"},
        {"author": "作者"},
        target_structure(author_field={"type": "relation"}) | {"relations": []},
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_target_missing:author:作者"]
    assert adapter.calls == []


def test_list_values_resolve_independently_and_keep_only_ids():
    adapter = FakeRelationAdapter({("db-authors", "刘慈欣"): [{"id": "author-page-1"}]})
    existing_id = "page_existing_1"

    resolved, warnings = resolve_record_relations(
        {"author": ["刘慈欣", "不存在", existing_id]},
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] == ["author-page-1", existing_id]
    assert warnings == ["relation_unresolved:author:不存在"]
    assert adapter.calls == [("db-authors", "刘慈欣"), ("db-authors", "不存在")]


def test_query_exception_warns_and_clears_value():
    adapter = FakeRelationAdapter(error=RuntimeError("boom"))

    resolved, warnings = resolve_record_relations(
        {"author": "刘慈欣"},
        {"author": "作者"},
        target_structure(),
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_query_failed:author:刘慈欣"]
