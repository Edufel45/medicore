"""
MediCore — SHA Admin
============================================================
"""

from django.contrib import admin
from .models import SHAContract, SHAClaim


@admin.register(SHAContract)
class SHAContractAdmin(admin.ModelAdmin):
    list_display = ("facility_code", "facility_name", "facility_level", "is_active", "contract_start", "contract_end")
    list_filter = ("is_active", "facility_level")
    search_fields = ("facility_code", "facility_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SHAClaim)
class SHAClaimAdmin(admin.ModelAdmin):
    list_display = ("claim_reference", "patient_name", "status", "claim_amount", "fund_type", "created_at")
    list_filter = ("status", "fund_type", "created_at")
    search_fields = ("claim_reference", "patient_name", "sha_number", "visit__patient__sid")
    readonly_fields = ("created_at", "updated_at", "submitted_at", "decided_at", "paid_at")
    date_hierarchy = "created_at"