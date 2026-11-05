import frappe
from frappe.model.document import Document


class ConsensusPoll(Document):
    """Generic poll configuration with immutable post-snapshot settings."""

    def before_insert(self):
        from consensus.providers.registry import get_configured_provider
        provider = get_configured_provider()
        self.provider_key = provider.key
        self.provider_api_version = provider.api_version

    def validate(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            frappe.throw("Voting end time must be later than voting start time.")
        if self.status == "Open" and self.snapshot_state != "Complete":
            frappe.throw("A poll cannot open before its eligibility snapshot is complete.")
        if self.poll_type and not frappe.db.get_value("Consensus Poll Type", self.poll_type, "enabled"):
            frappe.throw("Select an enabled Consensus Poll Type.")
        if self.require_otp and not frappe.get_single("Voting Integration Settings").otp_enabled:
            frappe.throw("OTP is not enabled in Voting Integration Settings.")
        original = self.get_doc_before_save()
        if original and original.snapshot_state == "Complete":
            frozen_fields = ("title", "description", "poll_type", "provider_key", "provider_api_version",
                "starts_at", "ends_at", "scope_selections", "scope_selection_operator",
                "scope_conditions", "scope_conditions_operator", "rule_key", "rule_config",
                "require_otp", "result_visibility", "publish_anonymous_register")
            def frozen_value(document, field):
                value = document.get(field)
                if field == "scope_selections":
                    return tuple(
                        tuple(row.get(key) for key in (
                            "scope_type", "entity_ref", "inclusion", "parent_ref",
                            "display_label_snapshot", "hierarchy_path_snapshot",
                            "provider_key_snapshot", "provider_api_version_snapshot",
                        ))
                        for row in (value or [])
                    )
                return value

            changed = [
                field for field in frozen_fields
                if frozen_value(self, field) != frozen_value(original, field)
            ]
            if changed:
                frappe.throw("Poll configuration is frozen after eligibility snapshot completion: " + ", ".join(changed))
