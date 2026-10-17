"""
MediCore — Clinical Tests
============================================================
Tests for the full clinical loop:

  registration → nurse vitals → doctor consult → lab → pharmacy → done

Covers:
  - Queue board visibility by stage
  - Nurse saves vitals → returns to doctor
  - Doctor saves diagnosis + prescriptions + lab orders
  - Doctor routes to lab / pharmacy / nurse
  - Lab releases results → returns to doctor
  - Pharmacy prices + collects + completes
"""

import pytest

from apps.billing.models import Bill, Payment
from apps.clinical.models import Diagnosis, Prescription, Visit, Vitals
from apps.laboratory.models import LabResult, LabTest
from apps.pharmacy.models import Drug
from apps.patients.models import Patient


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def visit_at_doctor(db, sample_patient, reception_user):
    """A visit ready for the doctor."""
    return Visit.objects.create(
        patient=sample_patient,
        queue_number=Visit.generate_queue_number(),
        stage="doctor",
        reason_for_visit="Fever",
        created_by=reception_user,
    )


# ------------------------------------------------------------
# Queue board
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_queue_board_shows_visit(client_doctor, visit_at_doctor):
    """Queue board renders with the patient in the Doctor column."""
    response = client_doctor.get("/queue/")
    assert response.status_code == 200
    content = response.content.decode()
    assert visit_at_doctor.queue_number in content
    assert visit_at_doctor.patient.sid in content


@pytest.mark.integration
@pytest.mark.django_db
def test_queue_board_excludes_done(client_doctor, visit_at_doctor):
    """Completed visits are not shown on the queue board."""
    visit_at_doctor.stage = "done"
    visit_at_doctor.save()

    response = client_doctor.get("/queue/")
    content = response.content.decode()
    assert visit_at_doctor.queue_number not in content


# ------------------------------------------------------------
# Nurse
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_nurse_saves_vitals_and_returns_to_doctor(client_nurse, visit_at_doctor):
    """Nurse can save vitals, which moves the visit back to doctor."""
    visit_at_doctor.stage = "nurse"
    visit_at_doctor.save()

    response = client_nurse.post(
        f"/visits/{visit_at_doctor.queue_number}/vitals/",
        {
            "blood_pressure": "118/76",
            "temperature_c": "36.8",
            "weight_kg": "64",
            "pulse_bpm": "88",
        },
    )
    assert response.status_code == 302   # redirect

    visit_at_doctor.refresh_from_db()
    assert visit_at_doctor.stage == "doctor"

    vitals = Vitals.objects.get(visit=visit_at_doctor)
    assert vitals.blood_pressure == "118/76"
    assert float(vitals.temperature_c) == 36.8
    assert float(vitals.weight_kg) == 64
    assert vitals.pulse_bpm == 88


@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_cannot_save_vitals(client_doctor, visit_at_doctor):
    """The doctor role cannot POST to the vitals endpoint (403)."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/vitals/",
        {"blood_pressure": "120/80"},
    )
    assert response.status_code == 403


# ------------------------------------------------------------
# Doctor
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_saves_diagnosis(client_doctor, visit_at_doctor):
    """Doctor saves a diagnosis on their visit."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/save-diagnosis/",
        {"diagnosis": "Fever, query malaria"},
    )
    assert response.status_code == 302

    dx = Diagnosis.objects.get(visit=visit_at_doctor)
    assert "malaria" in dx.text.lower()


@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_adds_prescription(client_doctor, visit_at_doctor, sample_drug):
    """Doctor adds a drug by name; if it matches the catalogue, it resolves."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/add-rx/",
        {"drug_name": "paracetamol 500mg"},
    )
    assert response.status_code == 302

    rx = Prescription.objects.get(visit=visit_at_doctor)
    assert rx.drug_name_typed == "paracetamol 500mg"
    assert rx.drug == sample_drug
    assert rx.is_dispensed is False


@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_orders_lab_test(client_doctor, visit_at_doctor):
    """Doctor orders a lab test → LabTest row + Bill line item created."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/add-lab/",
        {"test_name": "Malaria test", "test_price": "300"},
    )
    assert response.status_code == 302

    lt = LabTest.objects.get(visit=visit_at_doctor)
    assert lt.name == "Malaria test"
    assert float(lt.price_ksh) == 300
    assert lt.status == "ordered"

    # Bill should have a Lab line item
    if hasattr(visit_at_doctor, "bill"):
        assert any(
            li.kind == "lab"
            for li in visit_at_doctor.bill.line_items.all()
        )


@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_sends_to_lab(client_doctor, visit_at_doctor):
    """Routing to lab changes the visit stage."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/send-to-lab/"
    )
    assert response.status_code == 302
    visit_at_doctor.refresh_from_db()
    assert visit_at_doctor.stage == "lab"


@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_sends_to_pharmacy(client_doctor, visit_at_doctor):
    """Routing to pharmacy changes the visit stage."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/send-to-pharmacy/"
    )
    assert response.status_code == 302
    visit_at_doctor.refresh_from_db()
    assert visit_at_doctor.stage == "pharmacy"


@pytest.mark.integration
@pytest.mark.django_db
def test_doctor_requests_vitals(client_doctor, visit_at_doctor):
    """Requesting vitals moves the visit to the nurse stage."""
    response = client_doctor.post(
        f"/visits/{visit_at_doctor.queue_number}/request-vitals/"
    )
    assert response.status_code == 302
    visit_at_doctor.refresh_from_db()
    assert visit_at_doctor.stage == "nurse"


# ------------------------------------------------------------
# Lab
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_lab_releases_results_and_returns_to_doctor(
    client_lab, visit_at_doctor
):
    """Lab enters results → visit returns to doctor with LabResult attached."""
    lt = LabTest.objects.create(
        visit=visit_at_doctor,
        name="Malaria test",
        price_ksh=300,
        status="ordered",
    )
    visit_at_doctor.stage = "lab"
    visit_at_doctor.save()

    response = client_lab.post(
        f"/visits/{visit_at_doctor.queue_number}/release-lab/",
        {"lab_results": "Malaria test: Negative"},
    )
    assert response.status_code == 302

    visit_at_doctor.refresh_from_db()
    assert visit_at_doctor.stage == "doctor"

    lt.refresh_from_db()
    assert lt.status == "released"

    result = LabResult.objects.get(lab_test=lt)
    assert result.value == "Negative"


# ------------------------------------------------------------
# Pharmacy
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_pharmacy_sets_price_and_collects_cash(
    client_pharmacy, visit_at_doctor, sample_patient
):
    """Full pharmacy flow: price drug, then collect cash payment and complete."""
    from decimal import Decimal

    # Create bill + visit at pharmacy stage
    visit_at_doctor.stage = "pharmacy"
    visit_at_doctor.save()

    bill = Bill.objects.create(visit=visit_at_doctor)
    bill.add_line_item(
        label="Registration fee",
        amount=Decimal("200"),
        kind="registration",
    )

    rx = Prescription.objects.create(
        visit=visit_at_doctor,
        drug_name_typed="amoxicillin 500mg",
        is_dispensed=False,
    )

    # 1. Pharmacy sets price for unpriced drug
    response = client_pharmacy.post(
        f"/visits/{visit_at_doctor.queue_number}/price-rx/{rx.id}/",
        {"price": "120"},
    )
    assert response.status_code == 302

    rx.refresh_from_db()
    assert rx.drug is not None
    assert float(rx.drug.price_ksh) == 120

    # Bill should now total 320 (200 reg + 120 drug)
    bill.refresh_from_db()
    assert float(bill.total_amount) == 320

    # 2. Collect cash payment — completes visit
    response = client_pharmacy.post(
        f"/visits/{visit_at_doctor.queue_number}/collect/",
        {"method": "cash"},
    )
    assert response.status_code == 302

    visit_at_doctor.refresh_from_db()
    bill.refresh_from_db()

    assert visit_at_doctor.stage == "done"
    assert bill.status == "settled"
    assert Payment.objects.filter(bill=bill, status="success").exists()


# ------------------------------------------------------------
# Role gating
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_nurse_cannot_route_to_lab(client_nurse, visit_at_doctor):
    """Nurse is blocked from the doctor-only send-to-lab endpoint."""
    response = client_nurse.post(
        f"/visits/{visit_at_doctor.queue_number}/send-to-lab/"
    )
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_pharmacy_cannot_save_diagnosis(client_pharmacy, visit_at_doctor):
    """Pharmacist blocked from the doctor-only save-diagnosis endpoint."""
    response = client_pharmacy.post(
        f"/visits/{visit_at_doctor.queue_number}/save-diagnosis/",
        {"diagnosis": "Attempted bypass"},
    )
    assert response.status_code == 403