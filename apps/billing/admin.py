"""
MediCore — Billing Admin
============================================================
"""

from django.contrib import admin
from .models import Bill, LineItem, Payment, Receipt


class LineItemInline(admin.TabularInline):
    model = LineItem
    extra = 0
    fields = ("kind", "label", "amount", "created_at")
    readonly_fields = ("created_at",)


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("method", "amount", "status", "mpesa_receipt", "initiated_at")
    readonly_fields = ("initiated_at",)


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("id", "visit", "status", "total_amount", "paid_amount", "sha_covered_amount", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("visit__queue_number", "visit__patient__sid", "visit__patient__full_name")
    readonly_fields = ("created_at", "updated_at", "settled_at")
    date_hierarchy = "created_at"
    inlines = [LineItemInline, PaymentInline]


@admin.register(LineItem)
class LineItemAdmin(admin.ModelAdmin):
    list_display = ("bill", "kind", "label", "amount", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("label", "bill__visit__patient__sid")
    readonly_fields = ("created_at",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "bill", "method", "amount", "status", "mpesa_receipt", "initiated_at")
    list_filter = ("method", "status", "initiated_at")
    search_fields = ("mpesa_receipt", "bill__visit__patient__sid")
    readonly_fields = ("initiated_at", "confirmed_at")


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "bill", "total_paid", "sha_covered", "issued_at")
    list_filter = ("issued_at",)
    search_fields = ("receipt_number", "bill__visit__patient__sid")
    readonly_fields = ("issued_at",)