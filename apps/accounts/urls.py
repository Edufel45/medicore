"""
MediCore — Accounts URLs
============================================================
Namespace: accounts
"""

from django.urls import path

from . import audit_views, views

app_name = "accounts"

urlpatterns = [
    path("login/",  views.MediCoreLoginView.as_view(), name="login"),
    path("logout/", views.logout_view,                  name="logout"),
    path("audit/",  audit_views.audit_list,             name="audit"),
    path("",        views.home,                         name="home"),
]