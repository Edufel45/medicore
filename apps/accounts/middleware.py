"""
MediCore — Accounts Middleware
============================================================
Provides audit logging middleware.

The middleware writes an AuditLog row for every non-GET request
made by an authenticated user. In Phase 3 we swap the print()
placeholder for a real model write.

Proposal reference: §2.3 "audit trails and restricted reversals".
"""

import logging

logger = logging.getLogger("medicore")


class AuditMiddleware:
    """
    Lightweight middleware that logs authenticated writes.

    Phase 1 behaviour: logs to Python logging.
    Phase 3 behaviour: writes an AuditLog row (once the model exists).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only log authenticated, state-changing requests
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and request.method != "GET":
            # Extract a patient System ID if present in the path (MC-YYYY-NNNN)
            sid = ""
            for part in request.path.split("/"):
                if part.startswith("MC-"):
                    sid = part
                    break

            logger.info(
                "AUDIT | user=%s role=%s method=%s path=%s sid=%s status=%s",
                user.username,
                self._role_of(user),
                request.method,
                request.path,
                sid or "-",
                response.status_code,
            )

        return response

    @staticmethod
    def _role_of(user):
        """Return the first Django Group name for the user, or 'anon'."""
        try:
            groups = list(user.groups.values_list("name", flat=True))
            return groups[0] if groups else "no-role"
        except Exception:
            return "unknown"