from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote


@dataclass(frozen=True)
class ViewWriteConstraints:
    values: dict[str, Any]
    warnings: list[str]

    @property
    def conflicts(self) -> list[str]:
        return [warning for warning in self.warnings if warning.startswith("view_constraint_conflict:")]

    @property
    def unsupported(self) -> list[str]:
        return [warning for warning in self.warnings if warning not in self.conflicts]

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "warnings": list(self.warnings),
            "unsupported": self.unsupported,
            "conflicts": self.conflicts,
        }


def _schema_property_lookup(schema: dict[str, Any], view: dict[str, Any] | None = None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name, field_schema in schema.items():
        if not isinstance(field_schema, dict):
            continue
        lookup[str(name)] = str(name)
        field_name = field_schema.get("name")
        if isinstance(field_name, str) and field_name:
            lookup[field_name] = str(name)
        field_id = field_schema.get("id")
        if isinstance(field_id, str) and field_id:
            lookup[field_id] = str(name)
            lookup[unquote(field_id)] = str(name)
    configuration = view.get("configuration") if isinstance(view, dict) else None
    properties = configuration.get("properties") if isinstance(configuration, dict) else None
    if isinstance(properties, list):
        for item in properties:
            if not isinstance(item, dict):
                continue
            property_id = item.get("property_id")
            property_name = item.get("property_name")
            if isinstance(property_id, str) and isinstance(property_name, str) and property_name in lookup:
                lookup[property_id] = lookup[property_name]
    return lookup


def _constraint_value(predicate: dict[str, Any]) -> Any:
    for property_type in (
        "status",
        "select",
        "checkbox",
        "multi_select",
        "relation",
        "people",
        "date",
    ):
        condition = predicate.get(property_type)
        if not isinstance(condition, dict):
            continue
        if "equals" in condition:
            return condition["equals"]
        if "contains" in condition:
            return condition["contains"]
    return None


def _status_options(field_schema: dict[str, Any]) -> list[dict[str, Any]]:
    status = field_schema.get("status")
    options = status.get("options") if isinstance(status, dict) else None
    if not isinstance(options, list):
        options = field_schema.get("options")
    return [option for option in options if isinstance(option, dict)] if isinstance(options, list) else []


def _status_groups(field_schema: dict[str, Any]) -> list[dict[str, Any]]:
    status = field_schema.get("status")
    groups = status.get("groups") if isinstance(status, dict) else None
    return [group for group in groups if isinstance(group, dict)] if isinstance(groups, list) else []


def _normalize_constraint_value(property_name: str, value: Any, schema: dict[str, Any]) -> tuple[Any, str | None]:
    field_schema = schema.get(property_name)
    if not isinstance(value, str) or not isinstance(field_schema, dict) or field_schema.get("type") != "status":
        return value, None
    options = _status_options(field_schema)
    option_names = {option.get("name") for option in options if isinstance(option.get("name"), str)}
    if value in option_names:
        return value, None
    option_names_by_id = {
        option.get("id"): option.get("name")
        for option in options
        if isinstance(option.get("id"), str) and isinstance(option.get("name"), str)
    }
    for group in _status_groups(field_schema):
        if group.get("name") != value:
            continue
        option_ids = group.get("option_ids")
        names = [option_names_by_id[option_id] for option_id in option_ids if option_id in option_names_by_id] if isinstance(option_ids, list) else []
        if len(names) == 1:
            return names[0], None
        return None, f"view_constraint_unsupported_status_group:{property_name}:{value}"
    return value, None


def _single_constraint(
    predicate: dict[str, Any],
    property_lookup: dict[str, str],
    schema: dict[str, Any],
) -> tuple[str | None, Any, str | None]:
    property_ref = predicate.get("property")
    if not isinstance(property_ref, str) or not property_ref:
        return None, None, "view_constraint_unsupported:missing_property"
    property_name = property_lookup.get(property_ref)
    if not property_name:
        return None, None, f"view_constraint_unmapped_property:{property_ref}"
    value = _constraint_value(predicate)
    if value is None:
        return None, None, f"view_constraint_unsupported:{property_name}"
    value, warning = _normalize_constraint_value(property_name, value, schema)
    if warning:
        return None, None, warning
    return property_name, value, None


def _merge_constraint(values: dict[str, Any], property_name: str, value: Any) -> str | None:
    existing = values.get(property_name)
    if property_name in values and existing != value:
        return f"view_constraint_conflict:{property_name}:{existing}:{value}"
    values[property_name] = value
    return None


def _collect_filter_constraints(
    filter_object: Any,
    property_lookup: dict[str, str],
    schema: dict[str, Any],
) -> ViewWriteConstraints:
    if not isinstance(filter_object, dict) or not filter_object:
        return ViewWriteConstraints({}, [])
    if "or" in filter_object:
        return ViewWriteConstraints({}, ["view_constraint_unsupported:compound_or"])

    predicates = filter_object.get("and") if isinstance(filter_object.get("and"), list) else [filter_object]
    values: dict[str, Any] = {}
    warnings: list[str] = []
    for predicate in predicates:
        if not isinstance(predicate, dict):
            warnings.append("view_constraint_unsupported:invalid_predicate")
            continue
        property_name, value, warning = _single_constraint(predicate, property_lookup, schema)
        if warning:
            warnings.append(warning)
            continue
        if property_name is None:
            continue
        conflict = _merge_constraint(values, property_name, value)
        if conflict:
            return ViewWriteConstraints({}, [conflict])
    return ViewWriteConstraints(values, warnings)


def _collect_quick_filter_constraints(
    quick_filters: Any,
    property_lookup: dict[str, str],
    schema: dict[str, Any],
) -> ViewWriteConstraints:
    if not isinstance(quick_filters, dict) or not quick_filters:
        return ViewWriteConstraints({}, [])

    raw_filters = quick_filters.get("filters")
    if isinstance(raw_filters, list):
        return _collect_filter_constraints({"and": raw_filters}, property_lookup, schema)

    values: dict[str, Any] = {}
    warnings: list[str] = []
    for property_ref, condition in quick_filters.items():
        if not isinstance(property_ref, str):
            continue
        property_name = property_lookup.get(property_ref)
        if not property_name:
            warnings.append(f"view_constraint_unmapped_property:{property_ref}")
            continue
        if isinstance(condition, list):
            if len(condition) != 1:
                warnings.append(f"view_constraint_unsupported:{property_name}")
                continue
            value = condition[0]
        elif isinstance(condition, dict):
            value = _constraint_value(condition)
        else:
            value = condition
        if value is None:
            warnings.append(f"view_constraint_unsupported:{property_name}")
            continue
        value, warning = _normalize_constraint_value(property_name, value, schema)
        if warning:
            warnings.append(warning)
            continue
        conflict = _merge_constraint(values, property_name, value)
        if conflict:
            return ViewWriteConstraints({}, [conflict])
    return ViewWriteConstraints(values, warnings)


def derive_view_write_constraints(view: dict[str, Any], schema: dict[str, Any]) -> ViewWriteConstraints:
    property_lookup = _schema_property_lookup(schema, view)
    filter_constraints = _collect_filter_constraints(view.get("filter"), property_lookup, schema)
    quick_filter_constraints = _collect_quick_filter_constraints(view.get("quick_filters"), property_lookup, schema)

    values = dict(filter_constraints.values)
    warnings = list(filter_constraints.warnings)
    for property_name, value in quick_filter_constraints.values.items():
        conflict = _merge_constraint(values, property_name, value)
        if conflict:
            return ViewWriteConstraints({}, [conflict])
    warnings.extend(quick_filter_constraints.warnings)
    return ViewWriteConstraints(values, warnings)
