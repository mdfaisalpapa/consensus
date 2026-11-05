"""Authenticated voter and administrator endpoints."""
import frappe

from consensus.security.permissions import require_eligibility_officer, require_poll_administrator
from consensus.services.eligibility import build_eligibility_snapshot
from consensus.services.receipts import (
    download_my_receipt,
    download_register_csv,
    download_register_pdf,
    get_public_results,
    get_published_register,
    publish_register,
    publish_results,
    verify_receipt_claims,
)
from consensus.services.voting import (
    cast_vote,
    close_poll,
    get_my_vote,
    open_poll,
    send_voting_otp,
    verify_voting_otp,
)


@frappe.whitelist(methods=["POST"])
def build_snapshot(poll_name):
    require_eligibility_officer()
    return build_eligibility_snapshot(poll_name)


@frappe.whitelist(methods=["POST"])
def open(poll_name):
    require_poll_administrator()
    return open_poll(poll_name)


@frappe.whitelist(methods=["POST"])
def close(poll_name):
    require_poll_administrator()
    return close_poll(poll_name)


@frappe.whitelist(methods=["GET"])
def list_my_polls():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication is required.", frappe.PermissionError)
    rows = frappe.get_all(
        "Consensus Eligibility", filters={"voter_user": user, "status": ["in", ["Eligible", "Voted"]]},
        fields=["poll", "status"],
    )
    result = []
    for row in rows:
        poll = frappe.db.get_value("Consensus Poll", row.poll,
                                  ["name", "frozen_title", "status", "starts_at", "ends_at", "require_otp"],
                                  as_dict=True)
        if poll:
            result.append({"poll": row.poll, **poll, "eligibility_status": row.status})
    return result


@frappe.whitelist(methods=["GET"])
def get_poll_options(poll_name):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication is required.", frappe.PermissionError)
    if not frappe.db.exists("Consensus Eligibility", {"poll": poll_name, "voter_user": user}):
        frappe.throw("You are not allocated to this poll.", frappe.PermissionError)
    poll = frappe.get_doc("Consensus Poll", poll_name)
    return {
        "poll": poll.name, "title": poll.frozen_title or poll.title, "status": poll.status,
        "starts_at": poll.starts_at, "ends_at": poll.ends_at, "require_otp": bool(poll.require_otp),
        "options": frappe.get_all(
            "Consensus Poll Option", filters={"poll": poll_name, "enabled": 1},
            fields=["option_key", "frozen_label", "frozen_display_order"],
            order_by="frozen_display_order asc",
        ),
    }


@frappe.whitelist(methods=["POST"])
def send_otp(poll_name):
    return send_voting_otp(poll_name)


@frappe.whitelist(methods=["POST"])
def verify_otp(poll_name, code):
    return verify_voting_otp(poll_name, code)


@frappe.whitelist(methods=["POST"])
def cast(poll_name, option_key, idempotency_key):
    return cast_vote(poll_name, option_key, idempotency_key)


@frappe.whitelist(methods=["POST"])
def recover(poll_name, idempotency_key):
    return get_my_vote(poll_name, idempotency_key)


@frappe.whitelist(methods=["POST"])
def download_receipt(poll_name, idempotency_key):
    return download_my_receipt(poll_name, idempotency_key)


@frappe.whitelist(methods=["POST"])
def publish_ballot_register(poll_name):
    return publish_register(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def public_ballot_register(poll_name):
    return get_published_register(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def verify_public_receipt(poll_name, receipt_code, ballot_hash, option, weight):
    return verify_receipt_claims(poll_name, receipt_code, ballot_hash, option, weight)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download_public_register_csv(poll_name):
    return download_register_csv(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download_public_register_pdf(poll_name):
    return download_register_pdf(poll_name)


@frappe.whitelist(methods=["POST"])
def publish_poll_results(poll_name):
    return publish_results(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def public_poll_results(poll_name):
    return get_public_results(poll_name)
