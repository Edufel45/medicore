"""
MediCore — Accounts AppConfig
============================================================
Loads signals when the app is ready.
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Accounts (Users & Audit)"

    def ready(self):
        # Register signals
        from . import signals  # noqa: F401