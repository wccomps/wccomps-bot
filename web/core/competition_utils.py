"""Shared competition utilities."""

from orange_team.models import OrangeAssignment, OrangeCheckIn
from scoring.models import (
    FinalScore,
    IncidentReport,
    IncidentScreenshot,
    InjectScore,
    OrangeTeamScore,
    RedTeamScore,
    RedTeamScreenshot,
    ServiceDetail,
    ServiceScore,
)

from team.models import DiscordLink
from ticketing.models import Ticket, TicketAttachment, TicketComment, TicketHistory


def wipe_competition_data() -> dict[str, int]:
    """
    Wipe competition data for a fresh start.

    Deletes:
    - Scoring data (findings, incidents, inject grades, orange scores, service and final scores)
    - Orange team assignments (with their results and follow-ups) and check-ins
    - All tickets and history
    - Blue team Discord links (staff/volunteer links preserved)

    Preserves:
    - Teams (static config, always BlueTeam01-50)
    - Orange checks (reusable rubrics)
    - AuditLog (compliance/history)
    - BotState (dashboard message IDs, etc.)
    - DiscordTask (task history)
    - LinkToken/LinkAttempt (harmless)

    Returns dict of model names to deleted counts.
    """
    counts = {
        # Ticketing
        "TicketAttachment": TicketAttachment.objects.all().delete()[0],
        "TicketComment": TicketComment.objects.all().delete()[0],
        "TicketHistory": TicketHistory.objects.all().delete()[0],
        "Ticket": Ticket.objects.all().delete()[0],
        # Scoring
        "RedTeamScreenshot": RedTeamScreenshot.objects.all().delete()[0],
        "IncidentScreenshot": IncidentScreenshot.objects.all().delete()[0],
        "IncidentReport": IncidentReport.objects.all().delete()[0],
        "RedTeamScore": RedTeamScore.objects.all().delete()[0],
        "InjectScore": InjectScore.objects.all().delete()[0],
        "OrangeTeamScore": OrangeTeamScore.objects.all().delete()[0],
        "ServiceDetail": ServiceDetail.objects.all().delete()[0],
        "ServiceScore": ServiceScore.objects.all().delete()[0],
        "FinalScore": FinalScore.objects.all().delete()[0],
        "OrangeAssignment": OrangeAssignment.objects.all().delete()[0],
        "OrangeCheckIn": OrangeCheckIn.objects.all().delete()[0],
        # Blue team Discord links only (staff/volunteer links preserved)
        "BlueTeamDiscordLink": DiscordLink.objects.filter(team__isnull=False).delete()[0],
    }
    return counts
