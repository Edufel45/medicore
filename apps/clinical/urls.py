"""
MediCore — Clinical URLs
============================================================
Namespace: clinical
"""

from django.urls import path

from . import views

app_name = "clinical"

urlpatterns = [
    # Queue + workbench
    path("queue/",
         views.queue_board, name="queue"),
    path("visits/<str:queue_number>/",
         views.visit_detail, name="visit_detail"),

    # Nurse
    path("visits/<str:queue_number>/vitals/",
         views.save_vitals, name="save_vitals"),

    # Doctor — routing
    path("visits/<str:queue_number>/request-vitals/",
         views.request_vitals, name="request_vitals"),
    path("visits/<str:queue_number>/send-to-lab/",
         views.send_to_lab, name="send_to_lab"),
    path("visits/<str:queue_number>/send-to-pharmacy/",
         views.send_to_pharmacy, name="send_to_pharmacy"),

    # Doctor — consultation
    path("visits/<str:queue_number>/save-diagnosis/",
         views.save_diagnosis, name="save_diagnosis"),
    path("visits/<str:queue_number>/add-rx/",
         views.add_prescription, name="add_prescription"),
    path("visits/<str:queue_number>/remove-rx/<int:prescription_id>/",
         views.remove_prescription, name="remove_prescription"),
    path("visits/<str:queue_number>/add-lab/",
         views.add_lab_order, name="add_lab_order"),

    # Lab
    path("visits/<str:queue_number>/release-lab/",
         views.release_lab_results, name="release_lab_results"),
    path("lab/inbox/",
         views.lab_inbox, name="lab_inbox"),

    # Pharmacy
    path("visits/<str:queue_number>/price-rx/<int:prescription_id>/",
         views.price_prescription, name="price_prescription"),
    path("visits/<str:queue_number>/collect/",
         views.collect_payment, name="collect_payment"),
]