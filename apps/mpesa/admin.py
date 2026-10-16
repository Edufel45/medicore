"""
MediCore — M-Pesa Admin
============================================================
"""

from django.contrib import admin
from .models import MPesaTransaction, CallbackLog


@admin.register(MPesaTransaction)
class MPesaTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "transaction_type", "status", "amount", "phone", "mpesa_receipt", "initiated_at")
    list_filter = ("transaction_type", "status", "initiated_at")
    search_fields = ("mpesa_receipt", "checkout_request_id", "merchant_request_id", "phone", "account_reference")
    readonly_fields = ("initiated_at", "completed_at", "updated_at")
    date_hierarchy = "initiated_at"


@admin.register(CallbackLog)
class CallbackLogAdmin(admin.ModelAdmin):
    list_display = ("received_at", "kind", "processed", "matched_transaction", "ip_address")
    list_filter = ("kind", "processed", "received_at")
    search_fields = ("processing_note", "ip_address")
    readonly_fields = ("received_at", "payload")
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False