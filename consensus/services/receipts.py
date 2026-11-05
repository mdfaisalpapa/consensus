"""Transient signed PDF receipts and anonymous public register services."""
from __future__ import annotations

import base64
import io
import math
import re
from urllib.parse import urlencode
from xml.sax.saxutils import escape

import frappe
from frappe.utils import get_datetime

from consensus.security.secrets import resolve_secret
from consensus.services.eligibility import get_frozen_identity
from consensus.services.hashes import (
    ballot_hash,
    public_projection,
    public_register_rows,
    register_hash,
)


def _signer(poll):
    from asn1crypto import keys, pem, x509
    from pyhanko.sign.signers import SimpleSigner
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    version = poll.snapshot_signing_key_version
    key_pem, _ = resolve_secret("pdf_signing", version)
    certificate_url = frappe.get_single("Voting Integration Settings").signing_certificate
    if not certificate_url:
        frappe.throw("A public certificate must be configured before signed receipts can be generated.")
    certificate_path = frappe.get_doc("File", {"file_url": certificate_url}).get_full_path()
    certificate_pem = open(certificate_path, "rb").read()
    if not pem.detect(key_pem):
        frappe.throw("SecretResolver must return a PEM encoded signing key.")
    _, _, key_der = pem.unarmor(key_pem)
    _, _, cert_der = pem.unarmor(certificate_pem)
    private_key = keys.PrivateKeyInfo.load(key_der)
    certificate = x509.Certificate.load(cert_der)
    registry = SimpleCertificateStore()
    registry.register(certificate)
    return SimpleSigner(certificate, private_key, registry)


def _qr_url(poll, ballot):
    option_b64 = base64.urlsafe_b64encode(
        ballot.selected_option_label.encode("utf-8")
    ).decode("ascii").rstrip("=")
    claims = {
        "poll": poll.name, "receipt": ballot.receipt_code, "hash": ballot.ballot_hash,
        "option": option_b64, "weight": f"{float(ballot.voting_weight):.3f}",
    }
    return frappe.utils.get_url() + "/consensus-register?" + urlencode(claims)


def _make_pdf(poll, ballot, identity):
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    settings = frappe.get_single("Voting Integration Settings")
    fields = identity.get("fields", [])
    if not fields or any(row.get("required") and not str(row.get("value", "")).strip() for row in fields):
        frappe.throw("Frozen receipt identity is incomplete; signed receipt generation is blocked.")
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ConsensusHash", parent=styles["Normal"], fontName="Courier", fontSize=7))
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=18*mm, bottomMargin=25*mm)
    story = []
    if settings.logo:
        try:
            logo_path = frappe.get_doc("File", {"file_url": settings.logo}).get_full_path()
            story.append(Image(logo_path, width=35*mm, height=20*mm, kind="proportional"))
        except Exception as exc:
            raise frappe.ValidationError("Configured receipt logo is unavailable.") from exc
    story.extend([
        Paragraph(escape(settings.organisation_label or "Consensus"), styles["Heading2"]),
        Paragraph("VOTING RECEIPT", styles["Title"]),
        Spacer(1, 6*mm),
    ])
    rows = [["Poll", poll.frozen_title or poll.title], ["Poll reference", poll.name]]
    for field in sorted(fields, key=lambda item: (item.get("display_order", 0), item.get("key", ""))):
        rows.append([field.get("label", field["key"]), field.get("value", "")])
    rows.extend([
        ["Selected option", ballot.selected_option_label],
        ["Voting weight", f"{float(ballot.voting_weight):.3f}"],
        ["Receipt code", ballot.receipt_code],
        ["Date and time", str(ballot.cast_on)],
        ["SHA-256 ballot hash", Paragraph(escape(ballot.ballot_hash), styles["ConsensusHash"])],
    ])
    table = Table(rows, colWidths=[45*mm, 115*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([table, Spacer(1, 8*mm)])
    qr_value = _qr_url(poll, ballot)
    widget = qr.QrCodeWidget(qr_value)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(28*mm, 28*mm, transform=[28*mm/(x2-x1),0,0,28*mm/(y2-y1),0,0])
    drawing.add(widget)
    story.append(drawing)
    story.append(Paragraph(
        "The signature panel validates this PDF. Use the QR link to compare the anonymous ballot "
        "claims with the published register.", styles["Normal"]))
    doc.build(story)
    return buffer.getvalue()


def _signed_pdf(poll, ballot, identity):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigFieldSpec
    from pyhanko.sign.signers import PdfSignatureMetadata
    from pyhanko.stamp import TextStampStyle

    unsigned = _make_pdf(poll, ballot, identity)
    output = io.BytesIO()
    writer = IncrementalPdfFileWriter(io.BytesIO(unsigned))
    settings = frappe.get_single("Voting Integration Settings")
    signer = _signer(poll)
    pdf_signer = signers.PdfSigner(
        PdfSignatureMetadata(
            field_name="ConsensusSignature", md_algorithm="sha256",
            reason=settings.signature_reason or "Voting receipt",
        ),
        signer=signer,
        stamp_style=TextStampStyle(
            stamp_text="Digitally signed by %(signer)s\nDate: %(ts)s",
        ),
        new_field_spec=SigFieldSpec(
            sig_field_name="ConsensusSignature", on_page=-1,
            box=(56, 32, 539, 100), readable_field_name="Consensus Digital Signature",
        ),
    )
    pdf_signer.sign_pdf(
        writer, output=output,
        appearance_text_params={
            "signer": settings.signer_display_name or settings.organisation_label or "Consensus",
        },
    )
    result = output.getvalue()
    if not result.startswith(b"%PDF") or b"/ByteRange" not in result:
        frappe.throw("PDF signing failed; no signed receipt is available.")
    return result


def receipt_bytes(poll_name: str, receipt_code: str, eligibility_name: str):
    poll = frappe.get_doc("Consensus Poll", poll_name)
    ballot = frappe.db.get_value(
        "Consensus Ballot", {"poll": poll.name, "receipt_code": receipt_code},
        ["selected_option", "selected_option_label", "voting_weight", "cast_on", "ballot_hash",
         "receipt_code"], as_dict=True,
    )
    if not ballot:
        frappe.throw("Ballot receipt was not found.")
    eligibility = frappe.get_doc("Consensus Eligibility", eligibility_name)
    if eligibility.poll != poll.name:
        frappe.throw("Receipt identity allocation does not match this poll.")
    identity = get_frozen_identity(eligibility.name)
    return _signed_pdf(poll, ballot, identity)


def download_my_receipt(poll_name: str, idempotency_key: str):
    from consensus.services.voting import _eligible, _user, get_my_vote
    result = get_my_vote(poll_name, idempotency_key)
    eligibility = _eligible(poll_name, _user())
    content = receipt_bytes(poll_name, result["receipt_code"], eligibility.name)
    frappe.local.response.filename = f"Consensus-Receipt-{result['receipt_code']}.pdf"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
    frappe.local.response.display_content_as = "attachment"
    return


def publish_register(poll_name: str):
    from consensus.security.permissions import require_poll_administrator
    require_poll_administrator()
    poll = frappe.get_doc("Consensus Poll", poll_name)
    if poll.status != "Closed":
        frappe.throw("Only closed polls can publish the anonymous ballot register.")
    if not poll.publish_anonymous_register:
        frappe.throw("Register publication is disabled for this poll.")
    rows = public_register_rows(poll.name)
    digest = register_hash(rows)
    frappe.db.set_value("Consensus Poll", poll.name, {
        "register_published": 1, "register_hash": digest, "published_on": get_datetime()
    }, update_modified=False)
    return {"poll": poll.name, "register_hash": digest, "ballot_count": len(rows)}


def get_published_register(poll_name: str):
    poll = frappe.db.get_value("Consensus Poll", poll_name,
                               ["status", "register_published", "register_hash", "frozen_title", "published_on"],
                               as_dict=True)
    if not poll or poll.status != "Closed" or not poll.register_published:
        frappe.throw("This public ballot register is not available.")
    rows = public_register_rows(poll_name)
    if register_hash(rows) != poll.register_hash:
        frappe.throw("Published ballot register integrity check failed.")
    return {
        "poll": poll_name, "title": poll.frozen_title, "published_on": poll.published_on,
        "register_hash": poll.register_hash, "ballots": public_projection(rows),
    }


def verify_receipt_claims(poll_name: str, receipt_code: str, ballot_hash_claim: str, option: str, weight: str):
    """Guest verification compares anonymous QR claims against a verified public register."""
    if (
        not isinstance(poll_name, str) or not poll_name or len(poll_name) > 140
        or not isinstance(receipt_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", receipt_code)
        or not isinstance(ballot_hash_claim, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", ballot_hash_claim)
    ):
        return {"valid": False}
    try:
        if not isinstance(option, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", option):
            raise ValueError
        padded = option + "=" * (-len(option) % 4)
        label = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        weight_value = float(weight)
        if not math.isfinite(weight_value) or weight_value <= 0:
            raise ValueError
    except (TypeError, ValueError, UnicodeError):
        return {"valid": False}
    register = get_published_register(poll_name)
    match = next((row for row in register["ballots"] if row["receipt_code"] == receipt_code), None)
    if not match:
        return {"valid": False, "register_hash": register["register_hash"]}
    stored = frappe.db.get_value(
        "Consensus Ballot", {"poll": poll_name, "receipt_code": receipt_code},
        ["selected_option", "voting_weight", "cast_on", "ballot_hash"], as_dict=True,
    )
    recomputed = ballot_hash(
        poll_name, receipt_code, stored.selected_option, match["selected_option_label"],
        stored.voting_weight, stored.cast_on,
    ) if stored else None
    valid = (
        match["ballot_hash"] == ballot_hash_claim.lower()
        and match["selected_option_label"] == label
        and f"{float(match['voting_weight']):.3f}" == f"{weight_value:.3f}"
        and recomputed == stored.ballot_hash == match["ballot_hash"]
    )
    return {"valid": valid, "receipt_code": receipt_code,
            "register_hash": register["register_hash"], "ballot": match}


def download_register_csv(poll_name: str):
    import csv
    register = get_published_register(poll_name)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Receipt Code", "Selected Option", "Voting Weight", "Ballot Hash"])
    for row in register["ballots"]:
        writer.writerow([row["receipt_code"], row["selected_option_label"],
                         f"{float(row['voting_weight']):.3f}", row["ballot_hash"]])
    frappe.local.response.filename = f"Consensus-Register-{poll_name}.csv"
    frappe.local.response.filecontent = output.getvalue()
    frappe.local.response.type = "download"


def download_register_pdf(poll_name: str):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    register = get_published_register(poll_name)
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4))
    rows = [["Receipt Code", "Selected Option", "Weight", "Ballot Hash"]]
    rows.extend([[r["receipt_code"], r["selected_option_label"], f"{float(r['voting_weight']):.3f}",
                  r["ballot_hash"]] for r in register["ballots"]])
    table = Table(rows, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.lightgrey),("GRID",(0,0),(-1,-1),.3,colors.grey)]))
    doc.build([table])
    frappe.local.response.filename = f"Consensus-Register-{poll_name}.pdf"
    frappe.local.response.filecontent = out.getvalue()
    frappe.local.response.type = "download"


def publish_results(poll_name: str):
    from consensus.security.permissions import require_poll_administrator
    require_poll_administrator()
    poll = frappe.get_doc("Consensus Poll", poll_name)
    if poll.status != "Closed":
        frappe.throw("Poll results can be published only after closing.")
    frappe.db.set_value("Consensus Poll", poll.name, {
        "results_published": 1, "results_published_on": get_datetime()
    }, update_modified=False)
    return {"poll": poll.name, "published": True}


def get_public_results(poll_name: str):
    poll = frappe.db.get_value(
        "Consensus Poll", poll_name,
        ["status", "result_visibility", "results_published", "frozen_title"], as_dict=True,
    )
    if not poll:
        frappe.throw("Poll was not found.")
    if poll.status not in ("Open", "Closed"):
        frappe.throw("Poll results are not published.")
    visible = (
        poll.result_visibility == "Immediately"
        or (poll.result_visibility == "After Closing" and poll.status == "Closed")
        or (poll.result_visibility == "Manual" and poll.results_published)
    )
    if not visible:
        frappe.throw("Poll results are not published.")
    options = frappe.get_all(
        "Consensus Poll Option", filters={"poll": poll_name},
        fields=["name", "frozen_label", "frozen_display_order"],
        order_by="frozen_display_order asc",
    )
    results = []
    for option in options:
        ballots = frappe.get_all(
            "Consensus Ballot", filters={"poll": poll_name, "selected_option": option.name},
            fields=["voting_weight"],
        )
        results.append({
            "option_label": option.frozen_label,
            "ballot_count": len(ballots),
            "total_weight": round(sum(float(row.voting_weight or 0) for row in ballots), 3),
        })
    return {"poll": poll_name, "title": poll.frozen_title, "results": results}
