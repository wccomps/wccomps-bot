from django.contrib.auth.models import User

from team.models import Team


def _get_user_team(user: User) -> Team | None:
    """Get team for a user based on their Authentik groups."""
    from core.auth_utils import get_user_team_number

    team_number = get_user_team_number(user)
    if not team_number:
        return None
    return Team.objects.filter(team_number=team_number).first()
