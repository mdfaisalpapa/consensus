import secrets

import frappe
from frappe.model.document import Document


class ConsensusPollOption(Document):
    def before_validate(self):
        if not self.option_key:
            self.option_key = secrets.token_urlsafe(24)

    def validate(self):
        if self.poll and frappe.db.get_value("Consensus Poll", self.poll, "snapshot_state") == "Complete":
            if self.is_new() or (self.get_doc_before_save() and (
                self.option_key != self.get_doc_before_save().option_key
                or self.option_label != self.get_doc_before_save().option_label
                or self.description != self.get_doc_before_save().description
                or self.enabled != self.get_doc_before_save().enabled
                or self.display_order != self.get_doc_before_save().display_order
            )):
                frappe.throw("Poll options cannot change after eligibility snapshot completion.")

    def on_trash(self):
        if self.poll and frappe.db.get_value("Consensus Poll", self.poll, "snapshot_state") == "Complete":
            frappe.throw("Poll options cannot be deleted after eligibility snapshot completion.")
