"""Service layer for Discord-Authentik account linking."""

import logging
from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from core.models import DiscordTask
from team.models import DiscordLink, LinkAttempt, LinkToken, Team

logger = logging.getLogger(__name__)


@dataclass
class LinkResult:
    """Result of a linking operation."""

    success: bool
    error_template: str | None = None
    error_context: dict[str, str] | None = None
    team: Team | None = None
    team_number: int | None = None
    discord_username: str = ""
    authentik_username: str = ""
    is_team_account: bool = False


def validate_link_token(url_token: str | None, session_token: str | None, username: str) -> LinkResult | LinkToken:
    """Validate the link token from URL and session.

    Returns LinkToken on success, LinkResult with error on failure.
    """
    if not url_token:
        return LinkResult(
            success=False,
            error_template="link_error.html",
            error_context={
                "error": "Invalid request",
                "message": (
                    "Missing authentication state. Please start the linking process again with /link in Discord."
                ),
            },
        )

    # The browser completing the link must be the one that opened /auth/link. Without this, a
    # logged-in user sent straight to /auth/link-callback?token=<someone else's> got that person's
    # Discord account linked to their own portal account (and roles/permissions with it).
    if not session_token or session_token != url_token:
        session_hint = f"'{session_token[:8]}...'" if session_token else "none"
        logger.warning(
            f"Link token not started in this browser: session {session_hint}, url '{url_token[:8]}...' for {username}"
        )
        return LinkResult(
            success=False,
            error_template="link_error.html",
            error_context={
                "error": "Security verification failed",
                "message": (
                    "The linking request could not be verified. This may be a CSRF attack attempt. "
                    "Please start the linking process again with /link in Discord."
                ),
            },
        )

    try:
        link_token = LinkToken.objects.get(token=url_token, used=False)
    except LinkToken.DoesNotExist:
        return LinkResult(
            success=False,
            error_template="link_error.html",
            error_context={
                "error": "Invalid or expired token",
                "message": (
                    "This link has expired or been used already. Please use /link in Discord to generate a new one."
                ),
            },
        )

    if link_token.is_expired():
        return LinkResult(
            success=False,
            error_template="link_error.html",
            error_context={
                "error": "Token expired",
                "message": (
                    "This link has expired (15 minute limit). Please use /link in Discord to generate a new one."
                ),
            },
        )

    return link_token


def enforce_account_link_policy(
    user: User,
    discord_id: int,
    discord_username: str,
    authentik_username: str,
    team: Team | None,
    is_team_account: bool,
) -> LinkResult | None:
    """Check if account linking is allowed by policy.

    Returns:
        None if linking is allowed (no policy violation).
        LinkResult with error details if linking is blocked.

    Note: This follows the "error-or-None" pattern -- callers should check
    ``if result is not None: return render(...)`` to handle violations.
    """
    if is_team_account:
        return None

    existing_link = DiscordLink.objects.filter(user=user, is_active=True).first()
    if existing_link and existing_link.discord_id != discord_id:
        LinkAttempt.objects.create(
            discord_id=discord_id,
            discord_username=discord_username,
            authentik_username=authentik_username,
            team=team,
            success=False,
            failure_reason=f"Authentik account already linked to Discord user {existing_link.discord_username}",
        )
        return LinkResult(
            success=False,
            error_template="link_error.html",
            error_context={
                "error": "Account already linked",
                "message": (
                    f"This Authentik account ({authentik_username}) is already linked to "
                    f"Discord user {existing_link.discord_username}. "
                    "Each Authentik account can only be linked to one Discord account at a time. "
                    "Please contact an administrator if you need to unlink the previous account."
                ),
            },
        )
    return None


def store_discord_id_in_authentik(username: str, discord_id: int, authentik_id: str) -> None:
    """Optionally store discord_id in Authentik user attributes. Failures are non-fatal."""
    try:
        from core.authentik_manager import AuthentikManager

        manager = AuthentikManager()
        if manager.update_user_discord_id(username, discord_id, authentik_id):
            logger.info(f"Stored discord_id {discord_id} in Authentik for user {username}")
    except Exception as e:
        logger.warning(
            f"Could not store discord_id in Authentik (permissions issue): {e}. "
            f"Discord ID will be stored in DiscordLink table only."
        )


def execute_link(
    discord_id: int,
    discord_username: str,
    user: User,
    team: Team | None,
    is_team_account: bool,
) -> LinkResult | None:
    """Create the DiscordLink, handling team fullness with row locking.

    Returns error LinkResult or None on success.
    """
    if is_team_account and team:
        with transaction.atomic():
            team = Team.objects.select_for_update().get(pk=team.pk)
            if team.is_full():
                LinkAttempt.objects.create(
                    discord_id=discord_id,
                    discord_username=discord_username,
                    authentik_username=user.username,
                    team=team,
                    success=False,
                    failure_reason=f"Team full ({team.get_member_count()}/{team.max_members})",
                )
                return LinkResult(
                    success=False,
                    error_template="link_error.html",
                    error_context={
                        "error": "Team full",
                        "message": (
                            f"{team.team_name} is full ({team.get_member_count()}/{team.max_members} members). "
                            "Please contact an administrator."
                        ),
                    },
                )
            _create_or_update_link(discord_id, discord_username, user, team)
    else:
        _create_or_update_link(discord_id, discord_username, user, team=None)
    return None


def _create_or_update_link(
    discord_id: int,
    discord_username: str,
    user: User,
    team: Team | None,
) -> DiscordLink:
    """Create or update a DiscordLink, deactivating any previous link for this discord_id."""
    DiscordLink.deactivate_previous_links(discord_id)
    try:
        link = DiscordLink.objects.get(discord_id=discord_id, is_active=True)
        link.discord_username = discord_username
        link.user = user
        link.team = team
        link.linked_at = timezone.now()
        link.unlinked_at = None
        link.save()
    except DiscordLink.DoesNotExist:
        link = DiscordLink.objects.create(
            discord_id=discord_id,
            discord_username=discord_username,
            user=user,
            team=team,
            is_active=True,
        )
    return link


def finalize_link(
    link_token: LinkToken,
    discord_id: int,
    discord_username: str,
    authentik_username: str,
    team: Team | None,
    team_number: int | None,
    is_team_account: bool,
    groups: list[str],
) -> None:
    """Mark token used, create audit records, and queue Discord tasks."""
    # Mark token as used
    try:
        token_obj = LinkToken.objects.get(token=link_token.token)
        token_obj.used = True
        token_obj.save()
    except LinkToken.DoesNotExist:
        logger.warning(f"LinkToken disappeared during linking flow: token={link_token.token[:8]}...")

    # Audit record
    LinkAttempt.objects.create(
        discord_id=discord_id,
        discord_username=discord_username,
        authentik_username=authentik_username,
        team=team,
        success=True,
        failure_reason="",
    )

    # Discord task: assign group-based roles (for all accounts)
    DiscordTask.create_assign_group_roles(discord_id=discord_id, authentik_groups=groups)

    if is_team_account and team and team_number is not None:
        DiscordTask.create_assign_role(discord_id=discord_id, team_number=team_number)
        DiscordTask.create_log_to_channel(
            message=f"User Linked: <@{discord_id}> ({discord_username}) \u2192 **{team.team_name}**"
        )
        logger.info(f"Successfully linked {discord_username} ({discord_id}) to {team.team_name}")
    else:
        DiscordTask.create_log_to_channel(
            message=f"User Linked: <@{discord_id}> ({discord_username}) \u2192 **{authentik_username}** (non-team)"
        )
        logger.info(f"Successfully linked {discord_username} ({discord_id}) to {authentik_username}")
