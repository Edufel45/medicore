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

Also seeds:
  - Drug catalogue with common Kenyan-market drugs and prices
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

DRUG_SEED = [
    # (name, price_ksh, unit)
    ("artemether-lumefantrine", 250, "unit"),
    ("azithromycin 250mg",      350, "unit"),
    ("azithromycin 500mg",      500, "unit"),
    ("amoxicillin 250mg",       100, "unit"),
    ("amoxicillin 500mg",       150, "unit"),
    ("paracetamol 500mg",        20, "unit"),
    ("paracetamol 1g",           30, "unit"),
    ("ibuprofen 400mg",          60, "unit"),
    ("diclofenac 50mg",          30, "unit"),
    ("metronidazole 400mg",     100, "unit"),
    ("doxycycline 100mg",       150, "unit"),
    ("cotrimoxazole",            90, "unit"),
    ("omeprazole 20mg",         150, "unit"),
    ("cetirizine 10mg",          70, "unit"),
    ("ferrous sulphate",         60, "unit"),
    ("folic acid 5mg",           30, "unit"),
    ("amlodipine 5mg",          100, "unit"),
    ("enalapril 10mg",          120, "unit"),
    ("metformin 500mg",          80, "unit"),
    ("glibenclamide 5mg",        60, "unit"),
    ("salbutamol inhaler",      500, "unit"),
    ("prednisolone 5mg",         40, "unit"),
    ("ceftriaxone 1g",          300, "vial"),
    ("hydralazine 25mg",        100, "unit"),
]


class Command(BaseCommand):
    help = "Seed demo users, groups, and drug catalogue for MediCore."

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

        # ------------------------------------------------------------
        # Drug catalogue — seed common drugs with prices
        # ------------------------------------------------------------
        from apps.pharmacy.models import Drug

        drugs_created = 0
        drugs_existing = 0
        for name, price, unit in DRUG_SEED:
            _, was_created = Drug.objects.get_or_create(
                name=name,
                defaults={
                    "price_ksh": price,
                    "unit": unit,
                    "is_active": True,
                },
            )
            if was_created:
                drugs_created += 1
            else:
                drugs_existing += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[DRUGS] {drugs_created} created · {drugs_existing} already existed"
            )
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("✅ Demo users seeded."))
        self.stdout.write("")
        self.stdout.write("Login credentials (all users share the same password):")
        self.stdout.write("  Password: medicore123")
        for _, username, _, _ in ROLES:
            self.stdout.write(f"  Username: {username}")