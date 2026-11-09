"""Provider-driven, paginated eligibility snapshots and poll freezing."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import frappe
from frappe.utils import now_datetime

from consensus.providers.registry import get_registered_provider
from consensus.providers.scope_resolution import resolve_scope_query, validate_effective_rule
from consensus.security.crypto import (
    canonical_json_bytes,
    encrypt_protected_value,
    identity_snapshot_commitment,
    voter_reference_digest,
)
from consensus.security.secrets import resolve_secret

PAGE_SIZE = 500
MAX_CANDIDATES = 250_000


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _identity_payload(receipt_identity: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    if not isinstance(receipt_identity, dict):
        frappe.throw("Provider returned no receipt identity.")
    schema_key = receipt_identity.get("schema_key")
    schema_version = receipt_identity.get("schema_version")
    fields = receipt_identity.get("fields")
    if not isinstance(schema_key, str) or not schema_key or not isinstance(schema_version, str) or not schema_version:
        frappe.throw("Provider receipt identity schema is missing its key or version.")
    if not isinstance(fields, list) or not fields:
        frappe.throw("Provider receipt identity must contain display fields.")
    seen: set[str] = set()
    normalized = []
    for item in fields:
        if not isinstance(item, dict):
            frappe.throw("Provider receipt identity fields must be objects.")
        key, label, value = item.get("key"), item.get("label"), item.get("value")
        if not isinstance(key, str) or not key or key in seen:
            frappe.throw("Provider receipt identity field keys must be unique non-empty strings.")
        seen.add(key)
        if not isinstance(label, str) or not label.strip():
            frappe.throw(f"Receipt identity field {key!r} needs a display label.")
        if item.get("required", True) and (not isinstance(value, str) or not value.strip()):
            frappe.throw(f"Required receipt identity field {key!r} is missing.")
        if value is None:
            value = ""
        if not isinstance(value, str):
            frappe.throw(f"Receipt identity field {key!r} must be display text.")
        order = item.get("display_order", 0)
        if not isinstance(order, int):
            frappe.throw(f"Receipt identity field {key!r} has invalid display order.")
        normalized.append({
            "key": key, "label": label.strip(), "value": value,
            "display_order": order, "required": bool(item.get("required", True)),
            "format_key": item.get("format_key"),
        })
    normalized.sort(key=lambda x: (x["display_order"], x["key"]))
    return {"schema_key": schema_key, "schema_version": schema_version, "fields": normalized}, schema_key, schema_version


def build_eligibility_snapshot(poll_name: str) -> dict[str, Any]:
    """Create a complete frozen snapshot. Call only while poll is Draft."""
    poll = frappe.get_doc("Consensus Poll", poll_name)
    poll.check_permission("write")
    if poll.status != "Draft" or (poll.snapshot_state and poll.snapshot_state not in ("Not Started", "Failed")):
        frappe.throw("Eligibility snapshot can only be built for a draft poll.")
    provider = get_registered_provider(poll.provider_key)
    if provider.api_version != poll.provider_api_version:
        frappe.throw("Poll provider API version is no longer available.")
    poll_type = frappe.get_doc("Consensus Poll Type", poll.poll_type)
    context = {
        "poll_type": poll_type.type_key, "poll_name": poll.name,
        "starts_at": str(poll.starts_at), "ends_at": str(poll.ends_at),
        "rule_key": poll.rule_key or poll_type.default_rule_key,
    }
    selections = [row.as_dict() for row in poll.scope_selections]
    scope_query, resolved = resolve_scope_query(
        provider, selections, (poll.scope_selection_operator or "Any").lower(),
        frappe.parse_json(poll.scope_conditions or "{}"),
        (poll.scope_conditions_operator or "All").lower(), {}, context,
    )
    effective_rule = validate_effective_rule(
        provider, poll_type, poll.rule_key,
        frappe.parse_json(poll.rule_config or "{}"), context,
    )
    voter_key, voter_version = resolve_secret("voter_reference_hmac")
    ref_key, ref_version = resolve_secret("provider_reference_encryption")
    identity_key, identity_version = resolve_secret("identity_snapshot_encryption")
    identity_hmac_key, identity_hmac_version = resolve_secret("identity_snapshot_commitment_hmac")
    idem_key, idem_version = resolve_secret("idempotency_hmac")
    signing_key, signing_version = resolve_secret("pdf_signing")
    token = provider.begin_voter_snapshot(context)
    if not isinstance(token, dict) or not token.get("token") or not token.get("provider_data_version"):
        frappe.throw("Provider did not start a versioned voter snapshot.")
    build_id = frappe.generate_hash(length=32)
    poll.db_set({"snapshot_state": "Building", "snapshot_build_id": build_id,
                 "snapshot_started_on": now_datetime()}, update_modified=False)
    frappe.db.delete("Consensus Eligibility", {"poll": poll.name})
    seen_refs: set[str] = set()
    seen_users: set[str] = set()
    seen_user_contexts: set[tuple[str, str | None]] = set()
    cursor = None
    seen_cursors: set[str] = set()
    row_commitments = []
    count = 0
    try:
        while True:
            page = provider.get_voter_candidates(
                scope_query, context, token, cursor, PAGE_SIZE
            )
            if not isinstance(page, dict) or page.get("snapshot") != token:
                frappe.throw("Provider returned a missing or unexpected voter snapshot.")
            candidates = page.get("candidates")
            if not isinstance(candidates, list) or len(candidates) > PAGE_SIZE:
                frappe.throw("Provider returned an invalid candidate page.")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    frappe.throw("Provider candidate must be an object.")
                voter_ref = candidate.get("voter_ref")
                user = candidate.get("frappe_user")
                if not isinstance(voter_ref, str) or not voter_ref.strip():
                    frappe.throw("Provider candidate has no stable voter reference.")
                if voter_ref in seen_refs:
                    frappe.throw("Provider returned a duplicate voter reference.")
                seen_refs.add(voter_ref)
                if len(seen_refs) > MAX_CANDIDATES:
                    frappe.throw("Provider voter snapshot exceeds the configured candidate limit.")
                if not isinstance(user, str) or not user or not frappe.db.exists("User", user):
                    frappe.throw("Every eligible candidate must map to an existing Frappe User in v1.")
                allocation_mode = getattr(poll_type, "allocation_mode", "Single Allocation Per User") or "Single Allocation Per User"
                if allocation_mode == "Multiple Allocations Per User":
                    user_ctx = (user, candidate.get("context_ref"))
                    if user_ctx in seen_user_contexts:
                        frappe.throw("Provider allocated the same Frappe User to multiple voter references for the same context reference.")
                    seen_user_contexts.add(user_ctx)
                else:
                    if user in seen_users:
                        frappe.throw("Provider allocated one Frappe User to multiple voter references in this poll.")
                decision = provider.evaluate_eligibility(
                    candidate, context, effective_rule["rule_key"], effective_rule["config"]
                )
                if not isinstance(decision, dict) or not isinstance(decision.get("eligible"), bool):
                    frappe.throw("Provider returned an invalid eligibility decision.")
                if not decision["eligible"]:
                    continue
                weight = decision.get("voting_weight")
                try:
                    weight = float(weight)
                except (TypeError, ValueError):
                    frappe.throw("Provider returned an invalid voting weight.")
                if not math.isfinite(weight) or weight <= 0:
                    frappe.throw("Eligible voter weight must be a finite positive number.")
                identity, schema_key, schema_version = _identity_payload(
                    provider.get_receipt_identity(voter_ref, "receipt", None)
                )
                digest = voter_reference_digest(provider.key, poll.name, voter_ref, voter_version)
                encrypted_ref = encrypt_protected_value(
                    voter_ref.encode(), "provider_reference_encryption", ref_version,
                    poll_name=poll.name, provider_key=provider.key, voter_ref_digest=digest,
                )
                encrypted_identity = encrypt_protected_value(
                    _json(identity).encode(), "identity_snapshot_encryption", identity_version,
                    poll_name=poll.name, provider_key=provider.key, voter_ref_digest=digest,
                )
                commitment = identity_snapshot_commitment(
                    poll.name, provider.key, digest, schema_key, schema_version,
                    identity["fields"], identity_hmac_version,
                )
                row_core = {
                    "voter_ref_digest": digest, "voting_weight": f"{weight:.3f}",
                    "voter_user": user, "voter_category": decision.get("voter_category"),
                    "context_ref": decision.get("context_ref"),
                    "identity_commitment": commitment, "identity_schema_key": schema_key,
                    "identity_schema_version": schema_version,
                    "scope_snapshot": resolved, "provider": provider.key,
                    "provider_api_version": provider.api_version,
                    "rule_key": effective_rule["rule_key"],
                    "rule_version": effective_rule["rule_version"],
                }
                row_hash = hashlib.sha256(canonical_json_bytes(row_core)).hexdigest()
                doc = frappe.get_doc({
                    "doctype": "Consensus Eligibility", "poll": poll.name,
                    "voter_ref_digest": digest,
                    "encrypted_voter_reference": encrypted_ref["ciphertext"],
                    "reference_nonce": encrypted_ref["nonce"],
                    "reference_key_version": encrypted_ref["key_version"],
                    "provider_key": provider.key, "provider_api_version": provider.api_version,
                    "rule_key": effective_rule["rule_key"], "rule_version": effective_rule["rule_version"],
                    "scope_snapshot_json": _json(resolved), "voter_user": user,
                    "voter_category": decision.get("voter_category"), "context_ref": decision.get("context_ref"),
                    "encrypted_identity_snapshot": encrypted_identity["ciphertext"],
                    "identity_nonce": encrypted_identity["nonce"],
                    "identity_key_version": encrypted_identity["key_version"],
                    "identity_schema_key": schema_key, "identity_schema_version": schema_version,
                    "identity_commitment": commitment,
                    "identity_hmac_key_version": identity_hmac_version,
                    "voting_weight": weight, "status": "Eligible", "snapshot_hash": row_hash,
                })
                doc.insert(ignore_permissions=True)
                seen_users.add(user)
                row_commitments.append((digest, row_hash))
                count += 1
            next_cursor = page.get("next_cursor")
            if not next_cursor:
                break
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors or next_cursor == cursor:
                frappe.throw("Provider returned a repeated or invalid pagination cursor.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        row_commitments.sort()
        options = frappe.get_all(
            "Consensus Poll Option", filters={"poll": poll.name, "enabled": 1},
            fields=["name", "option_key", "option_label", "display_order"],
            order_by="display_order asc, name asc",
        )
        if len(options) < 2:
            frappe.throw("A poll requires at least two enabled options before its snapshot can complete.")
        frozen_options = [{
            "name": row.name, "option_key": row.option_key,
            "option_label": row.option_label, "display_order": row.display_order,
        } for row in options]
        option_hash = hashlib.sha256(canonical_json_bytes(frozen_options)).hexdigest()
        for option in options:
            frappe.db.set_value("Consensus Poll Option", option.name, {
                "frozen_label": option.option_label,
                "frozen_display_order": option.display_order,
            }, update_modified=False)
        frozen_title = poll.title
        poll_type_config_hash = hashlib.sha256(canonical_json_bytes({
            "type_key": poll_type.type_key,
            "configuration_version": poll_type.configuration_version,
            "default_rule_key": poll_type.default_rule_key,
            "default_rule_config": frappe.parse_json(poll_type.default_rule_config or "{}"),
        })).hexdigest()
        snapshot_core = {
            "poll": poll.name, "site": frappe.local.site,
            "frozen_title": frozen_title, "poll_type_label": poll_type.label,
            "poll_type": poll_type.type_key,
            "poll_type_configuration_version": poll_type.configuration_version,
            "poll_type_config_hash": poll_type_config_hash,
            "provider_key": provider.key, "provider_api_version": provider.api_version,
            "provider_data_version": token["provider_data_version"],
            "rule_key": effective_rule["rule_key"], "rule_version": effective_rule["rule_version"],
            "rule_config": effective_rule["config"], "scope_query": scope_query,
            "resolved_scope": resolved, "eligibility_rows": row_commitments,
            "option_hash": option_hash,
            "voter_hmac_key_version": voter_version,
            "idempotency_hmac_key_version": idem_version,
            "identity_hmac_key_version": identity_hmac_version,
            "reference_encryption_key_version": ref_version,
            "identity_encryption_key_version": identity_version,
            "signing_key_version": signing_version,
            "approved_publication_id": (token.get("approved_publication_id") or None) if isinstance(token, dict) else None,
            "approved_publication_version": (int(token["approved_publication_version"]) if token.get("approved_publication_version") else None) if isinstance(token, dict) else None,
            "approved_electorate_content_hash": (token.get("approved_electorate_content_hash") or None) if isinstance(token, dict) else None,
        }
        snapshot_hash = hashlib.sha256(canonical_json_bytes(snapshot_core)).hexdigest()
        pub_meta = {}
        if isinstance(token, dict):
            for k in ("approved_publication_id", "approved_publication_version", "approved_electorate_content_hash"):
                if token.get(k) is not None:
                    pub_meta[k] = token[k]
        frappe.db.set_value("Consensus Poll", poll.name, {
            "snapshot_state": "Complete", "snapshot_completed_on": now_datetime(),
            "snapshot_poll_type_key": poll_type.type_key,
            "snapshot_poll_type_config_hash": poll_type_config_hash,
            "snapshot_poll_type_configuration_version": poll_type.configuration_version,
            "snapshot_provider_key": provider.key, "snapshot_provider_api_version": provider.api_version,
            "snapshot_provider_data_version": token["provider_data_version"],
            "snapshot_rule_key": effective_rule["rule_key"],
            "snapshot_rule_version": effective_rule["rule_version"],
            "snapshot_scope_json": _json({"query": scope_query, "resolved": resolved}),
            "snapshot_rule_config_json": _json(effective_rule["config"]),
            "snapshot_voter_hmac_key_version": voter_version,
            "snapshot_idempotency_hmac_key_version": idem_version,
            "snapshot_identity_hmac_key_version": identity_hmac_version,
            "snapshot_reference_encryption_key_version": ref_version,
            "snapshot_identity_encryption_key_version": identity_version,
            "snapshot_signing_key_version": signing_version,
            "snapshot_option_hash": option_hash,
            "eligibility_snapshot_hash": snapshot_hash,
            "eligible_voter_count": count, "frozen_title": frozen_title,
            "snapshot_poll_type_label": poll_type.label,
            **pub_meta,
        }, update_modified=False)
        return {"poll": poll.name, "eligible_voter_count": count, "snapshot_hash": snapshot_hash}
    except Exception:
        frappe.db.rollback()
        frappe.db.set_value("Consensus Poll", poll.name, "snapshot_state", "Failed", update_modified=False)
        raise



def _persisted_snapshot_core(poll) -> dict[str, Any]:
    scope = frappe.parse_json(poll.snapshot_scope_json or "{}")
    if not isinstance(scope, dict) or not isinstance(scope.get("query"), dict) or not isinstance(scope.get("resolved"), list):
        frappe.throw("Frozen poll scope snapshot is malformed.")
    rule_config = frappe.parse_json(poll.snapshot_rule_config_json or "{}")
    option_rows = frappe.get_all(
        "Consensus Poll Option", filters={"poll": poll.name, "enabled": 1},
        fields=["name", "option_key", "frozen_label", "frozen_display_order"],
        order_by="frozen_display_order asc, name asc",
    )
    options = [{
        "name": row.name, "option_key": row.option_key,
        "option_label": row.frozen_label, "display_order": row.frozen_display_order,
    } for row in option_rows]
    option_hash = hashlib.sha256(canonical_json_bytes(options)).hexdigest()
    if option_hash != poll.snapshot_option_hash:
        frappe.throw("Frozen poll option labels or keys failed snapshot verification.")
    voter_rows = frappe.get_all(
        "Consensus Eligibility", filters={"poll": poll.name},
        fields=[
            "voter_ref_digest", "snapshot_hash", "voting_weight", "voter_user",
            "voter_category", "context_ref", "identity_commitment", "identity_schema_key",
            "identity_schema_version", "scope_snapshot_json", "provider_key",
            "provider_api_version", "rule_key", "rule_version",
        ],
        order_by="voter_ref_digest asc",
    )
    commitments = []
    for row in voter_rows:
        row_core = {
            "voter_ref_digest": row.voter_ref_digest,
            "voting_weight": f"{float(row.voting_weight):.3f}",
            "voter_user": row.voter_user, "voter_category": row.voter_category,
            "context_ref": row.context_ref, "identity_commitment": row.identity_commitment,
            "identity_schema_key": row.identity_schema_key,
            "identity_schema_version": row.identity_schema_version,
            "scope_snapshot": frappe.parse_json(row.scope_snapshot_json or "[]"),
            "provider": row.provider_key, "provider_api_version": row.provider_api_version,
            "rule_key": row.rule_key, "rule_version": row.rule_version,
        }
        row_hash = hashlib.sha256(canonical_json_bytes(row_core)).hexdigest()
        if row_hash != row.snapshot_hash:
            frappe.throw("An eligibility row failed frozen snapshot verification.")
        commitments.append((row.voter_ref_digest, row_hash))
    if len(voter_rows) != int(poll.eligible_voter_count or 0):
        frappe.throw("Frozen eligibility row count does not match the poll snapshot.")
    return {
        "poll": poll.name, "site": frappe.local.site,
        "frozen_title": poll.frozen_title,
        "poll_type_label": poll.snapshot_poll_type_label,
        "poll_type": poll.snapshot_poll_type_key,
        "poll_type_configuration_version": poll.snapshot_poll_type_configuration_version,
        "poll_type_config_hash": poll.snapshot_poll_type_config_hash,
        "provider_key": poll.snapshot_provider_key,
        "provider_api_version": poll.snapshot_provider_api_version,
        "provider_data_version": poll.snapshot_provider_data_version,
        "rule_key": poll.snapshot_rule_key, "rule_version": poll.snapshot_rule_version,
        "rule_config": rule_config, "scope_query": scope["query"],
        "resolved_scope": scope["resolved"], "eligibility_rows": commitments,
        "option_hash": option_hash,
        "voter_hmac_key_version": poll.snapshot_voter_hmac_key_version,
        "idempotency_hmac_key_version": poll.snapshot_idempotency_hmac_key_version,
        "identity_hmac_key_version": poll.snapshot_identity_hmac_key_version,
        "reference_encryption_key_version": poll.snapshot_reference_encryption_key_version,
        "identity_encryption_key_version": poll.snapshot_identity_encryption_key_version,
        "signing_key_version": poll.snapshot_signing_key_version,
        "approved_publication_id": poll.approved_publication_id or None,
        "approved_publication_version": int(poll.approved_publication_version) if poll.approved_publication_version else None,
        "approved_electorate_content_hash": poll.approved_electorate_content_hash or None,
    }


def verify_eligibility_snapshot(poll_name: str) -> bool:
    poll = frappe.get_doc("Consensus Poll", poll_name)
    if poll.snapshot_state != "Complete" or not poll.eligibility_snapshot_hash:
        frappe.throw("Eligibility snapshot is not complete.")
    actual = hashlib.sha256(canonical_json_bytes(_persisted_snapshot_core(poll))).hexdigest()
    if actual != poll.eligibility_snapshot_hash:
        frappe.throw("Eligibility snapshot integrity verification failed.")
    return True

def get_frozen_identity(eligibility_name: str) -> dict[str, Any]:
    row = frappe.get_doc("Consensus Eligibility", eligibility_name)
    from consensus.security.crypto import decrypt_protected_value
    plaintext = decrypt_protected_value(
        row.encrypted_identity_snapshot, row.identity_nonce, "identity_snapshot_encryption",
        row.identity_key_version, poll_name=row.poll, provider_key=row.provider_key,
        voter_ref_digest=row.voter_ref_digest,
    )
    try:
        identity = json.loads(plaintext.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise frappe.ValidationError("Frozen receipt identity snapshot is malformed.") from exc
    if identity.get("schema_key") != row.identity_schema_key or identity.get("schema_version") != row.identity_schema_version:
        frappe.throw("Frozen receipt identity schema does not match its eligibility row.")
    actual_commitment = identity_snapshot_commitment(
        row.poll, row.provider_key, row.voter_ref_digest,
        row.identity_schema_key, row.identity_schema_version,
        identity.get("fields", []), row.identity_hmac_key_version,
    )
    if actual_commitment != row.identity_commitment:
        frappe.throw("Frozen receipt identity failed its HMAC commitment check.")
    return identity
