"""
MediCore — Root URL Configuration
============================================================
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    # Accounts — login, logout, home, audit
    path("", include("apps.accounts.urls", namespace="accounts")),

    # Patients — registration + list
    path("patients/", include("apps.patients.urls", namespace="patients")),

    # Clinical — queue, visits, lab inbox
    path("", include("apps.clinical.urls", namespace="clinical")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)