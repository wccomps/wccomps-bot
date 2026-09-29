from django.urls import path

from . import views

urlpatterns = [
    path("", views.packets_list, name="packets_list"),
    path("upload/", views.upload_packet, name="upload_packet"),
    path("<int:packet_id>/", views.packet_detail, name="packet_detail"),
    path("<int:packet_id>/action/", views.packet_action, name="packet_action"),
    path("<int:packet_id>/resend/<int:team_id>/", views.packet_resend_team, name="packet_resend_team"),
]
