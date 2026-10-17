"""
MediCore — pytest fixtures
============================================================
Shared fixtures for all tests.

Fixtures:
  - reception_user, nurse_user, doctor_user, lab_user,
    pharmacy_user, accounts_user    : role-specific users
  - admin_user                      : superuser
  - client_reception, client_doctor, client_pharmacy, client_accounts
                                    : authenticated test clients
  - sample_patient                  : a ready-made patient
  - sample_visit                    : a visit at doctor stage
  - sample_drug                     : a drug in the catalogue
"""

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.clinical.models import Visit
from apps.patients.models import Patient
from apps.pharmacy.models import Drug


# ------------------------------------------------------------
# Group + user creation
# ------------------------------------------------------------
ROLE_GROUP_MAP = {
    "reception": "Receptionist",
    "nurse": "Nurse",
    "doctor": "Doctor",
    "lab": "Lab Technician",
    "pharmacy": "Pharmacist",
    "accounts": "Accountant",
}


def _make_user(username, role):
    group_name = ROLE_GROUP_MAP[role]
    group, _ = Group.objects.get_or_create(name=group_name)
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@test.local"},
    )
    if created:
        user.set_password("testpass123")
        user.save()
    user.groups.add(group)

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save()

    return user


# ------------------------------------------------------------
# Users — one per role
# ------------------------------------------------------------
@pytest.fixture
def reception_user(db):
    return _make_user("reception", "reception")


@pytest.fixture
def nurse_user(db):
    return _make_user("nurse", "nurse")


@pytest.fixture
def doctor_user(db):
    return _make_user("doctor", "doctor")


@pytest.fixture
def lab_user(db):
    return _make_user("lab", "lab")


@pytest.fixture
def pharmacy_user(db):
    return _make_user("pharmacy", "pharmacy")


@pytest.fixture
def accounts_user(db):
    return _make_user("accounts", "accounts")


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(
        username="admin", email="admin@test.local", password="adminpass123"
    )


# ------------------------------------------------------------
# Authenticated clients
# ------------------------------------------------------------
def _login_client(username, password="testpass123"):
    client = Client()
    client.login(username=username, password=password)
    return client


@pytest.fixture
def client_reception(reception_user):
    return _login_client("reception")


@pytest.fixture
def client_nurse(nurse_user):
    return _login_client("nurse")


@pytest.fixture
def client_doctor(doctor_user):
    return _login_client("doctor")


@pytest.fixture
def client_lab(lab_user):
    return _login_client("lab")


@pytest.fixture
def client_pharmacy(pharmacy_user):
    return _login_client("pharmacy")


@pytest.fixture
def client_accounts(accounts_user):
    return _login_client("accounts")


@pytest.fixture
def client_admin(admin_user):
    return _login_client("admin", password="adminpass123")


# ------------------------------------------------------------
# Domain objects
# ------------------------------------------------------------
@pytest.fixture
def sample_patient(db, reception_user):
    return Patient.objects.create(
        full_name="Test Patient",
        national_id="11112222",
        phone="0712345678",
        sex="F",
        age_years=30,
        sha_number="DEMO-1001",
        sha_status="active",
        reason_for_visit="Fever",
        created_by=reception_user,
    )


@pytest.fixture
def sample_visit(db, sample_patient, reception_user):
    return Visit.objects.create(
        patient=sample_patient,
        queue_number=Visit.generate_queue_number(),
        stage="doctor",
        reason_for_visit="Fever",
        created_by=reception_user,
    )


@pytest.fixture
def sample_drug(db):
    return Drug.objects.create(
        name="paracetamol 500mg",
        price_ksh=40,
        unit="tablet",
        reorder_level=25,
        is_active=True,
    )

# ------------------------------------------------------------
# Autouse: disable mock SHA latency in all tests
# ------------------------------------------------------------
@pytest.fixture(autouse=True)
def _disable_sha_latency(monkeypatch):
    """
    Speed up tests — MockSHAProvider should never sleep.
    Runs automatically for every test.
    """
    from apps.sha import providers

    original_init = providers.MockSHAProvider.__init__

    def fast_init(self, simulate_latency=True):
        original_init(self, simulate_latency=False)

    monkeypatch.setattr(providers.MockSHAProvider, "__init__", fast_init)