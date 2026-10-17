"""
MediCore — M-Pesa Daraja Service
============================================================
Wraps Safaricom's Daraja API.

Design:
  - One class: DarajaClient
  - Methods: access_token(), stk_push(), stk_query(),
             reversal(), c2b_register_urls(), c2b_simulate()
  - Falls back to MOCK MODE if credentials are missing — no
    exceptions, no network calls. Tests and local dev run
    entirely offline.

References (Daraja docs):
  - OAuth: /oauth/v1/generate?grant_type=client_credentials
  - STK Push: /mpesa/stkpush/v1/processrequest
  - STK Query: /mpesa/stkpushquery/v1/query
  - Reversal: /mpesa/reversal/v1/request
  - C2B Register: /mpesa/c2b/v1/registerurl
  - C2B Simulate: /mpesa/c2b/v1/simulate
"""

import base64
import logging
import random
import time
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("medicore")


# ------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------
class DarajaError(Exception):
    """Raised when the Daraja API returns an error."""


# ------------------------------------------------------------
# Client
# ------------------------------------------------------------
class DarajaClient:
    """
    Thin wrapper around Daraja. Use the helper methods; they
    return plain dicts.
    """

    TOKEN_CACHE_KEY = "daraja_access_token"
    TOKEN_CACHE_SECONDS = 50 * 60     # Daraja tokens last 1h; refresh at 50min

    def __init__(self):
        self.base_url = getattr(settings, "DARAJA_BASE_URL", "https://sandbox.safaricom.co.ke")
        self.consumer_key = getattr(settings, "DARAJA_CONSUMER_KEY", "")
        self.consumer_secret = getattr(settings, "DARAJA_CONSUMER_SECRET", "")
        self.shortcode = getattr(settings, "DARAJA_SHORTCODE", "174379")
        self.passkey = getattr(settings, "DARAJA_PASSKEY", "")
        self.callback_url = getattr(settings, "DARAJA_CALLBACK_URL", "")
        self.initiator_name = getattr(settings, "DARAJA_INITIATOR_NAME", "testapi")
        self.initiator_password = getattr(settings, "DARAJA_INITIATOR_PASSWORD", "")
        self.security_credential = getattr(settings, "DARAJA_SECURITY_CREDENTIAL", "")

    # ---------------- Mock mode check ----------------
    @property
    def is_mock(self):
        """True if we don't have real credentials — use mock responses."""
        return not (self.consumer_key and self.consumer_secret)

    # ---------------- Auth ----------------
    def access_token(self):
        """
        Get an OAuth access token. Cached for ~50 minutes.
        In mock mode, returns 'MOCK_TOKEN'.
        """
        if self.is_mock:
            return "MOCK_TOKEN"

        cached = cache.get(self.TOKEN_CACHE_KEY)
        if cached:
            return cached

        url = f"{self.base_url}/oauth/v1/generate"
        try:
            r = requests.get(
                url,
                params={"grant_type": "client_credentials"},
                auth=(self.consumer_key, self.consumer_secret),
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise DarajaError(f"OAuth failed: {exc}") from exc

        token = data.get("access_token")
        if not token:
            raise DarajaError(f"No access_token in response: {data}")

        cache.set(self.TOKEN_CACHE_KEY, token, self.TOKEN_CACHE_SECONDS)
        return token

    # ---------------- STK Push ----------------
    def stk_push(self, phone, amount, account_reference, description="Payment"):
        """
        Initiate an STK Push (Lipa na M-Pesa Online).

        Args:
          phone (str): 2547XXXXXXXX format
          amount (Decimal | int | str)
          account_reference (str): e.g. "INV-001"
          description (str): shown on the customer's phone

        Returns:
          dict with keys: merchant_request_id, checkout_request_id,
                          response_code, response_description, customer_message, raw

        In mock mode: generates a fake but realistic response.
        """
        phone = self._normalize_phone(phone)
        amount = int(Decimal(str(amount)))      # Daraja wants an integer
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{self.shortcode}{self.passkey}{timestamp}".encode()
        ).decode()

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone,
            "PartyB": self.shortcode,
            "PhoneNumber": phone,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference[:12],    # Daraja limit
            "TransactionDesc": description[:13],           # Daraja limit
        }

        if self.is_mock:
            # Simulate a realistic response
            time.sleep(0.3)
            mock_checkout = f"ws_CO_{timestamp}_{random.randint(100000, 999999)}"
            return {
                "merchant_request_id": f"MOCK-MRID-{random.randint(100000, 999999)}",
                "checkout_request_id": mock_checkout,
                "response_code": "0",
                "response_description": "Success. Request accepted for processing",
                "customer_message": "Success. Request accepted for processing",
                "raw": {"mock": True, "payload": payload},
            }

        # Real API call
        url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        token = self.access_token()
        try:
            r = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise DarajaError(f"STK Push failed: {exc}") from exc

        return {
            "merchant_request_id": data.get("MerchantRequestID", ""),
            "checkout_request_id": data.get("CheckoutRequestID", ""),
            "response_code": data.get("ResponseCode", ""),
            "response_description": data.get("ResponseDescription", ""),
            "customer_message": data.get("CustomerMessage", ""),
            "raw": data,
        }

    # ---------------- STK Query ----------------
    def stk_query(self, checkout_request_id):
        """
        Query the status of an STK Push transaction.
        Used to reconcile timeouts when the callback never arrived.
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{self.shortcode}{self.passkey}{timestamp}".encode()
        ).decode()

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }

        if self.is_mock:
            time.sleep(0.2)
            # 70% chance success, 30% still pending
            result_code = "0" if random.random() < 0.7 else "1037"
            return {
                "result_code": result_code,
                "result_description": (
                    "The service request is processed successfully."
                    if result_code == "0"
                    else "DS timeout user cannot be reached"
                ),
                "raw": {"mock": True},
            }

        url = f"{self.base_url}/mpesa/stkpushquery/v1/query"
        token = self.access_token()
        try:
            r = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise DarajaError(f"STK Query failed: {exc}") from exc

        return {
            "result_code": data.get("ResultCode", ""),
            "result_description": data.get("ResultDesc", ""),
            "raw": data,
        }

    # ---------------- Reversal ----------------
    def reversal(self, transaction_id, amount, remarks="Refund"):
        """
        Reverse a completed transaction. Full or partial.
        transaction_id: Safaricom TransactionID (e.g. "QK12345678")
        """
        payload = {
            "Initiator": self.initiator_name,
            "SecurityCredential": self.security_credential,
            "CommandID": "TransactionReversal",
            "TransactionID": transaction_id,
            "Amount": int(Decimal(str(amount))),
            "ReceiverParty": self.shortcode,
            "RecieverIdentifierType": "4",
            "ResultURL": f"{self.callback_url.replace('/stk/', '/reversal/')}",
            "QueueTimeOutURL": f"{self.callback_url.replace('/stk/', '/reversal-timeout/')}",
            "Remarks": remarks[:100],
            "Occasion": "",
        }

        if self.is_mock:
            time.sleep(0.2)
            return {
                "response_code": "0",
                "response_description": "Reversal accepted",
                "raw": {"mock": True, "payload": payload},
            }

        url = f"{self.base_url}/mpesa/reversal/v1/request"
        token = self.access_token()
        try:
            r = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise DarajaError(f"Reversal failed: {exc}") from exc

        return {
            "response_code": data.get("ResponseCode", ""),
            "response_description": data.get("ResponseDescription", ""),
            "raw": data,
        }
    # ---------------- C2B Register URLs ----------------
    def c2b_register_urls(self, confirmation_url, validation_url, response_type="Completed"):
        """
        Register C2B URLs with Safaricom. Only needs to be called once
        per shortcode (or after URL changes).

        response_type: "Completed" (auto-accept) or "Cancelled".
        """
        payload = {
            "ShortCode": self.shortcode,
            "ResponseType": response_type,
            "ConfirmationURL": confirmation_url,
            "ValidationURL": validation_url,
        }

        if self.is_mock:
            return {
                "response_code": "0",
                "response_description": "Mock registration",
                "raw": {"mock": True, "payload": payload},
            }

        url = f"{self.base_url}/mpesa/c2b/v1/registerurl"
        token = self.access_token()
        try:
            r = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise DarajaError(f"C2B register failed: {exc}") from exc

        return {
            "response_code": data.get("ResponseCode", ""),
            "response_description": data.get("ResponseDescription", ""),
            "raw": data,
        }

    # ---------------- Helpers ----------------
    @staticmethod
    def _normalize_phone(phone):
        """
        Convert various Kenyan phone formats to 2547XXXXXXXX.
        Accepts:  0712345678  +254712345678  254712345678  712345678
        """
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits.startswith("254"):
            return digits
        if digits.startswith("0"):
            return "254" + digits[1:]
        if len(digits) == 9:
            return "254" + digits
        return digits


# ------------------------------------------------------------
# Singleton helper
# ------------------------------------------------------------
_client = None


def get_daraja():
    global _client
    if _client is None:
        _client = DarajaClient()
    return _client