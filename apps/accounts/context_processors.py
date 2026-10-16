"""
MediCore — Accounts Context Processors
============================================================
Injects global template context so every rendered page has:

  - current_role        (string: "reception" | "nurse" | "doctor" | ...)
  - current_role_title  (human-readable: "Receptionist", "Doctor", ...)
  - current_role_color  (hex color for the rail tint)
  - nav_items           (list of {key, label, icon} for the rail)
  - user_theme          (string: "light" | "dark")

This keeps base.html and the rail template free of hard-coded role logic.

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

# Human-readable titles
ROLE_TITLES = {
    "reception": "Receptionist",
    "nurse": "Nurse",
    "doctor": "Doctor",
    "lab": "Lab technician",
    "pharmacy": "Pharmacist",
    "accounts": "Accountant",
}

# Role colors (matches the HTML prototype)
ROLE_COLORS = {
    "reception": "#1664B0",
    "nurse": "#7c3aed",
    "doctor": "#0FA68A",
    "lab": "#B76E00",
    "pharmacy": "#0b8a73",
    "accounts": "#64748b",
}

# Navigation items per role — mirrors the HTML prototype.
# Only include links whose URL routes actually exist.
# Missing routes are hidden until the relevant phase.
NAV_BY_ROLE = {
    "reception": [
        {"key": "",         "label": "Home",     "icon": "🏠"},
        {"key": "queue",    "label": "Queue",    "icon": "🧭"},
        {"key": "patients", "label": "Patients", "icon": "👥"},
    ],
    "nurse": [
        {"key": "",         "label": "Home",     "icon": "🏠"},
        {"key": "queue",    "label": "Queue",    "icon": "🧭"},
        {"key": "patients", "label": "Patients", "icon": "👥"},
    ],
    "doctor": [
        {"key": "",         "label": "Home",      "icon": "🏠"},
        {"key": "queue",    "label": "Queue",     "icon": "🧭"},
        {"key": "lab/inbox","label": "Lab inbox", "icon": "📥"},
        {"key": "patients", "label": "Patients",  "icon": "👥"},
    ],
    "lab": [
        {"key": "",         "label": "Home",     "icon": "🏠"},
        {"key": "queue",    "label": "Queue",    "icon": "🧭"},
        {"key": "patients", "label": "Patients", "icon": "👥"},
    ],
    "pharmacy": [
        {"key": "",         "label": "Home",     "icon": "🏠"},
        {"key": "queue",    "label": "Queue",    "icon": "🧭"},
        {"key": "patients", "label": "Patients", "icon": "👥"},
    ],
    "accounts": [
        {"key": "",         "label": "Home",     "icon": "🏠"},
        {"key": "claims",   "label": "Claims",   "icon": "📋"},
        {"key": "billing/ledger", "label": "Ledger", "icon": "📒"},
        {"key": "audit",    "label": "Audit",    "icon": "🕵️"},
        {"key": "patients", "label": "Patients", "icon": "👥"},
    ],
}


def role_context(request):
    """
    Return template context for the current user.
    Safe for anonymous users — returns empty/guest values.
    """
    ctx = {
        "current_role": None,
        "current_role_title": "",
        "current_role_color": "#0FA68A",
        "nav_items": [],
        "user_theme": "light",
    }

    # Get user
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ctx

    # Determine role from the user's first Django Group
    role_key = None
    try:
        group_names = list(user.groups.values_list("name", flat=True))
        for g in group_names:
            if g in GROUP_TO_ROLE:
                role_key = GROUP_TO_ROLE[g]
                break
    except Exception:
        role_key = None

    # Superuser without group -> treat as accountant (safe read-only view)
    if role_key is None and user.is_superuser:
        role_key = "accounts"

    ctx["current_role"] = role_key
    ctx["current_role_title"] = ROLE_TITLES.get(role_key, "Guest")
    ctx["current_role_color"] = ROLE_COLORS.get(role_key, "#0FA68A")
    ctx["nav_items"] = NAV_BY_ROLE.get(role_key, [])
    ctx["user_theme"] = getattr(user, "theme_preference", None) or "light"

    return ctx