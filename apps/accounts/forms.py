"""
MediCore — Accounts Forms
============================================================
Login form with styled widgets.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm


class MediCoreLoginForm(AuthenticationForm):
    """
    Custom login form. Applies the same CSS classes used in the
    HTML prototype (Tailwind-ish) so the styling stays consistent.
    """

    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={
            "class": "form-input",
            "placeholder": "Enter your username",
            "autofocus": True,
            "autocomplete": "username",
        }),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={
            "class": "form-input",
            "placeholder": "Enter your password",
            "autocomplete": "current-password",
        }),
    )