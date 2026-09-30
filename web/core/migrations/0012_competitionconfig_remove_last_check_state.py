from django.db import migrations


class Migration(migrations.Migration):
    """Forget last_check but keep its column: pods still running the previous release select and write it
    during a rolling deploy. A later release drops the column."""

    dependencies = [
        ("core", "0011_discordtask_drop_unused_types"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="competitionconfig", name="last_check"),
            ],
        ),
    ]
