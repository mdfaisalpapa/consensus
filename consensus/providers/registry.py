"""Site-aware discovery of trusted providers registered through Frappe hooks."""
from __future__ import annotations

from typing import Any

import frappe

from consensus.providers.protocol import PROVIDER_API_VERSION

PROVIDER_HOOK = "consensus_voter_providers"
REQUIRED_PROVIDER_METHODS = (
    "get_metadata",
    "list_scope_types",
    "list_scope_entities",
    "resolve_scope_entities",
    "validate_scope_query",
    "validate_rule_configuration",
    "begin_voter_snapshot",
    "get_voter_candidates",
    "evaluate_eligibility",
    "get_receipt_identity",
    "get_contact_destination",
)


class ProviderConfigurationError(frappe.ValidationError):
    pass


def _load_registry() -> dict[str, Any]:
    """Load this site's hooks on demand; deliberately avoid a process-global cache."""
    providers: dict[str, Any] = {}
    for import_path in frappe.get_hooks(PROVIDER_HOOK) or []:
        if not isinstance(import_path, str) or not import_path.strip():
            frappe.throw("Consensus provider hooks must contain trusted import paths.")
        provider = frappe.get_attr(import_path)
        if getattr(provider, "test_only", False) and not (
            frappe.conf.get("allow_tests") or frappe.flags.in_test
        ):
            continue
        provider = provider() if isinstance(provider, type) else provider
        if getattr(provider, "test_only", False) and not (
            frappe.conf.get("allow_tests") or frappe.flags.in_test
        ):
            continue
        key = getattr(provider, "key", None)
        version = getattr(provider, "api_version", None)
        if not isinstance(key, str) or not key.strip() or key in providers:
            frappe.throw("Consensus provider registration has a missing or duplicate key.")
        if version != PROVIDER_API_VERSION:
            frappe.throw(
                f"Consensus provider {key} implements API {version!r}; "
                f"required API is {PROVIDER_API_VERSION!r}."
            )
        missing = [name for name in REQUIRED_PROVIDER_METHODS if not callable(getattr(provider, name, None))]
        if missing:
            frappe.throw(f"Consensus provider {key} is missing required methods: {', '.join(missing)}")
        providers[key] = provider
    return providers


def get_registered_providers() -> dict[str, Any]:
    """Return a fresh registry for the current Frappe site context."""
    return _load_registry()


def get_registered_provider(provider_key: str) -> Any:
    """Resolve a provider key already selected by trusted site/poll configuration."""
    provider = get_registered_providers().get(provider_key)
    if not provider:
        frappe.throw(f"Consensus provider {provider_key!r} is not registered for this site.")
    return provider


def get_configured_provider() -> Any:
    """Resolve only the provider selected in this site's Integration Settings."""
    settings = frappe.get_single("Voting Integration Settings")
    if not settings.enabled:
        frappe.throw("Consensus integration is disabled.")
    if not settings.provider_key:
        frappe.throw("No voter provider is configured.")
    provider = get_registered_provider(settings.provider_key)
    if settings.provider_api_version != provider.api_version:
        frappe.throw("The configured voter provider API version no longer matches.")
    return provider
