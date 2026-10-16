"""
MediCore — Billing Services
============================================================
Receipt generation + PDF rendering + storage upload.

Storage strategy:
  1. If Supabase is configured (SUPABASE_URL + SUPABASE_SERVICE_KEY in .env):
     upload the PDF to Supabase Storage and save the public URL.
  2. Otherwise:
     save to MEDIA_ROOT/receipts/ and store a relative URL.

The caller doesn't care which path was taken — the Receipt.pdf_url
ends up populated either way.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger("medicore")


# ------------------------------------------------------------
# HTML / PDF rendering
# ------------------------------------------------------------
def render_receipt_html(receipt, bill, visit):
    """
    Return the receipt as a full HTML document string
    (suitable for WeasyPrint AND for direct browser viewing).
    """
    from decimal import Decimal

    line_items = list(bill.line_items.all())

    # SHA-covered vs patient-paid split
    sha_covered = getattr(receipt, "sha_covered", Decimal("0.00")) or Decimal("0.00")
    patient_paid = (receipt.total_paid or Decimal("0.00")) - sha_covered

    ctx = {
        "receipt": receipt,
        "bill": bill,
        "visit": visit,
        "patient": visit.patient,
        "line_items": line_items,
        "patient_paid": patient_paid,
        "sha_covered": sha_covered,
        "issued_at": receipt.issued_at or timezone.now(),
        "hospital_name": "MediCore Hospital",
        "hospital_sub": "Sample Hospital · OPD",
    }

    # The receipt template is a partial (no <html>/<head>/<body>).
    # We wrap it in a minimal document shell for standalone use
    # (WeasyPrint + direct browser open).
    body = render_to_string("billing/receipt.html", ctx)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Receipt {receipt.receipt_number}</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;">
{body}
</body>
</html>"""


def render_receipt_pdf(receipt, bill, visit):
    """
    Render the receipt to PDF bytes using WeasyPrint.
    Returns bytes or None on failure.
    """
    try:
        from weasyprint import HTML
    except ImportError:
        logger.warning("WeasyPrint not installed; skipping PDF generation.")
        return None

    try:
        html = render_receipt_html(receipt, bill, visit)
        pdf_bytes = HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
        return pdf_bytes
    except Exception as exc:
        logger.exception("PDF generation failed: %s", exc)
        return None


# ------------------------------------------------------------
# Storage
# ------------------------------------------------------------
def _supabase_configured():
    return bool(
        getattr(settings, "SUPABASE_URL", "")
        and getattr(settings, "SUPABASE_SERVICE_KEY", "")
    )


def _upload_to_supabase(path, pdf_bytes):
    """
    Upload PDF to Supabase Storage. Returns the public URL or None.
    """
    try:
        from supabase import create_client
    except ImportError:
        logger.warning("supabase-py not installed; skipping Supabase upload.")
        return None

    try:
        client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
        )
        bucket = settings.SUPABASE_STORAGE_BUCKET or "medicore"

        # Ensure bucket exists (idempotent)
        try:
            client.storage.create_bucket(bucket, options={"public": True})
        except Exception:
            pass  # already exists or no permission

        # Upload
        client.storage.from_(bucket).upload(
            path=path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        public_url = client.storage.from_(bucket).get_public_url(path)
        return public_url
    except Exception as exc:
        logger.exception("Supabase upload failed: %s", exc)
        return None


def _save_to_local(path, pdf_bytes):
    """
    Save PDF under MEDIA_ROOT/receipts/. Returns a relative URL.
    """
    target = Path(settings.MEDIA_ROOT) / "receipts" / Path(path).name
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        f.write(pdf_bytes)
    return f"{settings.MEDIA_URL}receipts/{target.name}"


def store_receipt_pdf(receipt, bill, visit, force_local=False):
    """
    Generate the PDF and store it.

    Returns the URL where it can be fetched, or "" on failure.
    """
    pdf_bytes = render_receipt_pdf(receipt, bill, visit)
    if not pdf_bytes:
        return ""

    filename = f"{receipt.receipt_number}.pdf"
    path = f"receipts/{filename}"

    if not force_local and _supabase_configured():
        url = _upload_to_supabase(path, pdf_bytes)
        if url:
            return url

    return _save_to_local(path, pdf_bytes) 


# ------------------------------------------------------------
# SHA claim auto-creation
# ------------------------------------------------------------
def create_sha_claim_if_needed(visit, user=None):
    """
    Create a SHAClaim for the visit IF:
      - The patient's SHA status is "active"
      - No claim exists yet for this visit
      - The bill has a total > 0

    Returns the created/existing SHAClaim, or None if not applicable.
    """
    from decimal import Decimal
    from django.utils import timezone as _tz

    from apps.sha.models import SHAClaim
    from apps.sha.providers import get_sha_provider

    patient = visit.patient
    if patient.sha_status != "active":
        return None

    bill = getattr(visit, "bill", None)
    if not bill or (bill.total_amount or Decimal("0.00")) <= 0:
        return None

    # Already exists? Return it.
    existing = SHAClaim.objects.filter(visit=visit).first()
    if existing:
        return existing

    # Build the claim
    claim_amount = bill.sha_covered_amount or bill.total_amount

    claim = SHAClaim(
        visit=visit,
        claim_reference=SHAClaim.generate_reference(patient.sid),
        sha_number=patient.sha_number or "",
        patient_name=patient.full_name,
        patient_national_id=patient.national_id or "",
        claim_amount=claim_amount,
        fund_type="primary",     # can be smarter later (L1-3 vs L4-6)
        status="pending",
        submitted_at=_tz.now(),
        created_by=user,
    )
    claim.save()

    # Ask the provider (mock) to acknowledge; keeps the flow realistic
    try:
        provider = get_sha_provider()
        provider.submit_claim({
            "patient_sid": patient.sid,
            "claim_amount": str(claim_amount),
            "reference": claim.claim_reference,
        })
    except Exception as exc:
        logger.warning("Provider claim submit failed (ignored): %s", exc)

    return claim