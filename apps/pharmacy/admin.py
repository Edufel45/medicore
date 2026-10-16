"""
MediCore — Pharmacy Admin
============================================================
"""

from django.contrib import admin
from .models import Drug, StockBatch, Dispense


class StockBatchInline(admin.TabularInline):
    model = StockBatch
    extra = 0
    fields = ("batch_number", "quantity_received", "quantity_remaining", "expiry_date")


@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("name", "price_ksh", "unit", "reorder_level", "is_active")
    list_filter = ("is_active", "unit")
    search_fields = ("name", "aliases")
    inlines = [StockBatchInline]


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ("drug", "batch_number", "quantity_remaining", "expiry_date", "received_at")
    list_filter = ("expiry_date", "received_at")
    search_fields = ("drug__name", "batch_number")
    date_hierarchy = "expiry_date"
    readonly_fields = ("received_at",)


@admin.register(Dispense)
class DispenseAdmin(admin.ModelAdmin):
    list_display = ("dispensed_at", "batch", "quantity", "prescription", "dispensed_by")
    list_filter = ("dispensed_at",)
    search_fields = ("batch__drug__name", "prescription__visit__patient__sid")
    readonly_fields = ("dispensed_at",)