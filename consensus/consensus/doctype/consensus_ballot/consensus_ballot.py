from frappe.model.document import Document


class ConsensusBallot(Document):
    """Consensus voting-engine record; service layer owns all writes."""
