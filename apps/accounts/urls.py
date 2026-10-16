"""
MediCore — Accounts URLs
============================================================
Namespace: accounts
"""

from django.urls import path

from . import audit_views, views

app_name = "accounts"

urlpatterns = [
    path("login/",   views.MediCoreLoginView.as_view(), name="login"),
    path("logout/",  views.logout_view,                 name="logout"),
    path("audit/",   audit_views.audit_list,            name="audit"),
    path("audit/export/", audit_views.audit_export_csv, name="audit_export"),
    path("",         views.home,                        name="home"),
]