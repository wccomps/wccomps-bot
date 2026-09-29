"""Ticket creation view."""

import contextlib
import logging
from typing import cast

from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from core.auth_utils import get_authentik_groups, has_permission
from core.models import DiscordTask
from core.tickets_config import get_all_categories
from core.utils import get_team_from_groups
from team.models import Team
from ticketing.forms import CreateTicketForm
from ticketing.models import TicketCategory

logger = logging.getLogger(__name__)


def create_ticket(request: HttpRequest) -> HttpResponse:
    """Create a new support ticket (web form alternative to Discord command)."""
    # Get user's team
    user = cast(User, request.user)
    authentik_username = user.username
    groups = get_authentik_groups(user)
    team, _, is_team = get_team_from_groups(groups)

    # Allow admins (incl. ticketing admins, filing on a team's behalf) to create tickets for any team
    is_admin = (
        has_permission(user, "gold_team") or has_permission(user, "admin") or has_permission(user, "ticketing_admin")
    )
    teams = None

    if is_admin:
        teams = Team.objects.filter(is_active=True).order_by("team_number")
        # If admin submitted form with team selection, use that team
        if request.method == "POST":
            admin_form = CreateTicketForm(request.POST)
            if admin_form.is_valid() and admin_form.cleaned_data.get("team_id"):
                with contextlib.suppress(Team.DoesNotExist):
                    team = Team.objects.get(id=admin_form.cleaned_data["team_id"], is_active=True)

    if not is_admin and not team:
        return render(
            request,
            "error.html",
            {
                "error": "Access denied",
                "message": "You must be a team member to create tickets.",
            },
        )

    # Fetch infrastructure from Quotient API (graceful degradation if unavailable)
    from quotient.client import QuotientAPIError, get_quotient_client

    infrastructure = None
    service_choices: list[dict[str, str]] = []
    box_names: list[str] = []
    box_ip_map: dict[str, str] = {}

    try:
        quotient_client = get_quotient_client()
        infrastructure = quotient_client.get_infrastructure()
        service_choices = quotient_client.get_service_choices()
        box_names = quotient_client.get_box_names()
        if infrastructure:
            for box in infrastructure.boxes:
                box_ip_map[box.name] = box.ip
    except QuotientAPIError:
        logger.warning("Quotient API unavailable, ticket form will have limited functionality")

    # Staff file tickets in any category; teams only in user-creatable ones.
    categories = get_all_categories(user_creatable_only=not is_admin)

    def form_page(error: str | None = None, status: int = 200) -> HttpResponse:
        context: dict[str, object] = {
            "team": team,
            "teams": teams,
            "categories": categories,
            "service_choices": service_choices,
            "box_names": box_names,
            "box_ip_map": box_ip_map,
        }
        if error:
            context["error"] = error
            context["form_data"] = request.POST
        return render(request, "create_ticket.html", context, status=status)

    if request.method == "POST":
        form = CreateTicketForm(request.POST)
        if not form.is_valid():
            return form_page(" ".join(str(err) for errors in form.errors.values() for err in errors))

        title = form.cleaned_data["title"]
        description = form.cleaned_data["description"]
        hostname = form.cleaned_data["hostname"]
        ip_address = form.cleaned_data["ip_address"]
        service_name = form.cleaned_data["service_name"]
        category_id = form.cleaned_data["category"]

        # Admins must select a team
        if is_admin and not team:
            return form_page("Please select a team.")

        if category_id not in categories:
            return form_page("Invalid ticket category selected.")

        category_obj = TicketCategory.objects.get(pk=category_id)
        cat_info = categories[category_id]

        # Validate required fields
        errors = []
        if not title:
            errors.append("Title is required.")

        required_fields = cat_info.get("required_fields", [])
        if "hostname" in required_fields and not hostname:
            errors.append("Hostname is required for this category.")

        if "ip_address" in required_fields and not ip_address:
            errors.append("IP Address is required for this category.")

        if "service_name" in required_fields and not service_name:
            errors.append("Service Name is required for this category.")

        if "description" in required_fields and not description:
            errors.append("Description is required for this category.")

        if errors:
            return form_page(" ".join(errors))

        # For box-reset, use hostname as description
        if cat_info.get("display_name", "").lower() == "box reset" and hostname:
            description = hostname

        if not team:
            return render(
                request,
                "error.html",
                {"error": "Bad Request", "message": "Team is required to create a ticket."},
                status=400,
            )

        # Create ticket using shared atomic function
        from ticketing.utils import TicketRateLimitError, create_ticket_atomic

        try:
            # One transaction, so a ticket never exists without the task that gives it a thread.
            with transaction.atomic():
                ticket = create_ticket_atomic(
                    team=team,
                    category=category_obj,
                    title=title,
                    description=description,
                    hostname=hostname,
                    ip_address=ip_address,
                    service_name=service_name,
                    actor_username=authentik_username,
                    enforce_team_limit=not is_admin,
                )
                DiscordTask.create_ticket_created_web(
                    ticket_id=ticket.id,
                    ticket_number=ticket.ticket_number,
                    team_number=team.team_number,
                    category=category_obj.display_name,
                    title=title,
                    created_by=authentik_username,
                )

            logger.info(f"Ticket {ticket.ticket_number} created via web by {authentik_username} for {team.team_name}")

            return redirect("ticket_detail", ticket_number=ticket.ticket_number)

        except TicketRateLimitError as e:
            logger.warning(f"Ticket rate limit hit by {authentik_username} for {team.team_name}")
            return form_page(str(e), status=429)
        except Exception:
            logger.exception("Failed to create ticket")

            return form_page("Failed to create ticket. Please try again or contact support if the problem persists.")

    # GET request - show form
    return form_page()
