"""
MediCore — seed_demo management command
============================================================
Creates demo users and groups for the 6 roles.

Usage:
    python manage.py seed_demo            # create / update demo users
    python manage.py seed_demo --reset    # also reset their passwords
    python manage.py seed_demo --wipe     # DELETE demo users first

Groups created:
    Receptionist, Nurse, Doctor, Lab Technician, Pharmacist, Accountant

Users created (password: medicore123):
    reception, nurse, doctor, lab, pharmacy, accounts

Each user is a Django User with:
  - Group membership -> drives backend permissions
  - UserProfile.role -> drives frontend rail navigation
"""

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import UserProfile


ROLES = [
    # (group name,         username,   first name,  role key)
    ("Receptionist",       "reception", "Reception", "reception"),
    ("Nurse",              "nurse",     "Nurse",     "nurse"),
    ("Doctor",             "doctor",    "Doctor",    "doctor"),
    ("Lab Technician",     "lab",       "Lab",       "lab"),
    ("Pharmacist",         "pharmacy",  "Pharmacy",  "pharmacy"),
    ("Accountant",         "accounts",  "Accounts",  "accounts"),
]

DEFAULT_PASSWORD = "medicore123"


class Command(BaseCommand):
    help = "Seed demo users and groups for all 6 MediCore roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Reset passwords for existing demo users.",
        )
        parser.add_argument(
            "--wipe",
            action="store_true",
            help="Delete demo users before creating them.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        reset = options["reset"]
        wipe = options["wipe"]

        if wipe:
            usernames = [row[1] for row in ROLES]
            deleted, _ = User.objects.filter(username__in=usernames).delete()
            self.stdout.write(self.style.WARNING(f"Wiped {deleted} demo user row(s)."))

        for group_name, username, first_name, role_key in ROLES:
            # 1. Ensure the group exists
            group, _ = Group.objects.get_or_create(name=group_name)

            # 2. Ensure the user exists
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "email": f"{username}@medicore.local",
                    "is_staff": False,
                    "is_active": True,
                },
            )

            # 3. Password
            if created or reset:
                user.set_password(DEFAULT_PASSWORD)
                user.save()

            # 4. Group membership
            user.groups.add(group)

            # 5. UserProfile role
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = role_key
            profile.save()

            # Report
            state = "CREATED" if created else "EXISTS"
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{state}] {username:10s} · role={role_key:10s} · group={group_name}"
                )
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("✅ Demo users seeded."))
        self.stdout.write("")
        self.stdout.write("Login credentials (all users share the same password):")
        self.stdout.write("  Password: medicore123")
        for _, username, _, _ in ROLES:
            self.stdout.write(f"  Username: {username}")