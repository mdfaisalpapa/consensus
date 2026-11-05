"""Authenticated provider-driven scope discovery for poll configuration."""

import frappe

from consensus.providers.registry import get_configured_provider
from consensus.providers.scope_resolution import list_scope_entities, list_scope_types
from consensus.security.permissions import require_poll_administrator


@frappe.whitelist()
def get_scope_types():
    require_poll_administrator()
    provider = get_configured_provider()
    return list_scope_types(provider)


@frappe.whitelist()
def get_scope_entities(scope_type, query=None, parent_ref=None):
    require_poll_administrator()
    provider = get_configured_provider()
    return list_scope_entities(provider, scope_type, query, parent_ref)
