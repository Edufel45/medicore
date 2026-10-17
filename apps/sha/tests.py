"""
MediCore — SHA Tests
============================================================
Tests for SHA claim creation + lifecycle.

Covers:
  - Automatic claim creation on visit completion (SHA-active only)
  - No claim for SHA-inactive patient
  - Claim reference format
  - Claim lifecycle: pending → approved → paid
  - Claim lifecycle: pending → rejected
  - Claims list access control
  - Claim detail access control
"""

from decimal import Decimal

import pytest

from apps.billing.models import Bill, Payment
from apps.billing.services import create_sha_claim_if_needed
from apps.clinical.models import Visit
from apps.patients.models import Patient
from apps.sha.models import SHAClaim


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _make_visit(patient, stage="doctor"):
    return Visit.objects.create(
        patient=patient,
        queue_number=Visit.generate_queue_number(),
        stage=stage,
        reason_for_visit="Test",
    )


def _give_bill_with_amount(visit, amount):
    bill = Bill.objects.create(visit=visit)
    bill.add_line_item(
        label="Consultation",
        amount=Decimal(str(amount)),
        kind="consultation",
    )
    return bill


# ------------------------------------------------------------
# Auto-creation
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_claim_created_for_active_sha(sample_patient):
    """SHA-active patient → claim created on completion."""
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)

    claim = create_sha_claim_if_needed(visit)

    assert claim is not None
    assert claim.status == "pending"
    assert claim.sha_number == "DEMO-1001"
    assert claim.patient_name == sample_patient.full_name
    assert float(claim.claim_amount) == 500.00
    assert claim.claim_reference.startswith("SHA-")


@pytest.mark.integration
@pytest.mark.django_db
def test_no_claim_for_inactive_sha(sample_patient):
    """SHA-inactive patient → no claim created."""
    sample_patient.sha_status = "inactive"
    sample_patient.save()

    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)

    claim = create_sha_claim_if_needed(visit)
    assert claim is None


@pytest.mark.integration
@pytest.mark.django_db
def test_no_claim_for_zero_bill(sample_patient):
    """Visit with 0 total → no claim created."""
    visit = _make_visit(sample_patient, stage="pharmacy")
    bill = Bill.objects.create(visit=visit)
    # No line items → total is 0

    claim = create_sha_claim_if_needed(visit)
    assert claim is None


@pytest.mark.integration
@pytest.mark.django_db
def test_no_duplicate_claim(sample_patient):
    """Calling the helper twice returns the same claim, no duplicate."""
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)

    claim1 = create_sha_claim_if_needed(visit)
    claim2 = create_sha_claim_if_needed(visit)

    assert claim1.id == claim2.id
    assert SHAClaim.objects.filter(visit=visit).count() == 1


@pytest.mark.integration
@pytest.mark.django_db
def test_claim_auto_created_on_pharmacy_complete(
    client_pharmacy, sample_patient, sample_drug
):
    """Full pharmacy completion triggers claim creation automatically."""
    from apps.clinical.models import Prescription

    visit = _make_visit(sample_patient, stage="pharmacy")
    bill = _give_bill_with_amount(visit, 500)

    # Add a prescription
    Prescription.objects.create(
        visit=visit,
        drug_name_typed=sample_drug.name,
        drug=sample_drug,
    )

    # Collect cash payment
    response = client_pharmacy.post(
        f"/visits/{visit.queue_number}/collect/",
        {"method": "cash"},
    )
    assert response.status_code == 302

    # Claim should exist
    claim = SHAClaim.objects.filter(visit=visit).first()
    assert claim is not None
    assert claim.status == "pending"


# ------------------------------------------------------------
# Reference format
# ------------------------------------------------------------
@pytest.mark.unit
def test_claim_reference_format():
    """Claim reference is SHA-MCYYYYNNNN-XXXX."""
    ref = SHAClaim.generate_reference("MC-2026-0001")
    assert ref.startswith("SHA-MC20260001-")
    assert len(ref.split("-")[-1]) == 4


# ------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_claim_lifecycle_approved(client_accounts, sample_patient):
    """Claim goes pending → approved via the simulate endpoint."""
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)
    claim = create_sha_claim_if_needed(visit)

    response = client_accounts.post(
        f"/sha/claims/{claim.id}/set/",
        {"status": "approved"},
    )
    assert response.status_code == 302

    claim.refresh_from_db()
    assert claim.status == "approved"
    assert claim.decided_at is not None
    assert claim.approved_amount is not None


@pytest.mark.integration
@pytest.mark.django_db
def test_claim_lifecycle_rejected(client_accounts, sample_patient):
    """Claim can be rejected with a reason."""
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)
    claim = create_sha_claim_if_needed(visit)

    response = client_accounts.post(
        f"/sha/claims/{claim.id}/set/",
        {"status": "rejected", "reason": "Missing diagnosis code"},
    )
    assert response.status_code == 302

    claim.refresh_from_db()
    assert claim.status == "rejected"
    assert claim.rejection_reason == "Missing diagnosis code"


@pytest.mark.integration
@pytest.mark.django_db
def test_claim_lifecycle_paid(client_accounts, sample_patient):
    """Claim can be marked paid after approval."""
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)
    claim = create_sha_claim_if_needed(visit)

    # Approve first
    claim.mark_approved(approved_amount=Decimal("500"))
    claim.refresh_from_db()

    # Then mark paid
    response = client_accounts.post(
        f"/sha/claims/{claim.id}/set/",
        {"status": "paid"},
    )
    assert response.status_code == 302

    claim.refresh_from_db()
    assert claim.status == "paid"
    assert claim.paid_at is not None


# ------------------------------------------------------------
# Access control
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_claims_list_blocked_for_doctor(client_doctor):
    response = client_doctor.get("/sha/claims/")
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_claims_list_allowed_for_accounts(client_accounts, sample_patient):
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)
    create_sha_claim_if_needed(visit)

    response = client_accounts.get("/sha/claims/")
    assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.django_db
def test_claim_detail_blocked_for_pharmacy(client_pharmacy, sample_patient):
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)
    claim = create_sha_claim_if_needed(visit)

    response = client_pharmacy.get(f"/sha/claims/{claim.id}/")
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_claim_detail_allowed_for_accounts(
    client_accounts, sample_patient
):
    visit = _make_visit(sample_patient, stage="pharmacy")
    _give_bill_with_amount(visit, 500)
    claim = create_sha_claim_if_needed(visit)

    response = client_accounts.get(f"/sha/claims/{claim.id}/")
    assert response.status_code == 200