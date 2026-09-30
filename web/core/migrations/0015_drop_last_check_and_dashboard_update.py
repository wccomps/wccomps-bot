from django.db import migrations


class Migration(migrations.Migration):
    """Drop what 0012 and 0013 removed from the model state but kept for pods on the previous release,
    which read and wrote them during that rollout. No code reads them now."""

    dependencies = [
        ("core", "0014_discordtask_sync_member_roles"),
    ]

    operations = [
        migrations.RunSQL("ALTER TABLE core_competitionconfig DROP COLUMN IF EXISTS last_check", migrations.RunSQL.noop),
        migrations.RunSQL("DROP TABLE IF EXISTS core_dashboardupdate", migrations.RunSQL.noop),
    ]
