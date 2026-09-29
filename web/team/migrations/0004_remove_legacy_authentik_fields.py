"""Remove legacy authentik_username and authentik_user_id fields from DiscordLink.

Their values are read from user.username and user.usergroups.authentik_id instead.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("team", "0003_discordlink_user_not_null"),
    ]

    operations = [
        # Remove the index on authentik_user_id first
        migrations.RemoveIndex(
            model_name="discordlink",
            name="team_discor_authent_81456e_idx",
        ),
        # Remove the fields
        migrations.RemoveField(
            model_name="discordlink",
            name="authentik_username",
        ),
        migrations.RemoveField(
            model_name="discordlink",
            name="authentik_user_id",
        ),
    ]
