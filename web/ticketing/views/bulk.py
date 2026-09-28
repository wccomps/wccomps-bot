"""Bulk ticket operations views."""

import logging
from typing import cast

from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from core.auth_utils import has_permission
from core.models import DiscordTask
from ticketing.forms import TicketBulkActionForm
from ticketing.models import Ticket, TicketHistory
from ticketing.utils import claim_ticket_atomic, resolve_ticket_atomic

logger = logging.getLogger(__name__)


def tickets_bulk_claim(request: HttpRequest) -> HttpResponse:
    """Bulk claim tickets (ticketing staff only)."""
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    user = cast(User, request.user)
    if not has_permission(user, "ticketing_support"):
        return HttpResponse("Access denied", status=403)

    form = TicketBulkActionForm(request.POST)
    if not form.is_valid():
        return HttpResponse("No tickets selected", status=400)

    claimed = 0
    for ticket_id in Ticket.objects.filter(ticket_number__in=form.cleaned_data["ticket_numbers"]).values_list(
        "id", flat=True
    ):
        ticket, error = claim_ticket_atomic(ticket_id=ticket_id, actor_username=user.username, user=user)
        if ticket is not None and not error:
            DiscordTask.create_post_ticket_update(ticket=ticket, action="claimed", actor=user.username)
            claimed += 1

    logger.info(f"Bulk claimed {claimed} tickets by {user.username}")
    return redirect("ticket_list")


def tickets_bulk_resolve(request: HttpRequest) -> HttpResponse:
    """Bulk resolve tickets at their category's points; same rule as resolving one ticket."""
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    user = cast(User, request.user)
    if not has_permission(user, "ticketing_support"):
        return HttpResponse("Access denied", status=403)

    form = TicketBulkActionForm(request.POST)
    if not form.is_valid():
        return HttpResponse("No tickets selected", status=400)

    requested = Ticket.objects.filter(ticket_number__in=form.cleaned_data["ticket_numbers"])
    tickets = requested if has_permission(user, "ticketing_admin") else requested.filter(assigned_to=user)

    resolved = 0
    skipped = [
        f"{number} (not assigned to you)"
        for number in requested.exclude(pk__in=tickets.values("pk")).values_list("ticket_number", flat=True)
    ]
    for ticket_id, ticket_number in tickets.values_list("id", "ticket_number"):
        ticket, error = resolve_ticket_atomic(
            ticket_id=ticket_id,
            actor_username=user.username,
            resolution_notes="Bulk resolved via web interface",
            user=user,
        )
        if ticket is None or error:
            skipped.append(f"{ticket_number} ({error})")
            continue
        DiscordTask.create_post_ticket_update(
            ticket=ticket,
            action="resolved",
            actor=user.username,
            resolution_notes=ticket.resolution_notes,
            points_charged=ticket.points_charged,
        )
        resolved += 1

    if skipped:
        messages.warning(request, "Not resolved: " + "; ".join(skipped))
    logger.info(f"Bulk resolved {resolved} tickets by {user.username}")
    return redirect("ticket_list")


def tickets_clear_all(request: HttpRequest) -> HttpResponse:
    """Clear all tickets and reset counters (admin only)."""
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    user = cast(User, request.user)
    authentik_username = user.username

    if not has_permission(user, "ticketing_admin"):
        return HttpResponse("Access denied - admin only", status=403)

    from core.models import AuditLog
    from team.models import Team
    from ticketing.models import TicketAttachment, TicketComment

    # Get counts before deletion
    ticket_count = Ticket.objects.count()
    attachment_count = TicketAttachment.objects.count()
    comment_count = TicketComment.objects.count()
    history_count = TicketHistory.objects.count()
    teams_to_reset = Team.objects.filter(ticket_counter__gt=0).count()

    with transaction.atomic():
        # Delete all tickets (CASCADE handles related data)
        Ticket.objects.all().delete()

        # Reset team ticket counters
        Team.objects.filter(ticket_counter__gt=0).update(ticket_counter=0)

        # Create audit log
        AuditLog.objects.create(
            action="clear_tickets",
            admin_user=authentik_username,
            target_entity="tickets",
            target_id=0,
            details={
                "tickets_deleted": ticket_count,
                "attachments_deleted": attachment_count,
                "comments_deleted": comment_count,
                "history_deleted": history_count,
                "teams_reset": teams_to_reset,
            },
        )

    logger.info(f"Cleared all tickets ({ticket_count}) by {authentik_username}")
    return redirect("ticket_list")
