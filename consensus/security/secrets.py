"""Trusted, configurable secret resolution interface and hook registry."""
from __future__ import annotations

from typing import Any, Protocol

import frappe

SECRET_RESOLVER_API_VERSION = "1.0"
SECRET_RESOLVER_HOOK = "consensus_secret_resolvers"


class SecretResolver(Protocol):
    key: str
    api_version: str

    def get_active_version(self, reference: str, purpose: str) -> str: ...

    def resolve_secret(self, reference: str, purpose: str, version: str) -> bytes: ...


class SecretResolutionError(frappe.ValidationError):
    pass


def get_registered_secret_resolvers() -> dict[str, Any]:
    """Resolve trusted resolver classes from current-site Frappe hooks, uncached."""
    resolvers: dict[str, Any] = {}
    for import_path in frappe.get_hooks(SECRET_RESOLVER_HOOK) or []:
        if not isinstance(import_path, str) or not import_path.strip():
            frappe.throw("Consensus secret-resolver hooks must contain trusted import paths.")
        resolver = frappe.get_attr(import_path)
        if getattr(resolver, "test_only", False) and not (
            frappe.conf.get("allow_tests") or frappe.flags.in_test
        ):
            continue
        resolver = resolver() if isinstance(resolver, type) else resolver
        if getattr(resolver, "test_only", False) and not (
            frappe.conf.get("allow_tests") or frappe.flags.in_test
        ):
            continue
        key = getattr(resolver, "key", None)
        if not isinstance(key, str) or not key.strip() or key in resolvers:
            frappe.throw("Consensus secret resolver has a missing or duplicate key.")
        if getattr(resolver, "api_version", None) != SECRET_RESOLVER_API_VERSION:
            frappe.throw(f"Consensus secret resolver {key} has an unsupported API version.")
        for method in ("get_active_version", "resolve_secret"):
            if not callable(getattr(resolver, method, None)):
                frappe.throw(f"Consensus secret resolver {key} is missing {method}().")
        resolvers[key] = resolver
    return resolvers


def get_secret_resolver() -> Any:
    settings = frappe.get_single("Voting Integration Settings")
    resolver_key = settings.secret_resolver_key
    resolver = get_registered_secret_resolvers().get(resolver_key)
    if not resolver:
        raise SecretResolutionError("Configured Consensus secret resolver is unavailable.")
    return resolver


def resolve_secret(purpose: str, version: str | None = None) -> tuple[bytes, str]:
    """Resolve a purpose-bound secret and its version from external storage."""
    settings = frappe.get_single("Voting Integration Settings")
    references = {
        "pdf_signing": settings.signing_key_reference,
        "provider_reference_encryption": settings.provider_reference_encryption_key_reference,
        "identity_snapshot_encryption": settings.identity_snapshot_encryption_key_reference,
        "voter_reference_hmac": settings.voter_reference_hmac_key_reference,
        "idempotency_hmac": settings.idempotency_hmac_key_reference,
        "identity_snapshot_commitment_hmac": settings.identity_snapshot_hmac_key_reference,
        "otp_hmac": settings.otp_hmac_key_reference,
    }
    reference = references.get(purpose)
    if not reference:
        raise SecretResolutionError(f"No secret reference is configured for {purpose}.")
    resolver = get_secret_resolver()
    selected_version = version or resolver.get_active_version(reference, purpose)
    if not selected_version:
        raise SecretResolutionError(f"No active key version is available for {purpose}.")
    secret = resolver.resolve_secret(reference, purpose, selected_version)
    if not isinstance(secret, bytes) or not secret:
        raise SecretResolutionError(f"Secret resolver returned no bytes for {purpose}.")
    return secret, selected_version
