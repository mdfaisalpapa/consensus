"""Temporary setup for browser stabilization tests on erptest.local."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import frappe
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

TEST_PASSWORD = "Consensus-E2E-Only-2026"
ADMIN = "e2e-admin@example.test"
VOTERS = ["e2e-voter-one@example.test", "e2e-voter-two@example.test"]


def _user(email, first_name, user_type, roles=()):
    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
    else:
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": first_name,
                               "user_type": user_type, "enabled": 1, "send_welcome_email": 0})
        user.insert(ignore_permissions=True)
    user.first_name = first_name
    user.user_type = user_type
    user.enabled = 1
    user.send_welcome_email = 0
    user.set("roles", [])
    for role in roles:
        user.append("roles", {"role": role})
    user.save(ignore_permissions=True)
    from frappe.utils.password import update_password
    update_password(email, TEST_PASSWORD)
    return email


def _require_test_environment() -> None:
    if not (frappe.conf.get("allow_tests") or frappe.flags.in_test):
        frappe.throw(
            "e2e_setup is restricted to test environments with 'allow_tests: true' in site_config.json.",
            frappe.PermissionError,
        )


def setup():
    _require_test_environment()
    Path("/tmp/consensus-e2e").mkdir(exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Consensus Browser E2E")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                   .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=7))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .sign(private_key, hashes.SHA256()))
    Path("/tmp/consensus-e2e/receipt-private-key.pem").write_bytes(
        private_key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    Path("/tmp/consensus-e2e/receipt-private-key.pem").chmod(0o600)
    cert_dir = Path(frappe.get_site_path("private", "files"))
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_file = cert_dir / "consensus-browser-e2e-cert.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    cert_url = "/private/files/consensus-browser-e2e-cert.pem"
    if not frappe.db.exists("File", {"file_url": cert_url}):
        frappe.get_doc({"doctype": "File", "file_name": cert_file.name,
                        "file_url": cert_url, "is_private": 1}).insert(ignore_permissions=True)

    _user(ADMIN, "Consensus E2E Administrator", "System User", ["System Manager"])
    for index, email in enumerate(VOTERS, 1):
        _user(email, f"Test Voter {index}", "System User", ["Desk User"])

    settings = frappe.get_single("Voting Integration Settings")
    settings.enabled = 1
    settings.provider_key = "browser_e2e"
    settings.provider_api_version = "1.0"
    settings.organisation_label = "Consensus Development E2E"
    settings.voter_label = "Member"
    settings.scope_label = "Organisation"
    settings.secret_resolver_key = "browser_e2e"
    settings.signing_key_reference = "browser-e2e/signing"
    settings.provider_reference_encryption_key_reference = "browser-e2e/provider-ref"
    settings.identity_snapshot_encryption_key_reference = "browser-e2e/identity"
    settings.voter_reference_hmac_key_reference = "browser-e2e/voter-hmac"
    settings.idempotency_hmac_key_reference = "browser-e2e/idempotency"
    settings.identity_snapshot_hmac_key_reference = "browser-e2e/identity-commitment"
    settings.otp_hmac_key_reference = "browser-e2e/otp"
    settings.signing_certificate = cert_url
    settings.signer_display_name = "Consensus E2E Test Signer"
    settings.signature_reason = "Development browser stabilization test"
    settings.otp_enabled = 1
    settings.save(ignore_permissions=True)
    frappe.db.commit()
    return {"admin": ADMIN, "voters": VOTERS, "password": TEST_PASSWORD,
            "certificate_url": cert_url}


def extract_latest_otp():
    _require_test_environment()
    import re
    rows = frappe.db.sql(
        "SELECT name, message FROM `tabEmail Queue` WHERE message LIKE %s ORDER BY creation DESC LIMIT 20",
        ("%verification code is%",), as_dict=True,
    )
    for row in rows:
        match = re.search(r"verification code is (\d{6})", row.message or "")
        if match:
            Path("/tmp/consensus-e2e/latest-otp.txt").write_text(match.group(1))
            return {"queued": True, "otp_found": True}
    return {"queued": bool(rows), "otp_found": False}
