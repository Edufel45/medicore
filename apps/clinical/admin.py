"""
MediCore — Clinical Admin
============================================================
"""

from django.contrib import admin
from .models import Visit, Vitals, Diagnosis, Prescription


class VitalsInline(admin.StackedInline):
    model = Vitals
    extra = 0


class DiagnosisInline(admin.StackedInline):
    model = Diagnosis
    extra = 0


class PrescriptionInline(admin.TabularInline):
    model = Prescription
    extra = 0
    fields = ("drug_name_typed", "drug", "is_dispensed")


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("queue_number", "patient", "stage", "registration_fee_paid", "started_at")
    list_filter = ("stage", "registration_fee_paid", "started_at")
    search_fields = ("queue_number", "patient__sid", "patient__full_name", "patient__national_id")
    readonly_fields = ("started_at", "updated_at", "completed_at")
    date_hierarchy = "started_at"
    ordering = ("-started_at",)
    inlines = [VitalsInline, DiagnosisInline, PrescriptionInline]


@admin.register(Vitals)
class VitalsAdmin(admin.ModelAdmin):
    list_display = ("visit", "blood_pressure", "temperature_c", "weight_kg", "pulse_bpm", "recorded_at")
    search_fields = ("visit__queue_number", "visit__patient__sid")
    readonly_fields = ("recorded_at", "updated_at")


@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ("visit", "text", "recorded_at")
    search_fields = ("visit__queue_number", "text")
    readonly_fields = ("recorded_at", "updated_at")


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("drug_name_typed", "visit", "drug", "is_dispensed", "prescribed_at")
    list_filter = ("is_dispensed", "prescribed_at")
    search_fields = ("drug_name_typed", "visit__queue_number", "visit__patient__sid")
    readonly_fields = ("prescribed_at",)