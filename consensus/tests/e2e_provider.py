"""Temporary browser-only provider and secret resolver for stabilization tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import frappe


def _require_test_environment() -> None:
    if not (frappe.conf.get("allow_tests") or frappe.flags.in_test):
        frappe.throw(
            "TestProvider and TestResolver are restricted to test environments with 'allow_tests: true' in site_config.json.",
            frappe.PermissionError,
        )


class TestProvider:
    key = "browser_e2e"
    api_version = "1.0"
    test_only = True

    def __init__(self):
        _require_test_environment()

    def get_metadata(self):
        _require_test_environment()
        return {"label": "Browser E2E Test Provider", "version": "1"}

    def list_scope_types(self, context):
        _require_test_environment()
        return [{"key": "organisation", "label": "Organisation", "hierarchical": True,
                 "multiple_entities": True, "supports_include_exclude": True,
                 "supports_any_selection": True, "supports_all_selection": True,
                 "description": "Test-only organisation scope"}]

    def list_scope_entities(self, scope_type, query=None, parent_ref=None, context=None):
        _require_test_environment()
        if scope_type != "organisation":
            return []
        return [{"entity_ref": "test-org", "label": "Test Organisation",
                 "parent_ref": None, "hierarchy_path": ["Test Organisation"]}]

    def resolve_scope_entities(self, scope_type, entity_refs, context):
        _require_test_environment()
        if scope_type != "organisation":
            return []
        known = {"test-org": {"entity_ref": "test-org", "label": "Test Organisation",
                              "parent_ref": None, "hierarchy_path": ["Test Organisation"]}}
        return [known[ref] for ref in entity_refs if ref in known]

    def validate_scope_query(self, query, poll_context):
        _require_test_environment()
        from consensus.security.crypto import canonical_json_bytes
        return {"query": query, "canonical_query_json": canonical_json_bytes(query).decode(),
                "provider_api_version": self.api_version}

    def validate_rule_configuration(self, rule_key, config, poll_type, poll_context):
        _require_test_environment()
        from consensus.security.crypto import canonical_json_bytes
        if rule_key != "all_members" or not isinstance(config, dict):
            frappe.throw("Invalid browser test rule configuration.")
        return {"rule_key": rule_key, "rule_version": "browser-e2e-rule-v1", "config": config,
                "canonical_config_json": canonical_json_bytes(config).decode()}

    def begin_voter_snapshot(self, poll_context):
        _require_test_environment()
        return {"token": "browser-e2e-snapshot", "provider_data_version": "browser-e2e-data-v1"}

    def get_voter_candidates(self, scope_query, poll_context, snapshot, cursor, page_size):
        _require_test_environment()
        users = ["e2e-voter-one@example.test", "e2e-voter-two@example.test"]
        offset = int(cursor or 0)
        if offset >= len(users):
            return {"candidates": [], "next_cursor": None, "snapshot": snapshot}
        user = users[offset]
        candidate = {"voter_ref": f"MEMBER-{offset + 1:03d}", "frappe_user": user,
                     "voter_category": "member", "context_ref": f"membership-{offset + 1:03d}",
                     "attributes": {}}
        next_cursor = str(offset + 1) if offset + 1 < len(users) else None
        return {"candidates": [candidate], "next_cursor": next_cursor, "snapshot": snapshot}

    def evaluate_eligibility(self, candidate, poll_context, rule_key, rule_config):
        _require_test_environment()
        return {"eligible": True, "reason_code": None, "voting_weight": "1.250",
                "rule_version": "browser-e2e-rule-v1", "voter_category": "member",
                "context_ref": candidate["context_ref"]}

    def get_receipt_identity(self, voter_ref, purpose, schema_version=None):
        _require_test_environment()
        sequence = voter_ref.rsplit("-", 1)[-1]
        return {"schema_key": "browser-e2e-member", "schema_version": "1",
                "fields": [
                    {"key": "member_name", "label": "Voter Name", "value": f"Test Voter {sequence}",
                     "display_order": 1, "required": True, "format_key": "plain"},
                    {"key": "membership_ref", "label": "Membership Reference", "value": voter_ref,
                     "display_order": 2, "required": True, "format_key": "plain"},
                ]}

    def get_contact_destination(self, voter_ref, channel):
        _require_test_environment()
        sequence = voter_ref.rsplit("-", 1)[-1]
        return {"channel": channel, "destination": f"e2e-voter-{['one','two'][int(sequence)-1]}@example.test"}


class TestResolver:
    key = "browser_e2e"
    api_version = "1.0"
    test_only = True

    def __init__(self):
        _require_test_environment()

    def get_active_version(self, reference, purpose):
        _require_test_environment()
        return "browser-e2e-v1"

    def resolve_secret(self, reference, purpose, version):
        _require_test_environment()
        if version != "browser-e2e-v1":
            raise RuntimeError("Unexpected browser test key version")
        if purpose == "pdf_signing":
            if Path("/tmp/consensus-e2e-signing.fail").exists():
                raise RuntimeError("Intentional browser E2E signing failure")
            return Path("/tmp/consensus-e2e/receipt-private-key.pem").read_bytes()
        return hashlib.sha256(("consensus-e2e:" + purpose).encode()).digest()

