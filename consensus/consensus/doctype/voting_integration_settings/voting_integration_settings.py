import frappe
from frappe.model.document import Document


class VotingIntegrationSettings(Document):
    def validate(self):
        if self.provider_key:
            from consensus.providers.registry import get_registered_provider
            provider = get_registered_provider(self.provider_key)
            if getattr(provider, "test_only", False) and not (
                frappe.conf.get("allow_tests") or frappe.flags.in_test
            ):
                frappe.throw(
                    "Test-only voter provider cannot be enabled on production sites.",
                    frappe.PermissionError,
                )
            self.provider_api_version = provider.api_version
        if self.secret_resolver_key:
            from consensus.security.secrets import get_registered_secret_resolvers
            resolvers = get_registered_secret_resolvers()
            if self.secret_resolver_key not in resolvers:
                frappe.throw("Select a SecretResolver registered by a trusted Frappe hook.")
            resolver = resolvers[self.secret_resolver_key]
            if getattr(resolver, "test_only", False) and not (
                frappe.conf.get("allow_tests") or frappe.flags.in_test
            ):
                frappe.throw(
                    "Test-only secret resolver cannot be enabled on production sites.",
                    frappe.PermissionError,
                )
        if self.enabled and (not self.provider_key or not self.secret_resolver_key):
            frappe.throw("Enable Consensus only after selecting a registered provider and SecretResolver.")
