"""
MediCore — Accounts Signals
============================================================
When a new Django User is created, we automatically create a
matching UserProfile. Without this, every admin user would need
a manual UserProfile row.

Also wires Group membership -> UserProfile.role so the two stay
in sync.
"""

from django.contrib.auth.models import Group, User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile


# Map Django Group name -> UserProfile.role value
GROUP_TO_ROLE = {
    "Receptionist":   "reception",
    "Nurse":          "nurse",
    "Doctor":         "doctor",
    "Lab Technician": "lab",
    "Pharmacist":     "pharmacy",
    "Accountant":     "accounts",
}


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create a UserProfile whenever a User is created."""
    if created:
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={"role": "reception"},
        )
    else:
        # Ensure the profile exists even if the user was created
        # before we added the signal
        UserProfile.objects.get_or_create(user=instance)