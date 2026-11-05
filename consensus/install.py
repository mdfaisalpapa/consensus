"""Fresh-install roles and idempotent Consensus scope index setup."""

import frappe

CONSENSUS_ROLES = ("Consensus Administrator", "Consensus Eligibility Officer")


def after_install():
    for role_name in CONSENSUS_ROLES:
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": 1,
            }).insert(ignore_permissions=True)
    ensure_database_constraints()


def ensure_database_constraints():
    """Install the uniqueness and lookup indexes required by the voting engine."""
    constraints = {
        "Consensus Poll Scope": [(["parent", "parenttype", "parentfield", "scope_type", "entity_ref"], "uniq_cons_poll_scope_entity")],
        "Consensus Poll Option": [(["poll", "option_key"], "uniq_cons_poll_option_key")],
        "Consensus Eligibility": [
            (["poll", "voter_ref_digest"], "uniq_cons_elig_voter_digest"),
            (["poll", "voter_user"], "uniq_cons_elig_voter_user"),
        ],
        "Consensus Participation": [(["poll", "voter_ref_digest"], "uniq_cons_part_voter_digest")],
        "Consensus Ballot": [
            (["poll", "receipt_code"], "uniq_cons_ballot_receipt"),
            (["poll", "idempotency_hmac_key_version", "idempotency_digest"], "uniq_cons_ballot_idem"),
        ],
    }
    # Combine repeated DocType declarations while retaining deterministic order.
    merged = {}
    for doctype, rows in constraints.items():
        merged.setdefault(doctype, []).extend(rows)
    for doctype, rows in merged.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        for fields, name in rows:
            frappe.db.add_unique(doctype, fields, constraint_name=name)
    indexes = {
        "Consensus Eligibility": [["poll", "status"], ["voter_user", "status"]],
        "Consensus Participation": [["poll", "voter_user"]],
        "Consensus Ballot": [["poll", "receipt_code"]],
    }
    for doctype, fields_list in indexes.items():
        if frappe.db.exists("DocType", doctype):
            for fields in fields_list:
                frappe.db.add_index(doctype, fields)


# Backward-compatible hook name for already-installed development sites.
ensure_scope_selection_unique_index = ensure_database_constraints
