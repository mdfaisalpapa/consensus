"""Shared role checks for Consensus configuration endpoints."""

import frappe


def require_poll_administrator():
    frappe.only_for(["System Manager", "Consensus Administrator"])


def require_eligibility_officer():
    frappe.only_for([
        "System Manager",
        "Consensus Administrator",
        "Consensus Eligibility Officer",
    ])
