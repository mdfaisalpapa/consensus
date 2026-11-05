"""Compatibility-sensitive anonymous ballot and public register hashes."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def ballot_hash(poll_name, receipt_code, selected_option, option_label, weight, cast_time):
    """Society ERP voting v1 canonical payload and serialization; do not alter without versioning."""
    payload = {
        "poll": poll_name,
        "receipt_code": receipt_code,
        "selected_option": selected_option,
        "option_label": option_label,
        "voting_weight": f"{float(weight):.3f}",
        "cast_on": str(cast_time),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def public_register_rows(poll_name: str) -> list[dict[str, Any]]:
    import frappe
    ballots = frappe.get_all(
        "Consensus Ballot", filters={"poll": poll_name},
        fields=["receipt_code", "selected_option", "selected_option_label", "voting_weight", "ballot_hash"],
        order_by="receipt_code asc",
    )
    return [{
        "receipt_code": row.receipt_code,
        "selected_option": row.selected_option,
        "selected_option_label": row.selected_option_label or "Option unavailable",
        "voting_weight": float(row.voting_weight or 0),
        "ballot_hash": row.ballot_hash,
    } for row in ballots]


def register_hash(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def public_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "receipt_code": row["receipt_code"],
        "selected_option_label": row["selected_option_label"],
        "voting_weight": row["voting_weight"],
        "ballot_hash": row["ballot_hash"],
    } for row in rows]
