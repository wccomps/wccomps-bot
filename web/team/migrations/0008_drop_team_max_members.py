from django.db import migrations


class Migration(migrations.Migration):
    """Drop the team_team.max_members column 0007 removed from the model state but kept for pods on the
    previous release during that rollout. The limit is CompetitionConfig.max_team_members."""

    dependencies = [
        ("team", "0007_team_max_members_to_config"),
    ]

    operations = [
        migrations.RunSQL("ALTER TABLE team_team DROP COLUMN IF EXISTS max_members", migrations.RunSQL.noop),
    ]
