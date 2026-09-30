from django.db import migrations, models


class Migration(migrations.Migration):
    """Drop Team.max_members from the model; the limit is CompetitionConfig.max_team_members.

    The column stays for now with a database default, so pods still on the previous release can read
    and write it during a rolling deploy while new code inserts teams without it. A later release
    drops the column.
    """

    dependencies = [
        ("team", "0006_migrate_helper_data"),
    ]

    operations = [
        migrations.AlterField(
            model_name="team",
            name="max_members",
            field=models.IntegerField(default=10, db_default=10),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="team", name="max_members"),
            ],
        ),
    ]
