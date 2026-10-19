"""
MediCore — Accounts Context Processors
============================================================
Injects global template context so every rendered page has:
  - current_role, current_role_title, current_role_color
  - nav_items, user_theme
  - notifications (per-role counts)

Proposal reference: §2.3 "role-based access control for Admin, Doctor,
Nurse, Pharmacist, Cashier, and SHA Clerk".
"""

# Map Django Group name -> role key used by the frontend
GROUP_TO_ROLE = {
    "Receptionist": "reception",
    "Nurse": "nurse",
    "Doctor": "doctor",
    "Lab Technician": "lab",
    "Pharmacist": "pharmacy",
    "Accountant": "accounts",
}

ROLE_TITLES = {
    "reception": "Receptionist",
    "nurse": "Nurse",
    "doctor": "Doctor",
    "lab": "Lab technician",
    "pharmacy": "Pharmacist",
    "accounts": "Accountant",
}

ROLE_COLORS = {
    "reception": "#1664B0",
    "nurse": "#7c3aed",
    "doctor": "#0FA68A",
    "lab": "#B76E00",
    "pharmacy": "#0b8a73",
    "accounts": "#64748b",
}

NAV_BY_ROLE = {
    "reception": [
        {"key": "",         "label": "Home",     "icon": "🏠"},
        {"key": "queue",    "label": "Queue",    "icon": "🧭"},
        {"key": "patients", "label": "Patients", "icon": "👥"},
    ],
    "nurse": [
        {"key": "nurse/lookup", "label": "Lookup",   "icon": "🔍"},
        {"key": "queue",        "label": "Queue",    "icon": "🧭"},
        {"key": "patients",     "label": "Patients", "icon": "👥"},
    ],
    "doctor": [
        {"key": "doctor/lookup", "label": "Lookup",    "icon": "🔍"},
        {"key": "queue",         "label": "Queue",     "icon": "🧭"},
        {"key": "lab/inbox",     "label": "Lab inbox", "icon": "📥"},
        {"key": "patients",      "label": "Patients",  "icon": "👥"},
    ],
    "lab": [
        {"key": "lab/lookup", "label": "Lookup",   "icon": "🔍"},
        {"key": "queue",      "label": "Queue",    "icon": "🧭"},
        {"key": "patients",   "label": "Patients", "icon": "👥"},
    ],
    "pharmacy": [
        {"key": "pharmacy/lookup", "label": "Lookup",   "icon": "🔍"},
        {"key": "queue",           "label": "Queue",    "icon": "🧭"},
        {"key": "patients",        "label": "Patients", "icon": "👥"},
    ],
    "accounts": [
        {"key": "",                   "label": "Home",     "icon": "🏠"},
        {"key": "sha/claims",         "label": "Claims",   "icon": "📋"},
        {"key": "billing/ledger",     "label": "Ledger",   "icon": "📒"},
        {"key": "mpesa/transactions", "label": "M-Pesa",   "icon": "📱"},
        {"key": "audit",              "label": "Audit",    "icon": "🕵️"},
        {"key": "patients",           "label": "Patients", "icon": "👥"},
    ],
}


def role_context(request):
    """Injects role, nav, theme into every template."""
    ctx = {
        "current_role": None,
        "current_role_title": "",
        "current_role_color": "#0FA68A",
        "nav_items": [],
        "user_theme": "light",
    }

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ctx

    role_key = None
    try:
        group_names = list(user.groups.values_list("name", flat=True))
        for g in group_names:
            if g in GROUP_TO_ROLE:
                role_key = GROUP_TO_ROLE[g]
                break
    except Exception:
        role_key = None

    if role_key is None and user.is_superuser:
        role_key = "accounts"

    ctx["current_role"] = role_key
    ctx["current_role_title"] = ROLE_TITLES.get(role_key, "Guest")
    ctx["current_role_color"] = ROLE_COLORS.get(role_key, "#0FA68A")
    ctx["nav_items"] = NAV_BY_ROLE.get(role_key, [])
    ctx["user_theme"] = getattr(user, "theme_preference", None) or "light"

    return ctx


def notification_counts(request):
    """
    Injects `notifications` into template context.

    Wrapped in try/except so a bug in this processor NEVER takes
    down the whole app. If anything fails, returns an empty
    notification set and logs the error.
    """
    import logging

    empty = {
        "notifications": {
            "pending": 0,
            "lab_ready": 0,
            "low_stock": 0,
            "unpriced": 0,
            "total": 0,
            "items": [],
        }
    }

    try:
        from apps.clinical.models import Prescription, Visit
        from apps.laboratory.models import LabTest
        from apps.pharmacy.models import Drug

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return empty

        try:
            profile = user.profile
            role = profile.role if profile else None
        except Exception:
            role = None

        if role is None:
            return empty

        pending = 0
        lab_ready = 0
        low_stock = 0
        unpriced = 0
        items = []

        if role == "reception":
            pending = Visit.objects.filter(stage="reg").count()
            if pending:
                items.append({
                    "icon": "🖥️",
                    "label": f"{pending} awaiting registration",
                    "url": "/queue/",
                })
        elif role == "nurse":
            pending = Visit.objects.filter(stage="nurse").count()
            if pending:
                items.append({
                    "icon": "🩺",
                    "label": f"{pending} awaiting vitals",
                    "url": "/queue/",
                })
        elif role == "doctor":
            pending = Visit.objects.filter(stage="doctor").count()
            if pending:
                items.append({
                    "icon": "🧑‍⚕️",
                    "label": f"{pending} awaiting consultation",
                    "url": "/queue/",
                })
            lab_ready = (
                Visit.objects
                .filter(stage="doctor", lab_tests__status="released")
                .distinct()
                .count()
            )
            if lab_ready:
                items.append({
                    "icon": "📥",
                    "label": f"{lab_ready} lab result{'s' if lab_ready != 1 else ''} ready",
                    "url": "/lab/inbox/",
                })
        elif role == "lab":
            pending = Visit.objects.filter(stage="lab").count()
            if pending:
                items.append({
                    "icon": "🔬",
                    "label": f"{pending} test{'s' if pending != 1 else ''} waiting",
                    "url": "/queue/",
                })
        elif role == "pharmacy":
            pending = Visit.objects.filter(stage="pharmacy").count()
            if pending:
                items.append({
                    "icon": "💊",
                    "label": f"{pending} prescription{'s' if pending != 1 else ''} to fill",
                    "url": "/queue/",
                })
            low_drugs = [
                d for d in Drug.objects.filter(is_active=True)
                if d.total_stock <= d.reorder_level
            ]
            low_stock = len(low_drugs)
            if low_stock:
                items.append({
                    "icon": "⚠️",
                    "label": f"{low_stock} drug{'s' if low_stock != 1 else ''} low on stock",
                    "url": "/queue/",
                })
            unpriced = (
                Prescription.objects
                .filter(visit__stage="pharmacy", drug__isnull=True)
                .count()
            )
            if unpriced:
                items.append({
                    "icon": "💰",
                    "label": f"{unpriced} unpriced drug{'s' if unpriced != 1 else ''}",
                    "url": "/queue/",
                })
        elif role == "accounts":
            from apps.sha.models import SHAClaim
            pending_claims = SHAClaim.objects.filter(
                status__in=["draft", "pending", "under_review"]
            ).count()
            if pending_claims:
                items.append({
                    "icon": "📋",
                    "label": f"{pending_claims} claim{'s' if pending_claims != 1 else ''} pending",
                    "url": "/sha/claims/",
                })
            pending = pending_claims

        total = pending + lab_ready + unpriced

        return {
            "notifications": {
                "pending": pending,
                "lab_ready": lab_ready,
                "low_stock": low_stock,
                "unpriced": unpriced,
                "total": total,
                "items": items,
            }
        }

    except Exception:
        logging.getLogger("medicore").exception(
            "notification_counts failed — returning empty"
        )
        return empty