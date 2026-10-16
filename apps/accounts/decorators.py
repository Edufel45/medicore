"""
MediCore — Accounts Decorators
============================================================
Role-based access control for views.

Usage:
    from apps.accounts.decorators import role_required

    @role_required("doctor")
    def consultation(request, visit_id):
        ...

    @role_required("reception", "accounts")  # multiple roles
    def shared_view(request):
        ...

    @role_required("doctor", raise_exception=False)
    def soft_gate(request):
        # Redirects to home instead of raising 403
        ...

The decorator reads the user's Group membership. If the user
has no matching group, they see a 403 (or get redirected).

Proposal reference:
  - §2.3 "role-based access control for Admin, Doctor, Nurse,
    Pharmacist, Cashier, and SHA Clerk"
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


# Map role key (as used in views) -> Django Group name
ROLE_TO_GROUP = {
    "reception": "Receptionist",
    "nurse":     "Nurse",
    "doctor":    "Doctor",
    "lab":       "Lab Technician",
    "pharmacy":  "Pharmacist",
    "accounts":  "Accountant",
}


def user_has_role(user, *role_keys):
    """
    Return True if the user is authenticated and belongs to at
    least one of the given role groups.
    Superusers always pass.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    group_names = set(user.groups.values_list("name", flat=True))
    wanted = {ROLE_TO_GROUP.get(r, r) for r in role_keys}
    return bool(group_names & wanted)


def role_required(*role_keys, raise_exception=True):
    """
    Decorator. Accepts one or more role keys:
        "reception", "nurse", "doctor", "lab", "pharmacy", "accounts"

    Behaviour:
      - Anonymous user        -> redirect to login page
      - Authenticated, no role -> 403 (or redirect to home if
                                  raise_exception=False)
      - Authenticated, correct role -> view runs normally
    """
    if not role_keys:
        raise ValueError("role_required() requires at least one role key.")

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            # Anonymous -> send to login
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())

            # Authenticated but wrong role
            if not user_has_role(request.user, *role_keys):
                if raise_exception:
                    raise PermissionDenied(
                        "Your role does not permit access to this page."
                    )
                return redirect("accounts:home")

            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator