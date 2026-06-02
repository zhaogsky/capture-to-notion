from capture_to_notion.people import resolve_record_people_with_facts


class FakePeopleAdapter:
    def __init__(self, users, current_user=None):
        self.users = users
        self.current_user = current_user
        self.search_calls = []
        self.list_calls = 0
        self.current_user_calls = 0

    def get_current_user(self):
        self.current_user_calls += 1
        return self.current_user

    def search_users(self, query):
        self.search_calls.append(query)
        lowered = query.casefold()
        return [
            user
            for user in self.users
            if lowered in str(user.get("name", "")).casefold()
            or lowered in str(user.get("person", {}).get("email", "")).casefold()
        ]

    def list_users(self):
        self.list_calls += 1
        return list(self.users)


def target_structure_with_people_field():
    return {
        "target": {"data_source_id": "ds-tasks"},
        "data_sources": {
            "tasks": {
                "data_source_id": "ds-tasks",
                "schema": {
                    "People": {"type": "people"},
                    "Notes": {"type": "rich_text"},
                },
            }
        },
    }


def test_people_resolver_passes_existing_user_ids_through():
    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": ["user-1", "user-2"]},
        {"people_key": "People"},
        target_structure_with_people_field(),
        FakePeopleAdapter([]),
    )

    assert resolved["people_key"] == ["user-1", "user-2"]
    assert warnings == []
    assert facts == {"people_resolution_requirements": []}


def test_people_resolver_resolves_single_email_match_to_user_id():
    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": "ada@example.com"},
        {"people_key": "People"},
        target_structure_with_people_field(),
        FakePeopleAdapter([
            {"id": "user-ada", "name": "Ada Lovelace", "type": "person", "person": {"email": "ada@example.com"}},
        ]),
    )

    assert resolved["people_key"] == "user-ada"
    assert warnings == []
    assert facts == {"people_resolution_requirements": []}


def test_people_resolver_resolves_me_to_current_person_user_id_without_searching():
    adapter = FakePeopleAdapter([], current_user={"id": "person-user", "type": "person", "name": "Ada"})

    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": "me"},
        {"people_key": "People"},
        target_structure_with_people_field(),
        adapter,
    )

    assert resolved["people_key"] == "person-user"
    assert warnings == []
    assert facts == {"people_resolution_requirements": []}
    assert adapter.current_user_calls == 1
    assert adapter.search_calls == []


def test_people_resolver_rejects_me_when_current_user_is_bot():
    adapter = FakePeopleAdapter([], current_user={"id": "bot-user", "type": "bot", "name": "Capture Bot"})

    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": "me"},
        {"people_key": "People"},
        target_structure_with_people_field(),
        adapter,
    )

    assert resolved["people_key"] is None
    assert warnings == ["people_unresolved:people_key:me"]
    assert facts == {"people_resolution_requirements": []}
    assert adapter.current_user_calls == 1
    assert adapter.search_calls == []


def test_people_resolver_returns_requirement_for_ambiguous_display_name_candidates():
    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": "Alex"},
        {"people_key": "People"},
        target_structure_with_people_field(),
        FakePeopleAdapter([
            {
                "id": "user-1",
                "name": "Alex Chen",
                "avatar_url": "https://example.com/a.png",
                "type": "person",
                "person": {"email": "alex.chen@example.com"},
            },
            {"id": "user-2", "name": "Alex Kim", "type": "bot"},
        ]),
    )

    assert resolved["people_key"] is None
    assert warnings == ["people_ambiguous:people_key:Alex"]
    assert facts["people_resolution_requirements"] == [
        {
            "record_key": "people_key",
            "source_value": "Alex",
            "target_field": "People",
            "candidates": [
                {
                    "user_id": "user-1",
                    "id": "user-1",
                    "name": "Alex Chen",
                    "email": "alex.chen@example.com",
                    "avatar_url": "https://example.com/a.png",
                    "type": "person",
                },
                {"user_id": "user-2", "id": "user-2", "name": "Alex Kim", "type": "bot"},
            ],
        }
    ]


def test_people_resolver_decision_choose_existing_sets_user_id():
    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": "Alex"},
        {"people_key": "People"},
        target_structure_with_people_field(),
        FakePeopleAdapter([]),
        decisions=[
            {
                "target_type": "people_resolution",
                "source_record_key": "people_key",
                "source_value": "Alex",
                "target_field": "People",
                "action": "choose_existing",
                "user_id": "user-2",
            }
        ],
    )

    assert resolved["people_key"] == "user-2"
    assert warnings == []
    assert facts == {"people_resolution_requirements": []}


def test_people_resolver_decision_skip_clears_people_field():
    resolved, warnings, facts = resolve_record_people_with_facts(
        {"people_key": "Alex"},
        {"people_key": "People"},
        target_structure_with_people_field(),
        FakePeopleAdapter([]),
        decisions=[
            {
                "target_type": "people_resolution",
                "source_record_key": "people_key",
                "source_value": "Alex",
                "target_field": "People",
                "action": "skip",
            }
        ],
    )

    assert resolved["people_key"] is None
    assert warnings == ["people_skipped:people_key:Alex"]
    assert facts == {"people_resolution_requirements": []}
