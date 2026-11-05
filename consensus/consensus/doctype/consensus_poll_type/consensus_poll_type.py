import re

import frappe
from frappe.model.document import Document


class ConsensusPollType(Document):
    """Administrator-configurable category, independent of voting scope."""

    def validate(self):
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.type_key or ""):
            frappe.throw("Type Key must be a lowercase slug using letters, digits and hyphens.")
        if self.default_rule_config:
            config = frappe.parse_json(self.default_rule_config)
            if not isinstance(config, dict):
                frappe.throw("Default provider rule configuration must be a JSON object.")
