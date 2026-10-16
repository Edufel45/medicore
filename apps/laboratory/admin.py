"""
MediCore — Laboratory Admin
============================================================
"""

from django.contrib import admin
from .models import LabTest, LabResult


class LabResultInline(admin.StackedInline):
    model = LabResult
    extra = 0


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ("name", "visit", "status", "price_ksh", "ordered_at", "released_at")
    list_filter = ("status", "ordered_at")
    search_fields = ("name", "visit__queue_number", "visit__patient__sid")
    readonly_fields = ("ordered_at", "released_at")
    inlines = [LabResultInline]


@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display = ("lab_test", "value", "entered_at")
    search_fields = ("lab_test__name", "value")
    readonly_fields = ("entered_at",)