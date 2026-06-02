from __future__ import annotations

import re
from typing import Any

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PAGE_ID_32_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")


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


def _relation_target(target_structure: dict[str, Any], field_name: str, field_schema: dict[str, Any]) -> str | None:
    target_database_id = field_schema.get("target_database_id")
    if target_database_id:
        return str(target_database_id)

    relation_schema = field_schema.get("relation")
    if isinstance(relation_schema, dict) and relation_schema.get("database_id"):
        return str(relation_schema["database_id"])

    relations = target_structure.get("relations", [])
    if not isinstance(relations, list):
        return None
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if relation.get("field") == field_name and relation.get("target_database_id"):
            return str(relation["target_database_id"])
    return None


def _relation_target_data_source(target_structure: dict[str, Any], field_name: str, field_schema: dict[str, Any]) -> str | None:
    relation_schema = field_schema.get("relation")
    if isinstance(relation_schema, dict) and relation_schema.get("data_source_id"):
        return str(relation_schema["data_source_id"])

    relations = target_structure.get("relations", [])
    if not isinstance(relations, list):
        return None
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if relation.get("field") != field_name:
            continue
        if relation.get("target_data_source_id"):
            return str(relation["target_data_source_id"])
    return None


def _relation_policy(target_structure: dict[str, Any], record_key: str, field_name: str) -> dict[str, Any]:
    relation_mapping = target_structure.get("relation_mapping")
    if not isinstance(relation_mapping, dict):
        return {}
    for key in (record_key, field_name):
        policy = relation_mapping.get(key)
        if isinstance(policy, dict):
            return policy
    return {}


def _looks_like_page_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(_UUID_RE.match(stripped) or _PAGE_ID_32_HEX_RE.match(stripped)) or stripped.startswith(("page_", "page-"))


def _candidate_page_id(candidate: Any) -> str | None:
    if not isinstance(candidate, dict):
        return None
    page_id = candidate.get("page_id") or candidate.get("id")
    return str(page_id) if page_id else None


def _candidate_title(candidate: dict[str, Any]) -> str | None:
    for key in ("title", "name"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            return value
    properties = candidate.get("properties")
    if isinstance(properties, dict):
        for property_value in properties.values():
            if not isinstance(property_value, dict) or property_value.get("type") != "title":
                continue
            title_items = property_value.get("title")
            if not isinstance(title_items, list):
                continue
            text = "".join(
                item.get("plain_text") or item.get("text", {}).get("content", "")
                for item in title_items
                if isinstance(item, dict)
            ).strip()
            if text:
                return text
    return None


def _candidate_fact(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    page_id = _candidate_page_id(candidate)
    if not page_id:
        return None
    fact: dict[str, Any] = {"page_id": page_id, "id": page_id}
    title = _candidate_title(candidate)
    if title:
        fact["title"] = title
        fact["name"] = title
    for key in ("url", "last_edited_time"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            fact[key] = value
    return fact


def _relation_requirement_fact(
    *,
    record_key: str,
    source_value: Any,
    target_field: str,
    target_database_id: str,
    target_data_source_id: str | None,
    matches: list[Any],
) -> dict[str, Any]:
    fact = {
        "record_key": record_key,
        "source_value": source_value,
        "target_field": target_field,
        "target_database_id": target_database_id,
        "target_data_source_id": target_data_source_id,
        "candidates": [candidate for candidate in (_candidate_fact(match) for match in matches) if candidate is not None],
    }
    if target_data_source_id is None:
        fact.pop("target_data_source_id")
    return fact


def _decision_source_key(decision: dict[str, Any]) -> Any:
    return decision.get("source_record_key") if decision.get("source_record_key") is not None else decision.get("record_key")


def _decision_matches_relation(
    decision: dict[str, Any],
    *,
    record_key: str,
    source_value: Any,
    target_field: str,
    target_database_id: str,
    target_data_source_id: str | None,
) -> bool:
    if decision.get("target_type") is not None and decision.get("target_type") != "relation_resolution":
        return False
    if _decision_source_key(decision) is not None and _decision_source_key(decision) != record_key:
        return False
    if decision.get("source_value") is not None and decision.get("source_value") != source_value:
        return False
    decision_field = decision.get("target_field") or decision.get("field")
    if decision_field is not None and decision_field != target_field:
        return False
    if decision.get("target_database_id") is not None and decision.get("target_database_id") != target_database_id:
        return False
    if decision.get("target_data_source_id") is not None and decision.get("target_data_source_id") != target_data_source_id:
        return False
    return True


def _relation_decision(
    decisions: list[dict[str, Any]] | None,
    *,
    record_key: str,
    source_value: Any,
    target_field: str,
    target_database_id: str,
    target_data_source_id: str | None,
) -> dict[str, Any] | None:
    for decision in decisions or []:
        if not isinstance(decision, dict):
            continue
        if _decision_matches_relation(
            decision,
            record_key=record_key,
            source_value=source_value,
            target_field=target_field,
            target_database_id=target_database_id,
            target_data_source_id=target_data_source_id,
        ):
            return decision
    return None


def _decision_page_id(decision: dict[str, Any]) -> str | None:
    for key in ("page_id", "id", "value"):
        value = decision.get(key)
        if isinstance(value, str) and value:
            return value
    candidate = decision.get("candidate")
    return _candidate_page_id(candidate)


def _create_missing_relation_target(
    adapter: Any,
    target_database_id: str,
    target_data_source_id: str | None,
    key: str,
    value: str,
) -> tuple[str | None, str]:
    try:
        page = adapter.create_relation_target_page(
            target_database_id,
            value,
            data_source_id=target_data_source_id,
            extra_properties=None,
        )
    except Exception:
        return None, f"relation_create_failed:{key}:{value}"
    page_id = page.get("id") if isinstance(page, dict) else None
    if not page_id:
        return None, f"relation_create_failed:{key}:{value}"
    return str(page_id), f"relation_created:{key}:{value}"


def _resolve_single_value(
    adapter: Any,
    target_database_id: str,
    key: str,
    target_field: str,
    value: Any,
    *,
    target_data_source_id: str | None = None,
    policy: dict[str, Any] | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    if _looks_like_page_id(value):
        return str(value), None, None
    if not isinstance(value, str) or not value.strip():
        return None, f"relation_unresolved:{key}:{value}", None

    decision = _relation_decision(
        decisions,
        record_key=key,
        source_value=value,
        target_field=target_field,
        target_database_id=target_database_id,
        target_data_source_id=target_data_source_id,
    )
    if decision is not None:
        action = decision.get("action")
        if action in {"choose_existing", "use_existing"}:
            page_id = _decision_page_id(decision)
            if page_id:
                return page_id, None, None
            return None, f"relation_decision_invalid:{key}:{value}", None
        if action in {"skip", "confirm_skip", "confirmed_skip"}:
            return None, f"relation_skipped:{key}:{value}", None

    try:
        matches = adapter.query_database_title_exact(
            target_database_id,
            value,
            data_source_id=target_data_source_id,
        )
    except Exception:
        return None, f"relation_query_failed:{key}:{value}", None

    if len(matches) == 1:
        page_id = _candidate_page_id(matches[0])
        if page_id:
            return str(page_id), None, None
        return None, f"relation_unresolved:{key}:{value}", None
    if not matches:
        if isinstance(policy, dict) and policy.get("create_missing") is True:
            resolved_id, warning = _create_missing_relation_target(adapter, target_database_id, target_data_source_id, key, value)
            return resolved_id, warning, None
        return None, f"relation_unresolved:{key}:{value}", None
    return (
        None,
        f"relation_ambiguous:{key}:{value}",
        _relation_requirement_fact(
            record_key=key,
            source_value=value,
            target_field=target_field,
            target_database_id=target_database_id,
            target_data_source_id=target_data_source_id,
            matches=matches,
        ),
    )


def resolve_record_relations_with_facts(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
    adapter: Any,
    *,
    decisions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    resolved_record = dict(record)
    warnings: list[str] = []
    relation_resolution_requirements: list[dict[str, Any]] = []

    for key, value in list(resolved_record.items()):
        if _is_empty(value):
            continue

        field_name = field_mapping.get(key)
        if not field_name:
            continue

        schema = _field_schema(target_structure, field_name)
        if not schema or schema.get("type") != "relation":
            continue

        target_database_id = _relation_target(target_structure, field_name, schema)
        if not target_database_id:
            resolved_record[key] = None
            warnings.append(f"relation_target_missing:{key}:{field_name}")
            continue
        target_data_source_id = _relation_target_data_source(target_structure, field_name, schema)
        policy = _relation_policy(target_structure, key, field_name)

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
                target_database_id,
                key,
                field_name,
                item,
                target_data_source_id=target_data_source_id,
                policy=policy,
                decisions=decisions,
            )
            if resolved_id and resolved_id not in seen_resolved_ids:
                resolved_values.append(resolved_id)
                seen_resolved_ids.add(resolved_id)
            if warning:
                warnings.append(warning)
            if requirement is not None:
                relation_resolution_requirements.append(requirement)

        if isinstance(value, list):
            resolved_record[key] = resolved_values or None
        else:
            resolved_record[key] = resolved_values[0] if resolved_values else None

    return resolved_record, warnings, {"relation_resolution_requirements": relation_resolution_requirements}


def resolve_record_relations(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
    adapter: Any,
) -> tuple[dict[str, Any], list[str]]:
    resolved_record, warnings, _facts = resolve_record_relations_with_facts(
        record,
        field_mapping,
        target_structure,
        adapter,
    )
    return resolved_record, warnings
