"""
MediCore — Patients Forms
============================================================
PatientForm     : register a new patient
VisitForm       : start a visit (reason + initial context)

Both forms apply CSS classes so widgets match the prototype.
"""

from django import forms
from django.core.exceptions import ValidationError

from .models import Patient


# Widget class we'll reuse
INPUT_CLASSES = "w-full px-3 py-2 border rounded-lg text-sm"


def _style(field, placeholder="", extra=None):
    """Apply consistent classes to a widget."""
    css = extra or "form-input"
    field.widget.attrs.update({"class": css})
    if placeholder:
        field.widget.attrs["placeholder"] = placeholder
    return field


# ------------------------------------------------------------
# PatientForm
# ------------------------------------------------------------
class PatientForm(forms.ModelForm):
    """
    Register a new patient.

    Additional validation:
      - Rejects National IDs / phones that already belong to an
        ACTIVE patient (soft — the DB constraint is the final guard).
      - SHA number is optional but required if you want to verify.
    """

    class Meta:
        model = Patient
        fields = [
            "full_name",
            "national_id",
            "phone",
            "sex",
            "date_of_birth",
            "age_years",
            "sha_number",
            "reason_for_visit",
        ]
        widgets = {
            "full_name":      forms.TextInput(attrs={"autocomplete": "name"}),
            "national_id":    forms.TextInput(attrs={"inputmode": "numeric"}),
            "phone":          forms.TextInput(attrs={"inputmode": "tel"}),
            "sex":            forms.Select(),
            "date_of_birth":  forms.DateInput(attrs={"type": "date"}),
            "age_years":      forms.NumberInput(attrs={"min": 0, "max": 120}),
            "sha_number":     forms.TextInput(attrs={"autocomplete": "off"}),
            "reason_for_visit": forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply styles + placeholders
        _style(self.fields["full_name"],       "Enter patient's full name")
        _style(self.fields["national_id"],     "e.g. 12345678")
        _style(self.fields["phone"],           "e.g. 0712345678")
        _style(self.fields["date_of_birth"],   "")
        _style(self.fields["age_years"],       "Age in years (if DOB unknown)")
        _style(self.fields["sha_number"],      "Try DEMO-1001, DEMO-2002, DEMO-3003")
        _style(self.fields["reason_for_visit"], "e.g. Fever and headache for 3 days")

    # ---------------- Validation ----------------
    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        # Duplicate check against ACTIVE patients
        dup = Patient.objects.filter(national_id=nid, is_active=True)
        if self.instance.pk:
            dup = dup.exclude(pk=self.instance.pk)
        if dup.exists():
            raise ValidationError(
                f"An active patient already has National ID {nid} "
                f"({dup.first().sid} · {dup.first().full_name})."
            )
        return nid

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        dup = Patient.objects.filter(phone=phone, is_active=True)
        if self.instance.pk:
            dup = dup.exclude(pk=self.instance.pk)
        if dup.exists():
            raise ValidationError(
                f"An active patient already uses phone {phone} "
                f"({dup.first().sid} · {dup.first().full_name})."
            )
        return phone

    def clean(self):
        data = super().clean()
        dob = data.get("date_of_birth")
        age = data.get("age_years")

        if not dob and age is None:
            raise ValidationError(
                "Provide either date of birth or an age (in years)."
            )

        sha = (data.get("sha_number") or "").strip().upper()
        if sha:
            data["sha_number"] = sha
        return data


# ------------------------------------------------------------
# VisitForm
# ------------------------------------------------------------
class VisitForm(forms.Form):
    """
    A tiny form attached to the registration flow.
    We don't use a ModelForm here because Visit requires a Patient
    FK that is only known after the patient is created.
    """

    reason_for_visit = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "form-input",
            "placeholder": "Reason for visit (optional)",
        }),
    )
    collect_registration_fee = forms.BooleanField(
        required=False,
        initial=True,
        label="Collect Ksh 200 registration fee now (M-Pesa)",
    )