from django.db import migrations

DONE_STATUSES = ("sent", "delivered")


def complete_finished_packets(apps, schema_editor):
    """Packets whose every email went out but whose stream was cut before it could say so."""
    Packet = apps.get_model("packets", "Packet")
    for packet in Packet.objects.filter(status="distributing"):
        distributions = packet.distributions.all()
        if distributions.exists() and not distributions.exclude(email_status__in=DONE_STATUSES).exists():
            packet.status = "completed"
            packet.save(update_fields=["status"])


class Migration(migrations.Migration):
    dependencies = [
        ("packets", "0006_alter_packet_options"),
    ]

    operations = [migrations.RunPython(complete_finished_packets, migrations.RunPython.noop)]
