"""
MediCore — Accounts Admin
============================================================
"""

from django.contrib import admin
from .models import UserProfile, AuditLog


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "phone", "theme_preference", "updated_at")
    list_filter = ("role", "theme_preference")
    search_fields = ("user__username", "user__email", "phone")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "role", "action", "patient_sid", "method", "path")
    list_filter = ("action", "role", "created_at")
    search_fields = ("patient_sid", "detail", "user__username", "path")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        # Audit log is append-only via the app — not editable in admin
        return False

    def has_change_permission(self, request, obj=None):
        return False