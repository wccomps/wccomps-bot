from django.urls import path

from . import views

urlpatterns = [
    path("", views.team_packet, name="team_packet"),
    path("download/<int:packet_id>/", views.download_packet, name="download_packet"),
]
