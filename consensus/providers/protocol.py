"""Versioned contracts for trusted organisation voter providers."""
from __future__ import annotations

from typing import Any, Protocol, TypedDict

PROVIDER_API_VERSION = "1.0"


class ScopeType(TypedDict):
    key: str
    label: str
    hierarchical: bool
    multiple_entities: bool
    supports_include_exclude: bool
    supports_any_selection: bool
    supports_all_selection: bool
    description: str


class ScopeEntity(TypedDict):
    entity_ref: str
    label: str
    parent_ref: str | None
    hierarchy_path: list[str]


class ScopeSelection(TypedDict):
    scope_type: str
    entity_ref: str
    inclusion: str
    parent_ref: str | None


class ScopeQuery(TypedDict):
    selections: list[ScopeSelection]
    selection_operator: str
    conditions: dict[str, Any]
    conditions_operator: str


class PollContext(TypedDict):
    poll_type: str
    poll_name: str | None
    starts_at: str | None
    ends_at: str | None
    rule_key: str | None


class ProviderSnapshotToken(TypedDict, total=False):
    token: str
    provider_data_version: str
    approved_publication_id: str | None
    approved_publication_version: int | None
    approved_electorate_content_hash: str | None


class VoterCandidate(TypedDict):
    voter_ref: str
    frappe_user: str | None
    voter_category: str | None
    context_ref: str | None
    attributes: dict[str, Any]


class CandidatePage(TypedDict):
    candidates: list[VoterCandidate]
    next_cursor: str | None
    snapshot: ProviderSnapshotToken


class CanonicalScopeQuery(TypedDict):
    query: ScopeQuery
    canonical_query_json: str
    provider_api_version: str


class CanonicalRuleConfig(TypedDict):
    rule_key: str
    rule_version: str
    config: dict[str, Any]
    canonical_config_json: str


class EligibilityDecision(TypedDict):
    eligible: bool
    reason_code: str | None
    voting_weight: str
    rule_version: str
    voter_category: str | None
    context_ref: str | None


class ReceiptIdentityField(TypedDict):
    key: str
    label: str
    value: str
    display_order: int
    required: bool
    format_key: str | None


class ReceiptIdentity(TypedDict):
    schema_key: str
    schema_version: str
    fields: list[ReceiptIdentityField]


class ContactDestination(TypedDict):
    channel: str
    destination: str


class VoterProviderV1(Protocol):
    key: str
    api_version: str

    def get_metadata(self) -> dict[str, Any]: ...

    def list_scope_types(self, context: dict[str, Any]) -> list[ScopeType]: ...

    def list_scope_entities(
        self,
        scope_type: str,
        query: str | None = None,
        parent_ref: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[ScopeEntity]: ...

    def resolve_scope_entities(
        self, scope_type: str, entity_refs: list[str], context: dict[str, Any]
    ) -> list[ScopeEntity]: ...

    def validate_scope_query(
        self, query: ScopeQuery, poll_context: PollContext
    ) -> CanonicalScopeQuery: ...

    def validate_rule_configuration(
        self,
        rule_key: str,
        config: dict[str, Any],
        poll_type: str,
        poll_context: PollContext,
    ) -> CanonicalRuleConfig: ...

    def begin_voter_snapshot(self, poll_context: PollContext) -> ProviderSnapshotToken: ...

    def get_voter_candidates(
        self,
        scope_query: ScopeQuery,
        poll_context: PollContext,
        snapshot: ProviderSnapshotToken,
        cursor: str | None,
        page_size: int,
    ) -> CandidatePage: ...

    def evaluate_eligibility(
        self,
        candidate: VoterCandidate,
        poll_context: PollContext,
        rule_key: str,
        rule_config: dict[str, Any],
    ) -> EligibilityDecision: ...

    def get_receipt_identity(
        self, voter_ref: str, purpose: str, schema_version: str | None = None
    ) -> ReceiptIdentity: ...

    def get_contact_destination(self, voter_ref: str, channel: str) -> ContactDestination: ...

    def assert_voting_enabled(self, poll_context: PollContext) -> None: ...
