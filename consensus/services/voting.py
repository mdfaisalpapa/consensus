"""Poll lifecycle, OTP authorization, atomic casting and voter-bound recovery."""
from __future__ import annotations

import hashlib
import json
import secrets

import frappe
from frappe.utils import get_datetime, now_datetime

from consensus.security.crypto import decrypt_protected_value, idempotency_digest, otp_digest
from consensus.security.permissions import require_poll_administrator
from consensus.security.secrets import resolve_secret
from consensus.services.hashes import ballot_hash

OTP_TTL = 300
OTP_COOLDOWN = 60
OTP_MAX_ATTEMPTS = 5
OTP_AUTH_TTL = 600


def _user():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication is required.", frappe.PermissionError)
    return user


def _otp_cache_key(kind, poll, user, voter_digest):
    raw = "\0".join((kind, poll, user, voter_digest)).encode()
    return "consensus:otp:" + hashlib.sha256(raw).hexdigest()


def _locked_row(doctype: str, name: str):
    """Use Frappe's database abstraction for MariaDB/PostgreSQL row locking."""
    return frappe.db.get_value(doctype, name, "name", for_update=True)


def _assert_poll_open(poll):
    now = get_datetime(now_datetime())
    if poll.status != "Open" or now < get_datetime(poll.starts_at) or now > get_datetime(poll.ends_at):
        frappe.throw("This poll is not currently open for voting.")


def _eligible(poll_name, user, lock=False):
    filters = {"poll": poll_name, "voter_user": user, "status": ["in", ["Eligible", "Voted"]]}
    rows = frappe.get_all("Consensus Eligibility", filters=filters, fields=["name"], limit=2)
    if len(rows) != 1:
        frappe.throw("Your account is not allocated to vote in this poll.", frappe.PermissionError)
    if lock:
        _locked_row("Consensus Eligibility", rows[0].name)
    return frappe.get_doc("Consensus Eligibility", rows[0].name)


def _assert_otp(poll, user, eligibility):
    if not poll.require_otp:
        return
    auth_key = _otp_cache_key("verified", poll.name, user, eligibility.voter_ref_digest)
    if not frappe.cache().get_value(auth_key):
        frappe.throw("Complete OTP verification before voting.", frappe.PermissionError)


def send_voting_otp(poll_name: str):
    user = _user()
    poll = frappe.get_doc("Consensus Poll", poll_name)
    _assert_poll_open(poll)
    eligibility = _eligible(poll.name, user)
    if eligibility.status != "Eligible":
        frappe.throw("A vote has already been recorded for this voter.")
    if not poll.require_otp:
        return {"success": True, "otp_required": False}
    settings = frappe.get_single("Voting Integration Settings")
    if not settings.otp_enabled:
        frappe.throw("OTP delivery is disabled in Consensus settings.")
    provider = __import__("consensus.providers.registry", fromlist=["get_registered_provider"]).get_registered_provider(
        poll.snapshot_provider_key
    )
    voter_ref = decrypt_protected_value(
        eligibility.encrypted_voter_reference, eligibility.reference_nonce,
        "provider_reference_encryption", eligibility.reference_key_version,
        poll_name=poll.name, provider_key=eligibility.provider_key,
        voter_ref_digest=eligibility.voter_ref_digest,
    ).decode("utf-8")
    if provider.api_version != eligibility.provider_api_version:
        frappe.throw("The poll's voter provider API version is unavailable.")
    destination = provider.get_contact_destination(voter_ref, "email")
    email = destination.get("destination") if isinstance(destination, dict) else None
    if not email:
        frappe.throw("The voter provider has no verified email delivery destination.")
    cache = frappe.cache()
    cooldown = _otp_cache_key("cooldown", poll.name, user, eligibility.voter_ref_digest)
    if cache.get_value(cooldown):
        frappe.throw("Wait before requesting another OTP.")
    sends_key = _otp_cache_key("sends", poll.name, user, eligibility.voter_ref_digest)
    sends = int(cache.get_value(sends_key) or 0)
    if sends >= 5:
        frappe.throw("The OTP send limit was reached. Try again later.")
    cache.set_value(sends_key, sends + 1, expires_in_sec=900)
    code = f"{secrets.randbelow(1_000_000):06d}"
    _, key_version = resolve_secret("otp_hmac")
    digest = otp_digest(poll.name, user, eligibility.voter_ref_digest, code, key_version)
    cache.set_value(_otp_cache_key("pending", poll.name, user, eligibility.voter_ref_digest),
                    json.dumps({"digest": digest, "version": key_version, "attempts": 0}), expires_in_sec=OTP_TTL)
    cache.set_value(cooldown, "1", expires_in_sec=OTP_COOLDOWN)
    frappe.sendmail(recipients=[email], subject="Your voting verification code",
                    message=f"Your Consensus verification code is {code}. It expires in five minutes.")
    return {"success": True, "otp_required": True}


def verify_voting_otp(poll_name: str, code: str):
    user = _user()
    poll = frappe.get_doc("Consensus Poll", poll_name)
    _assert_poll_open(poll)
    eligibility = _eligible(poll.name, user)
    key = _otp_cache_key("pending", poll.name, user, eligibility.voter_ref_digest)
    cache = frappe.cache()
    raw = cache.get_value(key)
    if not raw:
        frappe.throw("The OTP has expired or was not requested.")
    pending = json.loads(raw)
    if int(pending.get("attempts", 0)) >= OTP_MAX_ATTEMPTS:
        cache.delete_value(key)
        frappe.throw("Maximum OTP attempts exceeded.")
    actual = otp_digest(poll.name, user, eligibility.voter_ref_digest, str(code), pending["version"])
    if not secrets.compare_digest(actual, pending["digest"]):
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        cache.set_value(key, json.dumps(pending), expires_in_sec=OTP_TTL)
        frappe.throw("The OTP is invalid.")
    cache.delete_value(key)
    cache.set_value(_otp_cache_key("verified", poll.name, user, eligibility.voter_ref_digest),
                    "1", expires_in_sec=OTP_AUTH_TTL)
    return {"success": True}


def open_poll(poll_name: str):
    require_poll_administrator()
    _locked_row("Consensus Poll", poll_name)
    poll = frappe.get_doc("Consensus Poll", poll_name)
    if poll.status != "Draft" or poll.snapshot_state != "Complete":
        frappe.throw("Only a draft poll with a completed eligibility snapshot can open.")
    from consensus.services.eligibility import verify_eligibility_snapshot
    verify_eligibility_snapshot(poll.name)
    if get_datetime(poll.starts_at) > get_datetime(now_datetime()):
        poll.status = "Scheduled"
    else:
        poll.status = "Open"
    poll.save(ignore_permissions=True)
    return {"poll": poll.name, "status": poll.status}


def close_poll(poll_name: str):
    require_poll_administrator()
    _locked_row("Consensus Poll", poll_name)
    poll = frappe.get_doc("Consensus Poll", poll_name)
    if poll.status not in ("Open", "Scheduled"):
        frappe.throw("Only an open or scheduled poll can be closed.")
    poll.status = "Closed"
    poll.save(ignore_permissions=True)
    return {"poll": poll.name, "status": poll.status}


def cast_vote(poll_name: str, option_name: str, idempotency_key: str):
    """Insert participation and anonymous ballot in one Frappe request transaction."""
    user = _user()
    poll = frappe.get_doc("Consensus Poll", poll_name)
    _locked_row("Consensus Poll", poll.name)
    poll.reload()
    eligibility = _eligible(poll.name, user, lock=True)
    key_version = poll.snapshot_idempotency_hmac_key_version
    idem = idempotency_digest(poll.name, eligibility.voter_ref_digest, idempotency_key, key_version)
    existing_participation = frappe.db.get_value(
        "Consensus Participation", {"poll": poll.name, "voter_ref_digest": eligibility.voter_ref_digest},
        ["name", "voter_user"], as_dict=True,
    )
    if existing_participation:
        if existing_participation.voter_user != user:
            frappe.throw("Participation is already recorded for this eligibility.", frappe.PermissionError)
        ballot = frappe.db.get_value(
            "Consensus Ballot",
            {"poll": poll.name, "idempotency_hmac_key_version": key_version, "idempotency_digest": idem},
            ["receipt_code", "selected_option_label", "voting_weight", "cast_on", "ballot_hash"],
            as_dict=True,
        )
        if not ballot:
            frappe.throw("The vote exists, but this authenticated request cannot recover its receipt.")
        expected_option = frappe.db.get_value(
            "Consensus Poll Option", {"poll": poll.name, "option_key": option_name}, "frozen_label"
        )
        if not expected_option or ballot.selected_option_label != expected_option:
            frappe.throw("This retry does not match the original vote.")
        return {"success": True, "recovered": True, "poll": poll.name, **ballot}
    _assert_poll_open(poll)
    if poll.snapshot_state != "Complete" or eligibility.status != "Eligible":
        frappe.throw("This voter is not eligible to participate.")
    option = frappe.db.get_value(
        "Consensus Poll Option",
        {"option_key": option_name, "poll": poll.name, "enabled": 1},
        ["name", "option_key", "frozen_label"],
        as_dict=True,
    )
    if not option or not option.frozen_label:
        frappe.throw("Select an enabled option belonging to this poll.")
    _assert_otp(poll, user, eligibility)
    if frappe.db.exists("Consensus Ballot", {
        "poll": poll.name, "idempotency_hmac_key_version": key_version, "idempotency_digest": idem
    }):
        frappe.throw("This idempotency key has already been used.")
    cast_on = now_datetime()
    receipt_code = secrets.token_urlsafe(24)
    weight = float(eligibility.voting_weight)
    ballot_hash_value = ballot_hash(poll.name, receipt_code, option.name, option.frozen_label, weight, cast_on)
    frappe.get_doc({
        "doctype": "Consensus Participation", "poll": poll.name,
        "eligibility": eligibility.name, "voter_ref_digest": eligibility.voter_ref_digest,
        "voter_user": user, "participated_on": cast_on,
        "authorization_method": "Frappe User + OTP" if poll.require_otp else "Frappe User",
    }).insert(ignore_permissions=True)
    ballot_doc = frappe.get_doc({
        "doctype": "Consensus Ballot", "poll": poll.name, "receipt_code": receipt_code,
        "selected_option": option.name, "selected_option_key": option.option_key,
        "selected_option_label": option.frozen_label, "voting_weight": weight,
        "cast_on": cast_on, "ballot_hash": ballot_hash_value, "idempotency_digest": idem,
        "idempotency_hmac_key_version": key_version, "hash_format_version": "society-erp-v1",
    }).insert(ignore_permissions=True)
    # Frappe forces session user into owner fields; replace with the service account
    # within this same transaction to avoid a direct participation-to-ballot metadata link.
    frappe.db.set_value(
        "Consensus Ballot", ballot_doc.name,
        {"owner": "Administrator", "modified_by": "Administrator"}, update_modified=False,
    )
    frappe.db.set_value("Consensus Eligibility", eligibility.name, "status", "Voted", update_modified=False)
    if poll.require_otp:
        frappe.db.after_commit.add(
            lambda: frappe.cache().delete_value(
                _otp_cache_key("verified", poll.name, user, eligibility.voter_ref_digest)
            )
        )
    return {
        "success": True, "recovered": False, "poll": poll.name,
        "receipt_code": receipt_code, "voting_weight": weight,
        "cast_on": cast_on, "ballot_hash": ballot_hash_value,
    }


def get_my_vote(poll_name: str, idempotency_key: str):
    """Authenticated receipt recovery; eligibility is re-established independently."""
    user = _user()
    poll = frappe.get_doc("Consensus Poll", poll_name)
    eligibility = _eligible(poll.name, user)
    digest = idempotency_digest(
        poll.name, eligibility.voter_ref_digest, idempotency_key,
        poll.snapshot_idempotency_hmac_key_version,
    )
    participation = frappe.db.get_value(
        "Consensus Participation",
        {"poll": poll.name, "voter_ref_digest": eligibility.voter_ref_digest, "voter_user": user},
        "name",
    )
    if not participation:
        frappe.throw("No participation is recorded for this authenticated voter.")
    ballot = frappe.db.get_value(
        "Consensus Ballot",
        {"poll": poll.name, "idempotency_hmac_key_version": poll.snapshot_idempotency_hmac_key_version,
         "idempotency_digest": digest},
        ["receipt_code", "selected_option_label", "voting_weight", "cast_on", "ballot_hash"],
        as_dict=True,
    )
    if not ballot:
        frappe.throw("No receipt can be recovered using this voter's idempotency key.")
    return {"success": True, "poll": poll.name, **ballot}


def close_expired_polls():
    """Scheduler task: transition scheduled polls and close expired polls."""
    now = now_datetime()
    for row in frappe.get_all("Consensus Poll", filters={"status": "Scheduled", "starts_at": ["<=", now]}, fields=["name"]):
        frappe.db.set_value("Consensus Poll", row.name, "status", "Open", update_modified=False)
    for row in frappe.get_all("Consensus Poll", filters={"status": "Open", "ends_at": ["<", now]}, fields=["name"]):
        frappe.db.set_value("Consensus Poll", row.name, "status", "Closed", update_modified=False)
