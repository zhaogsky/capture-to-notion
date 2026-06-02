from __future__ import annotations

import pytest

from capture_to_notion.relations import resolve_record_relations, resolve_record_relations_with_facts


class FakeRelationAdapter:
    def __init__(self, responses=None, error=None, create_error=None):
        self.responses = responses or {}
        self.error = error
        self.create_error = create_error
        self.calls = []
        self.create_calls = []

    def query_database_title_exact(self, database_id, title, data_source_id=None):
        self.calls.append((database_id, title, data_source_id) if data_source_id is not None else (database_id, title))
        if self.error is not None:
            raise self.error
        return self.responses.get((database_id, title), [])

    def create_relation_target_page(self, database_id, title, data_source_id=None, extra_properties=None):
        self.create_calls.append((database_id, title, data_source_id, extra_properties))
        if self.create_error is not None:
            raise self.create_error
        return {"id": f"created-{len(self.create_calls)}"}


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


def test_resolves_relation_target_from_nested_relation_database_id():
    adapter = FakeRelationAdapter({("db-authors", "刘慈欣"): [{"id": "author-page-1"}]})
    structure = target_structure(
        author_field={
            "type": "relation",
            "relation": {"database_id": "db-authors", "data_source_id": "ds-authors"},
        }
    ) | {"relations": []}

    resolved, warnings = resolve_record_relations(
        {"author": "刘慈欣"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == "author-page-1"
    assert warnings == []
    assert adapter.calls == [("db-authors", "刘慈欣", "ds-authors")]



def test_relation_lookup_uses_relation_data_source_id_when_available():
    adapter = FakeRelationAdapter({("db-authors", "刘慈欣"): [{"id": "author-page-1"}]})
    structure = target_structure(
        author_field={
            "type": "relation",
            "relation": {"database_id": "db-authors", "data_source_id": "ds-authors"},
        }
    ) | {"relations": []}

    resolved, warnings = resolve_record_relations(
        {"author": "刘慈欣"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == "author-page-1"
    assert warnings == []
    assert adapter.calls == [("db-authors", "刘慈欣", "ds-authors")]



def test_relation_resolution_prefers_target_data_source_schema_when_field_names_overlap():
    adapter = FakeRelationAdapter({("db-authors-new", "CTN E2E Author"): [{"id": "author-page-new"}]})
    structure = {
        "target": {"data_source_id": "ds-books-new"},
        "data_sources": {
            "ds-books-old": {
                "data_source_id": "ds-books-old",
                "schema": {
                    "作者": {
                        "type": "relation",
                        "relation": {"database_id": "db-authors-old", "data_source_id": "ds-authors-old"},
                    }
                },
            },
            "ds-books-new": {
                "data_source_id": "ds-books-new",
                "schema": {
                    "作者": {
                        "type": "relation",
                        "relation": {"database_id": "db-authors-new", "data_source_id": "ds-authors-new"},
                    }
                },
            },
        },
        "relations": [],
    }

    resolved, warnings = resolve_record_relations(
        {"author": "CTN E2E Author"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == "author-page-new"
    assert warnings == []
    assert adapter.calls == [("db-authors-new", "CTN E2E Author", "ds-authors-new")]



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


def test_relation_create_missing_policy_creates_target_page_when_unresolved():
    adapter = FakeRelationAdapter()
    structure = target_structure()
    structure["relation_mapping"] = {"author": {"create_missing": True}}

    resolved, warnings = resolve_record_relations(
        {"author": "肯尼斯·L.费雪（Kenneth L. Fisher）"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == "created-1"
    assert warnings == ["relation_created:author:肯尼斯·L.费雪（Kenneth L. Fisher）"]
    assert adapter.calls == [("db-authors", "肯尼斯·L.费雪（Kenneth L. Fisher）")]
    assert adapter.create_calls == [("db-authors", "肯尼斯·L.费雪（Kenneth L. Fisher）", None, None)]



def test_relation_create_missing_policy_can_be_keyed_by_target_field_name():
    adapter = FakeRelationAdapter()
    structure = target_structure()
    structure["relation_mapping"] = {"作者": {"create_missing": True}}

    resolved, warnings = resolve_record_relations(
        {"author": "肯尼斯·L.费雪（Kenneth L. Fisher）"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == "created-1"
    assert warnings == ["relation_created:author:肯尼斯·L.费雪（Kenneth L. Fisher）"]



def test_relation_create_missing_policy_uses_relation_data_source_id_when_available():
    adapter = FakeRelationAdapter()
    structure = target_structure(
        author_field={
            "type": "relation",
            "relation": {"database_id": "db-authors", "data_source_id": "ds-authors"},
        }
    ) | {"relations": []}
    structure["relation_mapping"] = {"author": {"create_missing": True}}

    resolved, warnings = resolve_record_relations(
        {"author": "肯尼斯·L.费雪（Kenneth L. Fisher）"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == "created-1"
    assert warnings == ["relation_created:author:肯尼斯·L.费雪（Kenneth L. Fisher）"]
    assert adapter.create_calls == [("db-authors", "肯尼斯·L.费雪（Kenneth L. Fisher）", "ds-authors", None)]



def test_relation_create_missing_policy_deduplicates_repeated_list_values():
    adapter = FakeRelationAdapter()
    structure = target_structure()
    structure["relation_mapping"] = {"author": {"create_missing": True}}

    resolved, warnings = resolve_record_relations(
        {"author": ["新作者", "新作者"]},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] == ["created-1"]
    assert warnings == ["relation_created:author:新作者"]
    assert adapter.calls == [("db-authors", "新作者")]
    assert adapter.create_calls == [("db-authors", "新作者", None, None)]



def test_relation_create_missing_policy_does_not_create_ambiguous_match():
    adapter = FakeRelationAdapter({("db-authors", "重名"): [{"id": "a1"}, {"id": "a2"}]})
    structure = target_structure()
    structure["relation_mapping"] = {"author": {"create_missing": True}}

    resolved, warnings = resolve_record_relations(
        {"author": "重名"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_ambiguous:author:重名"]
    assert adapter.create_calls == []



def test_relation_create_missing_policy_reports_create_failure():
    adapter = FakeRelationAdapter(create_error=RuntimeError("boom"))
    structure = target_structure()
    structure["relation_mapping"] = {"author": {"create_missing": True}}

    resolved, warnings = resolve_record_relations(
        {"author": "肯尼斯·L.费雪（Kenneth L. Fisher）"},
        {"author": "作者"},
        structure,
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_create_failed:author:肯尼斯·L.费雪（Kenneth L. Fisher）"]



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


def test_ambiguous_relation_exposes_structured_candidate_facts():
    adapter = FakeRelationAdapter(
        {
            ("db-authors", "重名"): [
                {
                    "id": "a1",
                    "title": "重名 A",
                    "url": "https://notion.so/a1",
                    "last_edited_time": "2026-05-01T00:00:00Z",
                },
                {"page_id": "a2", "name": "重名 B"},
            ]
        }
    )

    resolved, warnings, facts = resolve_record_relations_with_facts(
        {"author": "重名"},
        {"author": "作者"},
        target_structure(
            author_field={
                "type": "relation",
                "relation": {"database_id": "db-authors", "data_source_id": "ds-authors"},
            }
        ) | {"relations": []},
        adapter,
    )

    assert resolved["author"] is None
    assert warnings == ["relation_ambiguous:author:重名"]
    assert facts["relation_resolution_requirements"] == [
        {
            "record_key": "author",
            "source_value": "重名",
            "target_field": "作者",
            "target_database_id": "db-authors",
            "target_data_source_id": "ds-authors",
            "candidates": [
                {
                    "page_id": "a1",
                    "id": "a1",
                    "title": "重名 A",
                    "name": "重名 A",
                    "url": "https://notion.so/a1",
                    "last_edited_time": "2026-05-01T00:00:00Z",
                },
                {"page_id": "a2", "id": "a2", "title": "重名 B", "name": "重名 B"},
            ],
        }
    ]


def test_relation_decision_choose_existing_uses_selected_candidate_page_id():
    adapter = FakeRelationAdapter({("db-authors", "重名"): [{"id": "a1"}, {"id": "a2"}]})

    resolved, warnings, facts = resolve_record_relations_with_facts(
        {"author": "重名"},
        {"author": "作者"},
        target_structure(),
        adapter,
        decisions=[
            {
                "target_type": "relation_resolution",
                "source_record_key": "author",
                "source_value": "重名",
                "target_field": "作者",
                "action": "choose_existing",
                "page_id": "a2",
            }
        ],
    )

    assert resolved["author"] == "a2"
    assert warnings == []
    assert facts["relation_resolution_requirements"] == []
    assert adapter.calls == []


def test_relation_decision_skip_clears_relation_without_querying():
    adapter = FakeRelationAdapter({("db-authors", "重名"): [{"id": "a1"}, {"id": "a2"}]})

    resolved, warnings, facts = resolve_record_relations_with_facts(
        {"author": "重名"},
        {"author": "作者"},
        target_structure(),
        adapter,
        decisions=[
            {
                "target_type": "relation_resolution",
                "source_record_key": "author",
                "source_value": "重名",
                "target_field": "作者",
                "action": "skip",
                "reason": "not_needed",
            }
        ],
    )

    assert resolved["author"] is None
    assert warnings == ["relation_skipped:author:重名"]
    assert facts["relation_resolution_requirements"] == []
    assert adapter.calls == []


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
