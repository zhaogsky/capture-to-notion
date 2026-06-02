from __future__ import annotations

import re
from typing import Any


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _field_schema(target_structure: dict[str, Any], field_name: str) -> dict[str, Any] | None:
    data_sources = target_structure.get("data_sources", {})
    if not isinstance(data_sources, dict):
        return None
    target = target_structure.get("target")
    target_data_source_id = target.get("data_source_id") if isinstance(target, dict) else None
    if isinstance(target_data_source_id, str) and target_data_source_id:
        data_source = data_sources.get(target_data_source_id)
        schema = data_source.get("schema", {}) if isinstance(data_source, dict) else {}
        if isinstance(schema, dict) and isinstance(schema.get(field_name), dict):
            return schema[field_name]
    for data_source in data_sources.values():
        if not isinstance(data_source, dict):
            continue
        schema = data_source.get("schema", {})
        if isinstance(schema, dict) and isinstance(schema.get(field_name), dict):
            return schema[field_name]
    return None


def _looks_like_user_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(_UUID_RE.match(stripped) or _HEX_32_RE.match(stripped)) or stripped.startswith(("user_", "user-"))


def _looks_like_email(value: Any) -> bool:
    return isinstance(value, str) and bool(_EMAIL_RE.match(value.strip()))


def _user_email(user: dict[str, Any]) -> str | None:
    person = user.get("person")
    if isinstance(person, dict) and isinstance(person.get("email"), str) and person.get("email"):
        return person["email"]
    value = user.get("email")
    return value if isinstance(value, str) and value else None


def _user_id(user: Any) -> str | None:
    if not isinstance(user, dict):
        return None
    value = user.get("id") or user.get("user_id")
    return str(value) if value else None


def _user_fact(user: Any) -> dict[str, Any] | None:
    if not isinstance(user, dict):
        return None
    user_id = _user_id(user)
    if not user_id:
        return None
    fact: dict[str, Any] = {"user_id": user_id, "id": user_id}
    for key in ("name", "avatar_url", "type"):
        value = user.get(key)
        if isinstance(value, str) and value:
            fact[key] = value
    email = _user_email(user)
    if email:
        fact["email"] = email
    return fact


def _search_users(adapter: Any, value: str) -> list[dict[str, Any]]:
    if hasattr(adapter, "search_users"):
        users = adapter.search_users(value)
    elif hasattr(adapter, "list_users"):
        lowered = value.casefold()
        users = [
            user
            for user in adapter.list_users()
            if isinstance(user, dict)
            and (
                lowered in str(user.get("name", "")).casefold()
                or lowered in str(_user_email(user) or "").casefold()
            )
        ]
    else:
        return []
    return [user for user in users if isinstance(user, dict)] if isinstance(users, list) else []


def _exact_email_matches(users: list[dict[str, Any]], email: str) -> list[dict[str, Any]]:
    lowered = email.casefold()
    return [user for user in users if (_user_email(user) or "").casefold() == lowered]


def _people_requirement_fact(*, record_key: str, source_value: Any, target_field: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "record_key": record_key,
        "source_value": source_value,
        "target_field": target_field,
        "candidates": [fact for fact in (_user_fact(match) for match in matches) if fact is not None],
    }


def _decision_source_key(decision: dict[str, Any]) -> Any:
    return decision.get("source_record_key") if decision.get("source_record_key") is not None else decision.get("record_key")


def _decision_matches_people(
    decision: dict[str, Any],
    *,
    record_key: str,
    source_value: Any,
    target_field: str,
) -> bool:
    if decision.get("target_type") is not None and decision.get("target_type") != "people_resolution":
        return False
    if _decision_source_key(decision) is not None and _decision_source_key(decision) != record_key:
        return False
    if decision.get("source_value") is not None and decision.get("source_value") != source_value:
        return False
    decision_field = decision.get("target_field") or decision.get("field")
    if decision_field is not None and decision_field != target_field:
        return False
    return True


def _people_decision(
    decisions: list[dict[str, Any]] | None,
    *,
    record_key: str,
    source_value: Any,
    target_field: str,
) -> dict[str, Any] | None:
    for decision in decisions or []:
        if not isinstance(decision, dict):
            continue
        if _decision_matches_people(
            decision,
            record_key=record_key,
            source_value=source_value,
            target_field=target_field,
        ):
            return decision
    return None


def _decision_user_id(decision: dict[str, Any]) -> str | None:
    for key in ("user_id", "id", "value"):
        value = decision.get(key)
        if isinstance(value, str) and value:
            return value
    candidate = decision.get("candidate")
    if isinstance(candidate, dict):
        value = candidate.get("user_id") or candidate.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _resolve_single_value(
    adapter: Any,
    key: str,
    target_field: str,
    value: Any,
    *,
    decisions: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    if _looks_like_user_id(value):
        return str(value), None, None
    if not isinstance(value, str) or not value.strip():
        return None, f"people_unresolved:{key}:{value}", None

    stripped = value.strip()
    if stripped.casefold() == "me" and hasattr(adapter, "get_current_user"):
        try:
            current_user = adapter.get_current_user()
            current_user_id = _user_id(current_user) if isinstance(current_user, dict) and current_user.get("type") == "person" else None
        except Exception:
            current_user_id = None
        if current_user_id:
            return current_user_id, None, None
        return None, f"people_unresolved:{key}:{value}", None

    decision = _people_decision(decisions, record_key=key, source_value=stripped, target_field=target_field)
    if decision is None and stripped != value:
        decision = _people_decision(decisions, record_key=key, source_value=value, target_field=target_field)
    if decision is not None:
        action = decision.get("action")
        if action in {"choose_existing", "use_existing"}:
            user_id = _decision_user_id(decision)
            if user_id:
                return user_id, None, None
            return None, f"people_decision_invalid:{key}:{value}", None
        if action in {"skip", "confirm_skip", "confirmed_skip"}:
            return None, f"people_skipped:{key}:{value}", None

    try:
        matches = _search_users(adapter, stripped)
    except Exception:
        return None, f"people_query_failed:{key}:{value}", None

    if _looks_like_email(stripped):
        matches = _exact_email_matches(matches, stripped)
    if len(matches) == 1:
        user_id = _user_id(matches[0])
        if user_id:
            return user_id, None, None
        return None, f"people_unresolved:{key}:{value}", None
    if not matches:
        return None, f"people_unresolved:{key}:{value}", None
    return (
        None,
        f"people_ambiguous:{key}:{value}",
        _people_requirement_fact(record_key=key, source_value=value, target_field=target_field, matches=matches),
    )


def resolve_record_people_with_facts(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
    adapter: Any,
    *,
    decisions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    resolved_record = dict(record)
    warnings: list[str] = []
    people_resolution_requirements: list[dict[str, Any]] = []

    for key, value in list(resolved_record.items()):
        if _is_empty(value):
            continue
        field_name = field_mapping.get(key)
        if not field_name:
            continue
        schema = _field_schema(target_structure, field_name)
        if not schema or schema.get("type") != "people":
            continue

        values = value if isinstance(value, list) else [value]
        resolved_values: list[str] = []
        seen_inputs: set[str] = set()
        seen_resolved_ids: set[str] = set()
        for item in values:
            if _is_empty(item):
                continue
            input_key = item.strip() if isinstance(item, str) else repr(item)
            if input_key in seen_inputs:
                continue
            seen_inputs.add(input_key)
            resolved_id, warning, requirement = _resolve_single_value(
                adapter,
                key,
                field_name,
                item,
                decisions=decisions,
            )
            if resolved_id and resolved_id not in seen_resolved_ids:
                resolved_values.append(resolved_id)
                seen_resolved_ids.add(resolved_id)
            if warning:
                warnings.append(warning)
            if requirement is not None:
                people_resolution_requirements.append(requirement)

        if isinstance(value, list):
            resolved_record[key] = resolved_values or None
        else:
            resolved_record[key] = resolved_values[0] if resolved_values else None

    return resolved_record, warnings, {"people_resolution_requirements": people_resolution_requirements}


def resolve_record_people(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
    adapter: Any,
) -> tuple[dict[str, Any], list[str]]:
    resolved_record, warnings, _facts = resolve_record_people_with_facts(record, field_mapping, target_structure, adapter)
    return resolved_record, warnings
