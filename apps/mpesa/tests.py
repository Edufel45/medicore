"""
MediCore — M-Pesa Tests
============================================================
Tests for the Daraja integration, all with mocked HTTP.

No network calls to Safaricom. Uses `responses` to fake the
Daraja API endpoints.

Covers:
  - DarajaClient in mock mode (no credentials)
  - Phone normalization
  - STK Push callback processing
  - Payment creation on callback
  - Reversal flow
  - Transaction log access control
"""

import json
from decimal import Decimal

import pytest
import responses

from apps.billing.models import Bill, Payment
from apps.clinical.models import Visit
from apps.mpesa.daraja import DarajaClient, DarajaError, get_daraja
from apps.mpesa.models import CallbackLog, MPesaTransaction
from apps.patients.models import Patient


# ------------------------------------------------------------
# DarajaClient — mock mode
# ------------------------------------------------------------
@pytest.mark.unit
def test_daraja_is_mock_without_credentials(settings):
    """No credentials → is_mock is True."""
    settings.DARAJA_CONSUMER_KEY = ""
    settings.DARAJA_CONSUMER_SECRET = ""
    client = DarajaClient()
    assert client.is_mock is True


@pytest.mark.unit
def test_daraja_not_mock_with_credentials(settings):
    """Both credentials present → is_mock is False."""
    settings.DARAJA_CONSUMER_KEY = "test_key"
    settings.DARAJA_CONSUMER_SECRET = "test_secret"
    client = DarajaClient()
    assert client.is_mock is False


@pytest.mark.unit
def test_phone_normalization():
    """Various phone formats normalize to 2547XXXXXXXX."""
    client = DarajaClient()
    assert client._normalize_phone("0712345678") == "254712345678"
    assert client._normalize_phone("+254712345678") == "254712345678"
    assert client._normalize_phone("254712345678") == "254712345678"
    assert client._normalize_phone("712345678") == "254712345678"


@pytest.mark.unit
def test_stk_push_mock_returns_realistic_response(settings):
    """In mock mode, stk_push returns fake but well-formed data."""
    settings.DARAJA_CONSUMER_KEY = ""
    settings.DARAJA_CONSUMER_SECRET = ""
    client = DarajaClient()
    result = client.stk_push(
        phone="0712345678",
        amount=Decimal("500"),
        account_reference="INV-A-001",
        description="Test",
    )
    assert result["response_code"] == "0"
    assert result["checkout_request_id"].startswith("ws_CO_")
    assert len(result["merchant_request_id"]) > 0


@pytest.mark.unit
def test_stk_query_mock(settings):
    """In mock mode, stk_query returns a fake result."""
    settings.DARAJA_CONSUMER_KEY = ""
    settings.DARAJA_CONSUMER_SECRET = ""
    client = DarajaClient()
    result = client.stk_query("ws_CO_TEST_123")
    assert "result_code" in result
    assert "result_description" in result


@pytest.mark.unit
def test_reversal_mock(settings):
    """In mock mode, reversal returns a fake success."""
    settings.DARAJA_CONSUMER_KEY = ""
    settings.DARAJA_CONSUMER_SECRET = ""
    client = DarajaClient()
    result = client.reversal(
        transaction_id="QK12345678",
        amount=Decimal("100"),
        remarks="Test refund",
    )
    assert result["response_code"] == "0"


# ------------------------------------------------------------
# Real mode — mocked HTTP
# ------------------------------------------------------------
@pytest.mark.unit
@responses.activate
def test_oauth_fetch(settings):
    """access_token() calls Daraja OAuth and caches the token."""
    settings.DARAJA_CONSUMER_KEY = "test_key"
    settings.DARAJA_CONSUMER_SECRET = "test_secret"
    settings.DARAJA_BASE_URL = "https://sandbox.safaricom.co.ke"

    responses.add(
        responses.GET,
        "https://sandbox.safaricom.co.ke/oauth/v1/generate",
        json={"access_token": "fake_token_xyz", "expires_in": "3599"},
        status=200,
    )

    # Clear cache
    from django.core.cache import cache
    cache.clear()

    client = DarajaClient()
    token = client.access_token()
    assert token == "fake_token_xyz"


@pytest.mark.unit
@responses.activate
def test_stk_push_real_mode(settings):
    """In real mode, stk_push hits Daraja and parses the response."""
    settings.DARAJA_CONSUMER_KEY = "test_key"
    settings.DARAJA_CONSUMER_SECRET = "test_secret"
    settings.DARAJA_BASE_URL = "https://sandbox.safaricom.co.ke"
    settings.DARAJA_SHORTCODE = "174379"
    settings.DARAJA_PASSKEY = "test_passkey"

    # Mock OAuth
    responses.add(
        responses.GET,
        "https://sandbox.safaricom.co.ke/oauth/v1/generate",
        json={"access_token": "fake_token_xyz", "expires_in": "3599"},
        status=200,
    )

    # Mock STK Push
    responses.add(
        responses.POST,
        "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
        json={
            "MerchantRequestID": "123-456",
            "CheckoutRequestID": "ws_CO_123456",
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success. Request accepted for processing",
        },
        status=200,
    )

    from django.core.cache import cache
    cache.clear()

    client = DarajaClient()
    result = client.stk_push(
        phone="0712345678",
        amount=500,
        account_reference="INV-001",
    )

    assert result["checkout_request_id"] == "ws_CO_123456"
    assert result["response_code"] == "0"


# ------------------------------------------------------------
# Callback processing
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_stk_callback_creates_payment(client_pharmacy, sample_visit):
    """A successful STK Push callback creates a Payment and marks visit done."""
    # Setup: bill + pending transaction
    bill = Bill.objects.create(visit=sample_visit)
    bill.add_line_item(label="Consultation", amount=Decimal("500"), kind="consultation")
    sample_visit.stage = "pharmacy"
    sample_visit.save()

    txn = MPesaTransaction.objects.create(
        transaction_type="stk_push",
        status="pending",
        amount=Decimal("500"),
        phone="254712345678",
        account_reference=f"INV-{sample_visit.queue_number}",
        checkout_request_id="ws_CO_TEST_001",
        visit=sample_visit,
        initiated_by=None,
    )

    # Simulate a Safaricom callback
    payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "123",
                "CheckoutRequestID": "ws_CO_TEST_001",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 500},
                        {"Name": "MpesaReceiptNumber", "Value": "QKTEST123"},
                        {"Name": "PhoneNumber", "Value": "254712345678"},
                    ]
                },
            }
        }
    }

    response = client_pharmacy.post(
        "/mpesa/callback/stk/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200

    # Transaction should now be success
    txn.refresh_from_db()
    assert txn.status == "success"
    assert txn.mpesa_receipt == "QKTEST123"

    # Payment should exist
    payments = Payment.objects.filter(bill=bill, status="success")
    assert payments.count() == 1

    # Visit should be completed
    sample_visit.refresh_from_db()
    assert sample_visit.stage == "done"

    # CallbackLog should be processed
    log = CallbackLog.objects.filter(kind="stk_push").first()
    assert log is not None
    assert log.processed is True


@pytest.mark.integration
@pytest.mark.django_db
def test_stk_callback_unknown_checkout_id(client_pharmacy, sample_visit):
    """Callback with unknown checkout_request_id is logged but not matched."""
    payload = {
        "Body": {
            "stkCallback": {
                "CheckoutRequestID": "ws_CO_UNKNOWN",
                "ResultCode": 0,
                "ResultDesc": "Success",
            }
        }
    }
    response = client_pharmacy.post(
        "/mpesa/callback/stk/",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200

    log = CallbackLog.objects.filter(kind="stk_push").first()
    assert log is not None
    assert "No matching" in log.processing_note


# ------------------------------------------------------------
# Reversal
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_reversal_marks_payment_refunded(
    client_accounts, sample_visit
):
    """Accountant initiates reversal → transaction reversed, payment refunded."""
    bill = Bill.objects.create(visit=sample_visit)
    bill.add_line_item(label="Consultation", amount=Decimal("500"), kind="consultation")

    # Completed transaction
    txn = MPesaTransaction.objects.create(
        transaction_type="stk_push",
        status="success",
        amount=Decimal("500"),
        phone="254712345678",
        mpesa_receipt="QKTEST999",
        visit=sample_visit,
    )

    payment = Payment.objects.create(
        bill=bill,
        method="mpesa",
        amount=Decimal("500"),
        status="success",
        mpesa_transaction=txn,
        mpesa_receipt="QKTEST999",
    )
    bill.recompute_totals()

    # Initiate reversal
    response = client_accounts.post(
        f"/mpesa/reversal/{txn.id}/",
        {"reason": "Test refund"},
    )
    assert response.status_code == 302

    txn.refresh_from_db()
    assert txn.reversed_at is not None
    assert txn.reversal_reason == "Test refund"

    payment.refresh_from_db()
    assert payment.status == "refunded"


@pytest.mark.integration
@pytest.mark.django_db
def test_reversal_requires_success_status(
    client_accounts, sample_visit
):
    """Pending transactions cannot be reversed."""
    txn = MPesaTransaction.objects.create(
        transaction_type="stk_push",
        status="pending",
        amount=Decimal("500"),
        phone="254712345678",
        visit=sample_visit,
    )

    response = client_accounts.post(
        f"/mpesa/reversal/{txn.id}/",
        {"reason": "Attempt"},
    )
    assert response.status_code == 302

    txn.refresh_from_db()
    assert txn.reversed_at is None


# ------------------------------------------------------------
# Access control
# ------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.django_db
def test_transactions_view_blocked_for_doctor(client_doctor):
    """Doctors cannot view the M-Pesa transaction log."""
    response = client_doctor.get("/mpesa/transactions/")
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.django_db
def test_transactions_view_allowed_for_accounts(client_accounts):
    """Accountants can view the transaction log."""
    response = client_accounts.get("/mpesa/transactions/")
    assert response.status_code == 200