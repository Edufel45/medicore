"""
MediCore — SHA Providers
============================================================
Abstract interface + two implementations:

  - SHAProvider  (ABC)         : the contract
  - MockSHAProvider            : fake but realistic (used in dev/demo)
  - RealSHAProvider            : stub for the real SHA HMIS (Phase 7)

The provider is selected by settings.SHA_PROVIDER ("mock" | "real").

Why this matters:
  - The proposal explicitly states SHA HMIS is mocked for the
    project (no public API, requires empanelment).
  - Swapping to the real SHA HMIS should be a config change,
    not a code change.
  - Tests can use MockSHAProvider to run without network access.

Proposal reference:
  - §1.3.2 II "SHA HMIS integration for real-time patient
    verification and electronic claims submission"
  - §2.3 "The SHA HMIS transition framework requires real-time
    patient verification"
"""

from __future__ import annotations

import hashlib
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone


# ------------------------------------------------------------
# Result dataclasses
# ------------------------------------------------------------
@dataclass
class EligibilityResult:
    """Result of a SHA eligibility check."""
    sha_number: str
    status: str              # "active" | "inactive" | "pending" | "unknown"
    patient_name: str = ""
    message: str = ""
    verified_at: datetime = None
    raw: dict = None

    def __post_init__(self):
        if self.verified_at is None:
            self.verified_at = timezone.now()
        if self.raw is None:
            self.raw = {}


@dataclass
class ClaimSubmissionResult:
    """Result of submitting a claim to SHA."""
    claim_reference: str
    status: str              # "pending" | "approved" | "rejected"
    message: str = ""
    submitted_at: datetime = None
    raw: dict = None

    def __post_init__(self):
        if self.submitted_at is None:
            self.submitted_at = timezone.now()
        if self.raw is None:
            self.raw = {}


# ------------------------------------------------------------
# Abstract interface
# ------------------------------------------------------------
class SHAProvider(ABC):
    """
    The contract every SHA provider must implement.
    Swap Mock <-> Real by changing settings.SHA_PROVIDER.
    """

    @abstractmethod
    def verify_eligibility(self, sha_number: str) -> EligibilityResult:
        """Check if a patient is eligible for SHA coverage."""
        raise NotImplementedError

    @abstractmethod
    def submit_claim(self, claim_data: dict) -> ClaimSubmissionResult:
        """Submit a claim to SHA."""
        raise NotImplementedError

    @abstractmethod
    def get_claim_status(self, claim_reference: str) -> str:
        """Query the current status of a submitted claim."""
        raise NotImplementedError


# ------------------------------------------------------------
# Mock implementation
# ------------------------------------------------------------
class MockSHAProvider(SHAProvider):
    """
    Deterministic mock that behaves like the real SHA HMIS but
    without needing network access or API credentials.

    Rules (deterministic on the SHA number):
      - DEMO-1001  → active
      - DEMO-2002  → inactive
      - DEMO-3003  → pending
      - Anything else → hashed status (70% active, 15% inactive, 15% pending)

    The provider also simulates 200-400ms of network latency so
    the HTMX loader in the UI is visible.
    """

    DEMO_MAP = {
        "DEMO-1001": "active",
        "DEMO-2002": "inactive",
        "DEMO-3003": "pending",
    }

    def __init__(self, simulate_latency: bool = True):
        self.simulate_latency = simulate_latency

    # ---------------- verify ----------------
    def verify_eligibility(self, sha_number: str) -> EligibilityResult:
        sha_number = (sha_number or "").strip().upper()

        if self.simulate_latency:
            time.sleep(random.uniform(0.15, 0.35))

        if not sha_number:
            return EligibilityResult(
                sha_number=sha_number,
                status="unknown",
                message="No SHA number provided.",
            )

        # Known demo numbers
        if sha_number in self.DEMO_MAP:
            status = self.DEMO_MAP[sha_number]
            return EligibilityResult(
                sha_number=sha_number,
                status=status,
                patient_name=self._mock_name(sha_number),
                message=self._message_for(status),
                raw={"source": "mock", "rule": "demo_number"},
            )

        # Unknown numbers: deterministic pseudo-random
        h = int(hashlib.md5(sha_number.encode()).hexdigest(), 16)
        bucket = h % 100
        if bucket < 70:
            status = "active"
        elif bucket < 85:
            status = "inactive"
        else:
            status = "pending"

        return EligibilityResult(
            sha_number=sha_number,
            status=status,
            patient_name=self._mock_name(sha_number),
            message=self._message_for(status),
            raw={"source": "mock", "rule": "hashed", "bucket": bucket},
        )

    # ---------------- submit claim ----------------
    def submit_claim(self, claim_data: dict) -> ClaimSubmissionResult:
        if self.simulate_latency:
            time.sleep(random.uniform(0.2, 0.4))

        ref = self._generate_ref(claim_data.get("patient_sid", ""))
        return ClaimSubmissionResult(
            claim_reference=ref,
            status="pending",
            message="Claim received. Awaiting adjudication.",
            raw={"source": "mock"},
        )

    # ---------------- get status ----------------
    def get_claim_status(self, claim_reference: str) -> str:
        if not claim_reference:
            return "unknown"
        h = int(hashlib.md5(claim_reference.encode()).hexdigest(), 16)
        bucket = h % 100
        if bucket < 60:
            return "approved"
        if bucket < 75:
            return "rejected"
        if bucket < 90:
            return "pending"
        return "under_review"

    # ---------------- helpers ----------------
    @staticmethod
    def _message_for(status: str) -> str:
        return {
            "active":   "Eligible. SHA covers pharmacy bill for covered services.",
            "inactive": "Not eligible. Patient pays by M-Pesa at pharmacy.",
            "pending":  "Pending. Re-check before billing.",
            "unknown":  "Status not found. Enter a valid SHA number.",
        }.get(status, "Unknown status.")

    @staticmethod
    def _mock_name(sha_number: str) -> str:
        """Return a fake but stable name for demo purposes."""
        h = int(hashlib.md5(sha_number.encode()).hexdigest(), 16)
        firsts = ["Alice", "Brian", "Catherine", "David", "Esther", "Felix", "Grace", "Henry"]
        lasts = ["Kamau", "Wanjiku", "Otieno", "Mwangi", "Achieng", "Njoroge", "Ochieng", "Wambui"]
        return f"{firsts[h % len(firsts)]} {lasts[(h // 8) % len(lasts)]}"

    @staticmethod
    def _generate_ref(patient_sid: str) -> str:
        import random as _r
        suffix = "".join(str(_r.randint(0, 9)) for _ in range(4))
        base = patient_sid.replace("-", "") if patient_sid else "MC"
        return f"SHA-{base}-{suffix}"


# ------------------------------------------------------------
# Real implementation (stub)
# ------------------------------------------------------------
class RealSHAProvider(SHAProvider):
    """
    Stub for the real SHA HMIS integration.

    To be implemented in Phase 7 when the SHA facility is
    empanelled and API credentials are provisioned.
    """

    def __init__(self):
        self.base_url = settings.SHA_API_BASE
        self.api_key = settings.SHA_API_KEY
        self.facility_code = settings.SHA_FACILITY_CODE

    def verify_eligibility(self, sha_number: str) -> EligibilityResult:
        raise NotImplementedError(
            "RealSHAProvider is not implemented yet. "
            "Set SHA_PROVIDER=mock in .env to use the mock provider."
        )

    def submit_claim(self, claim_data: dict) -> ClaimSubmissionResult:
        raise NotImplementedError("RealSHAProvider.submit_claim not implemented.")

    def get_claim_status(self, claim_reference: str) -> str:
        raise NotImplementedError("RealSHAProvider.get_claim_status not implemented.")


# ------------------------------------------------------------
# Factory
# ------------------------------------------------------------
def get_sha_provider() -> SHAProvider:
    """
    Return the configured provider.
    Reads settings.SHA_PROVIDER ("mock" | "real").
    """
    provider = getattr(settings, "SHA_PROVIDER", "mock").lower()
    if provider == "real":
        return RealSHAProvider()
    return MockSHAProvider()


# ------------------------------------------------------------
# OTP verification (SHA Taifa Care-style)
# ------------------------------------------------------------
class SHAOTPProvider:
    """
    Extends SHAProvider with OTP challenge-based verification.

    Two-step flow:
      1. send_otp(sha_number, phone) → returns verification_id
         SHA (mock) "sends" a 6-digit OTP to the patient's phone.
      2. verify_otp(verification_id, otp_code) → returns EligibilityResult
         The OTP is validated; if correct, eligibility is returned.

    The mock provider generates the OTP deterministically from
    the SHA number so it's printable in the demo. The real provider
    will use a real SMS gateway.
    """

    def send_otp(self, sha_number: str, phone: str) -> dict:
        """
        Send an OTP to `phone`. Returns:
          {"verification_id": "...", "expires_in": 300, "masked_phone": "07XX…5678", "dev_otp": "123456"}
        """
        raise NotImplementedError

    def verify_otp(self, verification_id: str, otp_code: str) -> "EligibilityResult":
        """
        Validate the OTP. Returns an EligibilityResult.
        Raises ValueError if the OTP is wrong or expired.
        """
        raise NotImplementedError


class MockSHAOTPProvider(SHAOTPProvider):
    """
    Mock OTP provider. Uses Django cache to store pending challenges.

    In DEMO mode, `send_otp()` returns the OTP in the response so
    the receptionist can see it (pretend the patient's phone beeped
    and they read it out). In production, this field is removed.
    """

    CACHE_KEY_PREFIX = "sha_otp:"
    OTP_TTL = 300  # seconds

    def __init__(self):
        super().__init__()

    @staticmethod
    def _generate_otp(sha_number: str) -> str:
        """
        Deterministic 6-digit code from SHA number.
        Real provider would use random.randint(100000, 999999).
        """
        import hashlib
        h = int(hashlib.md5(("OTP" + sha_number.upper()).encode()).hexdigest(), 16)
        return f"{h % 1000000:06d}"

    def send_otp(self, sha_number: str, phone: str) -> dict:
        import uuid
        from django.core.cache import cache

        sha_number = (sha_number or "").strip().upper()
        phone = (phone or "").strip()

        if not sha_number:
            raise ValueError("SHA number is required.")
        if not phone:
            raise ValueError("Phone number is required.")

        verification_id = str(uuid.uuid4())
        otp = self._generate_otp(sha_number)

        cache.set(
            f"{self.CACHE_KEY_PREFIX}{verification_id}",
            {"sha_number": sha_number, "phone": phone, "otp": otp},
            self.OTP_TTL,
        )

        # Mask the phone for the UI: 0712345678 → 07XX…5678
        masked = phone
        if len(phone) >= 6:
            masked = phone[:2] + "XX" + "…" + phone[-4:]

        return {
            "verification_id": verification_id,
            "expires_in": self.OTP_TTL,
            "masked_phone": masked,
            "dev_otp": otp,  # ← REMOVE IN PRODUCTION
        }

    def verify_otp(self, verification_id: str, otp_code: str) -> "EligibilityResult":
        from django.core.cache import cache

        key = f"{self.CACHE_KEY_PREFIX}{verification_id}"
        data = cache.get(key)

        if not data:
            raise ValueError("OTP expired or invalid. Send a new code.")

        otp_code = (otp_code or "").strip()
        if otp_code != data["otp"]:
            raise ValueError("Incorrect code. Try again.")

        # OTP is correct — return eligibility for the SHA number
        eligibility = MockSHAProvider(simulate_latency=False).verify_eligibility(data["sha_number"])
        cache.delete(key)
        return eligibility


def get_sha_otp_provider():
    """
    Return the configured OTP provider.
    Always returns the mock for now — replace when real SHA API is available.
    """
    return MockSHAOTPProvider()