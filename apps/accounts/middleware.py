"""
MediCore — Accounts Middleware
============================================================
Audit logging middleware.

Every non-GET request made by an authenticated user is recorded
in the AuditLog table. Also extracts a patient System ID (MC-YYYY-NNNN)
from the URL path if present.

Failure safety: if writing the audit row fails for any reason
(e.g. DB is down), we log to Python console and let the request
continue. Auditing must never break the app.

Proposal reference: §2.3 "audit trails and restricted reversals".
"""

import logging
import re

logger = logging.getLogger("medicore")

# Matches MC-YYYY-NNNN anywhere in a URL
SID_PATTERN = re.compile(r"MC-\d{4}-\d{4}")

# Actions we explicitly map from URL prefix to AuditLog.action
# (everything else becomes "other")
ACTION_MAP = [
    ("/patients/register/",  "patient.register"),
    ("/patients/",           "patient.view"),
    ("/queue/",              "stage.change"),
    ("/lab/",                "stage.change"),
    ("/pharmacy/",           "stage.change"),
    ("/billing/collect/",    "payment.pharmacy"),
    ("/billing/reg/",        "payment.reg"),
    ("/sha/claim/",          "claim.create"),
    ("/receipt/",            "receipt.open"),
    ("/audit/",              "other"),
]


def _map_action(path, method):
    """Return an AuditLog action key for a given path+method."""
    for prefix, action in ACTION_MAP:
        if path.startswith(prefix):
            return action
    return "other"


class AuditMiddleware:
    """
    Writes one AuditLog row per authenticated non-GET request.
    Runs after the response is generated so it never blocks.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return response

        # Skip static/media in development
        if request.path.startswith(("/static/", "/media/", "/admin/jsi18n/")):
            return response

        # Only log state-changing methods
        if request.method == "GET":
            return response

        try:
            # Imported here to avoid circular import at app-load time
            from apps.accounts.models import AuditLog

            # Find a System ID in the URL if there is one
            sid_match = SID_PATTERN.search(request.path)
            patient_sid = sid_match.group(0) if sid_match else ""

            # Extract role from the user's profile if available
            role = ""
            try:
                role = user.profile.role
            except Exception:
                pass

            # Get client IP
            xff = request.META.get("HTTP_X_FORWARDED_FOR")
            if xff:
                ip = xff.split(",")[0].strip()
            else:
                ip = request.META.get("REMOTE_ADDR")

            AuditLog.objects.create(
                user=user,
                role=role,
                action=_map_action(request.path, request.method),
                detail=f"{request.method} {request.path}"[:500],
                ip_address=ip,
                path=request.path[:255],
                method=request.method[:8],
                patient_sid=patient_sid,
            )
        except Exception as exc:
            # Never let audit logging break the request
            logger.warning("Audit log write failed: %s", exc)

        return response