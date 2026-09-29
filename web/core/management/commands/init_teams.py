from django.core.management.base import BaseCommand

from core.models import CompetitionConfig
from team.models import MAX_TEAMS, Team


class Command(BaseCommand):
    help = "Initialize teams in the database"

    def handle(self, *args: str, **options: object) -> None:
        created_count = 0
        updated_count = 0

        config = CompetitionConfig.get_config()
        max_members = config.max_team_members

        for team_num in range(1, MAX_TEAMS + 1):
            team_name = f"BlueTeam{team_num:02d}"
            authentik_group = f"WCComps_BlueTeam{team_num:02d}"

            _team, created = Team.objects.get_or_create(
                team_number=team_num,
                defaults={
                    "team_name": team_name,
                    "authentik_group": authentik_group,
                    "is_active": True,
                    "max_members": max_members,
                },
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created {team_name}"))
            else:
                updated_count += 1
                self.stdout.write(self.style.WARNING(f"{team_name} already exists"))

        self.stdout.write(
            self.style.SUCCESS(f"\nInitialization complete: {created_count} created, {updated_count} already existed")
        )
