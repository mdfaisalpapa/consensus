"""Strict provider-driven scope and rule validation."""
from __future__ import annotations

import json
from typing import Any

import frappe

from consensus.providers.protocol import ScopeQuery
from consensus.security.crypto import canonical_json_bytes


def _scope_types(provider, context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    declarations = provider.list_scope_types(context)
    if not isinstance(declarations, list):
        frappe.throw("Provider scope type response must be a list.")
    types: dict[str, dict[str, Any]] = {}
    for item in declarations:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
            frappe.throw("Provider returned an invalid scope type declaration.")
        if not isinstance(item.get("label"), str) or not item["label"].strip():
            frappe.throw("Provider scope type is missing a display label.")
        for capability in (
            "hierarchical", "multiple_entities", "supports_include_exclude",
            "supports_any_selection", "supports_all_selection",
        ):
            if not isinstance(item.get(capability), bool):
                frappe.throw(f"Provider scope type {item['key']!r} has invalid capability {capability!r}.")
        if item["key"] in types:
            frappe.throw(f"Provider returned duplicate scope type {item['key']!r}.")
        types[item["key"]] = item
    return types


def list_scope_types(provider, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list(_scope_types(provider, context or {}).values())


def list_scope_entities(provider, scope_type: str, query=None, parent_ref=None, context=None):
    supported = _scope_types(provider, context or {})
    if scope_type not in supported:
        frappe.throw("Selected scope type is not registered by the configured provider.")
    entities = provider.list_scope_entities(scope_type, query, parent_ref, context or {})
    if not isinstance(entities, list):
        frappe.throw("Provider scope entity response must be a list.")
    refs = [row.get("entity_ref") for row in entities if isinstance(row, dict)]
    if len(refs) != len(entities) or any(not isinstance(ref, str) or not ref for ref in refs):
        frappe.throw("Provider returned an invalid scope entity.")
    if len(refs) != len(set(refs)):
        frappe.throw("Provider returned duplicate scope entity references.")
    return entities


def resolve_scope_query(
    provider,
    selections: list[dict[str, Any]],
    selection_operator: str = "any",
    conditions: dict[str, Any] | None = None,
    conditions_operator: str = "all",
    context: dict[str, Any] | None = None,
    poll_context: dict[str, Any] | None = None,
) -> tuple[ScopeQuery, list[dict[str, Any]]]:
    if not selections:
        frappe.throw("Select at least one voting scope entity.")
    if selection_operator not in {"any", "all"}:
        frappe.throw("Scope selection operator must be 'any' or 'all'.")
    if conditions_operator not in {"all", "any"}:
        frappe.throw("Scope conditions operator must be 'all' or 'any'.")
    if conditions is not None and not isinstance(conditions, dict):
        frappe.throw("Provider scope conditions must be a JSON object.")

    site_context = context or {}
    declared_types = _scope_types(provider, site_context)
    normalized: list[dict[str, Any]] = []
    by_scope: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()

    for row in selections:
        if not isinstance(row, dict):
            frappe.throw("Each scope selection must be an object.")
        scope_type = str(row.get("scope_type") or "").strip()
        entity_ref = str(row.get("entity_ref") or "").strip()
        inclusion = str(row.get("inclusion") or "include").lower()
        if not scope_type or scope_type not in declared_types:
            frappe.throw(f"Scope type {scope_type!r} is not registered by this provider.")
        if not entity_ref or inclusion not in {"include", "exclude"}:
            frappe.throw("Each scope selection needs an entity and include/exclude mode.")
        pair = (scope_type, entity_ref)
        if pair in seen:
            frappe.throw(f"Duplicate scope selection: {scope_type}/{entity_ref}.")
        seen.add(pair)
        if inclusion == "exclude" and not declared_types[scope_type].get("supports_include_exclude"):
            frappe.throw(f"Provider scope type {scope_type!r} does not support exclusions.")
        normalized.append({
            "scope_type": scope_type,
            "entity_ref": entity_ref,
            "inclusion": inclusion,
            "parent_ref": row.get("parent_ref") or None,
        })
        by_scope.setdefault(scope_type, []).append(entity_ref)

    for scope_type, entity_refs in by_scope.items():
        if len(entity_refs) > 1 and not declared_types[scope_type].get("multiple_entities"):
            frappe.throw(f"Provider scope type {scope_type!r} does not support multiple entities.")

    if selection_operator == "any" and any(
        not declared_types[row["scope_type"]].get("supports_any_selection") for row in normalized
    ):
        frappe.throw("The provider does not support the requested 'any' scope operator.")
    if selection_operator == "all" and any(
        not declared_types[row["scope_type"]].get("supports_all_selection") for row in normalized
    ):
        frappe.throw("The provider does not support the requested 'all' scope operator.")

    resolved: list[dict[str, Any]] = []
    for scope_type, entity_refs in by_scope.items():
        rows = provider.resolve_scope_entities(scope_type, entity_refs, site_context)
        if not isinstance(rows, list):
            frappe.throw("Provider scope resolution response must be a list.")
        returned_refs = [item.get("entity_ref") for item in rows if isinstance(item, dict)]
        if len(returned_refs) != len(rows) or any(not isinstance(ref, str) for ref in returned_refs):
            frappe.throw("Provider returned an invalid resolved scope entity.")
        if len(returned_refs) != len(set(returned_refs)):
            frappe.throw("Provider returned duplicate resolved entity references.")
        if set(returned_refs) != set(entity_refs):
            frappe.throw("Provider returned missing or unexpected scope entities.")
        entities_by_ref = {item["entity_ref"]: item for item in rows}
        for selection in normalized:
            if selection["scope_type"] != scope_type:
                continue
            entity = entities_by_ref[selection["entity_ref"]]
            if not isinstance(entity.get("label"), str) or not entity["label"].strip():
                frappe.throw("Provider scope entity is missing its resolved display label.")
            resolved.append({
                **selection,
                "display_label_snapshot": entity["label"],
                "hierarchy_path_snapshot": entity.get("hierarchy_path", []),
                "resolved_parent_ref": entity.get("parent_ref"),
            })

    proposed: ScopeQuery = {
        "selections": normalized,
        "selection_operator": selection_operator,
        "conditions": conditions or {},
        "conditions_operator": conditions_operator,
    }
    validated = provider.validate_scope_query(proposed, poll_context or {})
    if not isinstance(validated, dict) or validated.get("provider_api_version") != provider.api_version:
        frappe.throw("Provider did not return a valid, versioned canonical scope query.")
    canonical_query = validated.get("query")
    canonical_json = validated.get("canonical_query_json")
    if not isinstance(canonical_query, dict) or not isinstance(canonical_json, str):
        frappe.throw("Provider returned an invalid canonical scope query.")
    if not isinstance(canonical_query.get("conditions"), dict):
        frappe.throw("Provider canonical scope query must contain validated conditions.")
    expected_pairs = {(x["scope_type"], x["entity_ref"], x["inclusion"], x["parent_ref"]) for x in normalized}
    actual_selections = canonical_query.get("selections")
    if not isinstance(actual_selections, list):
        frappe.throw("Provider canonical scope query is missing selections.")
    if (
        canonical_query.get("selection_operator") != selection_operator
        or canonical_query.get("conditions_operator") != conditions_operator
    ):
        frappe.throw("Provider canonical scope query changed the requested combination operators.")
    actual_pairs = [
        (x.get("scope_type"), x.get("entity_ref"), x.get("inclusion"), x.get("parent_ref"))
        for x in actual_selections if isinstance(x, dict)
    ]
    if (
        len(actual_pairs) != len(actual_selections)
        or len(actual_pairs) != len(set(actual_pairs))
        or set(actual_pairs) != expected_pairs
    ):
        frappe.throw("Provider canonical scope query changed or duplicated selected entities.")
    try:
        if json.loads(canonical_json) != canonical_query or canonical_json_bytes(canonical_query).decode("utf-8") != canonical_json:
            frappe.throw("Provider canonical scope JSON does not match its query structure.")
    except (TypeError, ValueError):
        frappe.throw("Provider returned malformed canonical scope JSON.")
    return canonical_query, resolved


def merge_rule_configuration(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic recursive object merge; arrays replace and null removes keys."""
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        frappe.throw("Poll Type defaults and poll-specific rule overrides must be JSON objects.")
    result = dict(defaults)
    for key, value in overrides.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_rule_configuration(result[key], value)
        elif isinstance(value, dict):
            result[key] = merge_rule_configuration({}, value)
        else:
            result[key] = value
    return result


def validate_effective_rule(provider, poll_type, poll_rule_key, poll_rule_config, poll_context):
    default_key = poll_type.default_rule_key
    default_config = json.loads(poll_type.default_rule_config or "{}")
    selected_key = poll_rule_key or default_key
    if not selected_key:
        frappe.throw("A provider eligibility rule must be selected.")
    if selected_key == default_key:
        effective_config = merge_rule_configuration(default_config, poll_rule_config or {})
    else:
        effective_config = poll_rule_config or {}
        if default_key and not poll_rule_key:
            frappe.throw("A different rule key must be explicitly selected.")
    if not isinstance(effective_config, dict):
        frappe.throw("Effective rule configuration must be a JSON object.")
    result = provider.validate_rule_configuration(
        selected_key, effective_config, poll_type.type_key, poll_context
    )
    if (
        not isinstance(result, dict)
        or result.get("rule_key") != selected_key
        or not isinstance(result.get("rule_version"), str)
        or not result.get("rule_version")
        or not isinstance(result.get("config"), dict)
        or not isinstance(result.get("canonical_config_json"), str)
    ):
        frappe.throw("Provider returned an invalid canonical rule configuration.")
    try:
        if (
            json.loads(result["canonical_config_json"]) != result["config"]
            or canonical_json_bytes(result["config"]).decode("utf-8") != result["canonical_config_json"]
        ):
            frappe.throw("Provider canonical rule JSON does not match its configuration.")
    except (TypeError, ValueError):
        frappe.throw("Provider returned malformed canonical rule JSON.")
    return result
