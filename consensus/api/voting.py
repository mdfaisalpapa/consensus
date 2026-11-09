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


@frappe.whitelist(methods=["GET", "POST"])
def list_my_polls():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication is required.", frappe.PermissionError)
    rows = frappe.get_all(
        "Consensus Eligibility", filters={"voter_user": user, "status": ["in", ["Eligible", "Voted"]]},
        fields=["name", "poll", "status", "context_ref", "voting_weight"],
    )
    result = []
    for row in rows:
        poll = frappe.db.get_value(
            "Consensus Poll", row.poll,
            [
                "name", "title", "frozen_title", "status", "starts_at", "ends_at", "require_otp",
                "approved_publication_id", "register_published", "results_published",
                "ballots_cast_count", "eligible_voter_count", "turnout_percentage", "description"
            ],
            as_dict=True,
        )
        if poll:
            cast_count = frappe.db.count("Consensus Participation", {"poll": row.poll})
            eligible = poll.get("eligible_voter_count") or frappe.db.count("Consensus Eligibility", {"poll": row.poll}) or 0
            pct = round((cast_count / eligible * 100.0), 2) if eligible else 0.0
            if poll.get("ballots_cast_count") != cast_count or poll.get("turnout_percentage") != pct:
                frappe.db.set_value(
                    "Consensus Poll", row.poll,
                    {"ballots_cast_count": cast_count, "turnout_percentage": pct},
                    update_modified=False,
                )
            poll["ballots_cast_count"] = cast_count
            poll["turnout_percentage"] = pct

            if not poll.get("approved_publication_id"):
                scope_pub = frappe.db.get_value(
                    "Consensus Poll Scope",
                    {"parent": row.poll, "scope_type": "publication"},
                    "entity_ref",
                )
                if scope_pub:
                    poll["approved_publication_id"] = scope_pub
            poll_info = dict(poll)
            poll_info.pop("name", None)
            result.append({
                "name": row.name,
                "eligibility_name": row.name,
                "poll": row.poll,
                "context_ref": row.context_ref,
                "voting_weight": row.voting_weight,
                **poll_info,
                "eligibility_status": row.status,
            })
    return result


@frappe.whitelist(methods=["GET", "POST"])
def get_poll_options(poll_name):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Authentication is required.", frappe.PermissionError)
    eligibility = frappe.db.get_value(
        "Consensus Eligibility",
        {"poll": poll_name, "voter_user": user},
        ["name", "status", "context_ref", "voting_weight"],
        as_dict=True,
    )
    poll = frappe.get_doc("Consensus Poll", poll_name)
    user_roles = frappe.get_roles(user)
    is_admin = bool({"System Manager", "Consensus Administrator"} & set(user_roles))

    if not eligibility and not is_admin and poll.status != "Closed":
        frappe.throw("You are not allocated to this poll.", frappe.PermissionError)

    pub_id = poll.approved_publication_id
    if not pub_id and poll.scope_selections:
        for scope in poll.scope_selections:
            if scope.scope_type == "publication" and scope.entity_ref:
                pub_id = scope.entity_ref
                break

    pub_info = None
    if pub_id and frappe.db.exists("Electorate Publication", pub_id):
        pub_info = frappe.db.get_value(
            "Electorate Publication", pub_id,
            ["name", "title", "status", "electorate_content_hash", "published_on", "approval_certified_on"],
            as_dict=True,
        )

    options = frappe.get_all(
        "Consensus Poll Option", filters={"poll": poll_name, "enabled": 1},
        fields=["option_key", "option_label", "frozen_label", "frozen_display_order", "description"],
        order_by="frozen_display_order asc",
    )

    cast_count = frappe.db.count("Consensus Participation", {"poll": poll.name})
    eligible = poll.eligible_voter_count or frappe.db.count("Consensus Eligibility", {"poll": poll.name}) or 0
    pct = round((cast_count / eligible * 100.0), 2) if eligible else 0.0
    if poll.ballots_cast_count != cast_count or poll.turnout_percentage != pct:
        frappe.db.set_value(
            "Consensus Poll", poll.name,
            {"ballots_cast_count": cast_count, "turnout_percentage": pct},
            update_modified=False,
        )
        poll.ballots_cast_count = cast_count
        poll.turnout_percentage = pct

    return {
        "poll": poll.name,
        "title": poll.frozen_title or poll.title,
        "description": poll.description or "",
        "status": poll.status,
        "starts_at": poll.starts_at,
        "ends_at": poll.ends_at,
        "require_otp": bool(poll.require_otp),
        "approved_publication_id": pub_id,
        "publication_info": pub_info,
        "register_published": bool(poll.register_published),
        "register_hash": poll.register_hash if poll.register_published else None,
        "results_published": bool(poll.results_published),
        "ballots_cast_count": cast_count,
        "eligible_voter_count": eligible,
        "turnout_percentage": pct,
        "my_eligibility": eligibility,
        "options": options,
    }


@frappe.whitelist(methods=["POST"])
def send_otp(poll_name, eligibility_name=None):
    return send_voting_otp(poll_name, eligibility_name=eligibility_name)


@frappe.whitelist(methods=["POST"])
def verify_otp(poll_name, code, eligibility_name=None):
    return verify_voting_otp(poll_name, code, eligibility_name=eligibility_name)


@frappe.whitelist(methods=["POST"])
def cast(poll_name, option_key, idempotency_key, eligibility_name=None):
    return cast_vote(poll_name, option_key, idempotency_key, eligibility_name=eligibility_name)


@frappe.whitelist(methods=["POST"])
def recover(poll_name, idempotency_key, eligibility_name=None):
    try:
        return get_my_vote(poll_name, idempotency_key, eligibility_name=eligibility_name)
    except frappe.PermissionError:
        return {"success": False, "recovered": False, "reason": "Your account is not allocated to vote in this poll."}


@frappe.whitelist(methods=["POST"])
def download_receipt(poll_name, idempotency_key, eligibility_name=None):
    return download_my_receipt(poll_name, idempotency_key, eligibility_name=eligibility_name)


@frappe.whitelist(methods=["POST"])
def publish_ballot_register(poll_name):
    return publish_register(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def public_ballot_register(poll_name):
    return get_published_register(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def verify_public_receipt(poll_name, receipt_code, ballot_hash, option, weight):
    return verify_receipt_claims(poll_name, receipt_code, ballot_hash, option, weight)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def download_public_register_csv(poll_name):
    return download_register_csv(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def download_public_register_pdf(poll_name):
    return download_register_pdf(poll_name)


@frappe.whitelist(methods=["POST"])
def publish_poll_results(poll_name):
    return publish_results(poll_name)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def public_poll_results(poll_name):
    return get_public_results(poll_name)


@frappe.whitelist(methods=["GET", "POST"])
def get_poll_turnout(poll_name):
    require_poll_administrator()
    cast_count = frappe.db.count("Consensus Participation", {"poll": poll_name})
    eligible = frappe.db.get_value("Consensus Poll", poll_name, "eligible_voter_count") or 0
    pct = round((cast_count / eligible * 100.0), 2) if eligible else 0.0
    return {
        "cast_count": cast_count,
        "eligible_count": eligible,
        "turnout_percentage": pct,
    }


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_ballot_canonical_text(poll_name, receipt_code):
    import json
    ballot = frappe.db.get_value(
        "Consensus Ballot",
        {"poll": poll_name, "receipt_code": receipt_code},
        ["receipt_code", "selected_option", "selected_option_label", "voting_weight", "cast_on", "ballot_hash"],
        as_dict=True,
    )
    if not ballot:
        frappe.throw("Ballot not found.")
    payload = {
        "poll": poll_name,
        "receipt_code": ballot.receipt_code,
        "selected_option": ballot.selected_option,
        "option_label": ballot.selected_option_label,
        "voting_weight": f"{float(ballot.voting_weight):.3f}",
        "cast_on": str(ballot.cast_on),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "canonical_text": canonical,
        "ballot_hash": ballot.ballot_hash,
    }


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_register_canonical_text(poll_name):
    import json
    from consensus.services.hashes import public_register_rows
    poll = frappe.db.get_value("Consensus Poll", poll_name, ["status", "register_published", "register_hash"], as_dict=True)
    if not poll or poll.status != "Closed" or not poll.register_published:
        frappe.throw("Published register is not available.")
    rows = public_register_rows(poll_name)
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "canonical_text": canonical,
        "register_hash": poll.register_hash,
    }
