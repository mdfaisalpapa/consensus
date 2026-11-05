"""Domain-separated HMACs and AES-256-GCM protection for identity-side data."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from typing import Any

import frappe
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from consensus.security.secrets import SecretResolutionError, resolve_secret

CANONICAL_JSON_KWARGS = {
    "sort_keys": True,
    "ensure_ascii": False,
    "separators": (",", ":"),
}


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, **CANONICAL_JSON_KWARGS).encode("utf-8")
    except (TypeError, ValueError) as exc:
        frappe.throw(f"Value is not supported by Consensus canonical JSON: {exc}")


def _site() -> str:
    site = getattr(frappe.local, "site", None)
    if not site:
        raise SecretResolutionError("Consensus cryptographic operation requires a site context.")
    return str(site)


def _hmac(purpose: str, version: str, value: dict[str, Any]) -> str:
    key, _ = resolve_secret(purpose, version)
    if len(key) < 32:
        raise SecretResolutionError(f"Configured {purpose} HMAC key must be at least 256 bits.")
    versioned_value = {**value, "hmac_key_version": version}
    return hmac.new(key, canonical_json_bytes(versioned_value), hashlib.sha256).hexdigest()


def voter_reference_digest(provider_key: str, poll_name: str, voter_ref: str, key_version: str) -> str:
    """Poll/site/provider-scoped stable voter digest; never use plain SHA-256."""
    if not all(isinstance(item, str) and item for item in (provider_key, poll_name, voter_ref)):
        frappe.throw("Provider, poll and stable voter reference are required.")
    return _hmac("voter_reference_hmac", key_version, {
        "domain": "consensus:voter-ref:v1",
        "site": _site(),
        "provider": provider_key,
        "poll": poll_name,
        "voter_ref": voter_ref,
    })


def idempotency_digest(
    poll_name: str, voter_ref_digest: str, raw_client_key: str, key_version: str
) -> str:
    if (
        not isinstance(poll_name, str) or not poll_name
        or not isinstance(voter_ref_digest, str) or not voter_ref_digest
        or not isinstance(raw_client_key, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", raw_client_key)
    ):
        frappe.throw("Poll, voter digest and a valid high-entropy client key are required.")
    return _hmac("idempotency_hmac", key_version, {
        "domain": "consensus:idempotency:v1",
        "site": _site(),
        "poll": poll_name,
        "voter_ref_digest": voter_ref_digest,
        "client_key": raw_client_key,
    })


def identity_snapshot_commitment(
    poll_name: str,
    provider_key: str,
    voter_ref_digest: str,
    schema_key: str,
    schema_version: str,
    fields: list[dict[str, Any]],
    key_version: str,
) -> str:
    canonical_fields = sorted(fields, key=lambda item: (item.get("display_order", 0), item.get("key", "")))
    return _hmac("identity_snapshot_commitment_hmac", key_version, {
        "domain": "consensus:receipt-identity:v1",
        "site": _site(),
        "poll": poll_name,
        "provider": provider_key,
        "voter_ref_digest": voter_ref_digest,
        "schema_key": schema_key,
        "schema_version": schema_version,
        "fields": canonical_fields,
    })


def encrypt_protected_value(
    plaintext: bytes,
    purpose: str,
    key_version: str,
    *,
    poll_name: str,
    provider_key: str,
    voter_ref_digest: str,
) -> dict[str, str]:
    if not isinstance(plaintext, bytes):
        frappe.throw("Protected Consensus values must be encoded as bytes.")
    key, _ = resolve_secret(purpose, key_version)
    if len(key) != 32:
        raise SecretResolutionError(f"Configured {purpose} AES-GCM key must be exactly 256 bits.")
    nonce = secrets.token_bytes(12)
    associated_data = canonical_json_bytes({
        "domain": "consensus:protected-identity:v1",
        "site": _site(),
        "poll": poll_name,
        "provider": provider_key,
        "voter_ref_digest": voter_ref_digest,
        "purpose": purpose,
        "key_version": key_version,
    })
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return {
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "key_version": key_version,
    }


def decrypt_protected_value(
    ciphertext: str,
    nonce: str,
    purpose: str,
    key_version: str,
    *,
    poll_name: str,
    provider_key: str,
    voter_ref_digest: str,
) -> bytes:
    key, _ = resolve_secret(purpose, key_version)
    if len(key) != 32:
        raise SecretResolutionError(f"Configured {purpose} AES-GCM key must be exactly 256 bits.")
    associated_data = canonical_json_bytes({
        "domain": "consensus:protected-identity:v1",
        "site": _site(),
        "poll": poll_name,
        "provider": provider_key,
        "voter_ref_digest": voter_ref_digest,
        "purpose": purpose,
        "key_version": key_version,
    })
    try:
        decoded_ciphertext = base64.b64decode(ciphertext.encode("ascii"), altchars=b"-_", validate=True)
        decoded_nonce = base64.b64decode(nonce.encode("ascii"), altchars=b"-_", validate=True)
        if len(decoded_nonce) != 12:
            raise ValueError("Invalid AES-GCM nonce length")
        return AESGCM(key).decrypt(decoded_nonce, decoded_ciphertext, associated_data)
    except (InvalidTag, ValueError, UnicodeError) as exc:
        raise SecretResolutionError("Protected Consensus identity data failed authentication.") from exc


def otp_digest(poll_name: str, user: str, voter_ref_digest: str, code: str, key_version: str) -> str:
    if not all(isinstance(value, str) and value for value in (poll_name, user, voter_ref_digest, code)):
        frappe.throw("OTP digest requires poll, user, voter digest and code.")
    return _hmac("otp_hmac", key_version, {
        "domain": "consensus:otp:v1", "site": _site(),
        "poll": poll_name, "user": user, "voter_ref_digest": voter_ref_digest, "otp": code,
    })
