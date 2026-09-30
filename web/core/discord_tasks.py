"""The work the portal queues for the bot: one payload dataclass per DiscordTask type.

TaskPayload is the registry. DiscordTask.task_type's choices come from it, DiscordTask.enqueue
takes one of its members, and the bot's dispatch matches on it exhaustively, so mypy rejects a
payload type the bot cannot handle.
"""

from dataclasses import dataclass
from typing import ClassVar, get_args


@dataclass(frozen=True, kw_only=True)
class PostComment:
    task_type: ClassVar = "post_comment"
    label: ClassVar = "Post Comment to Thread"
    ticket_id: int
    comment_id: int


@dataclass(frozen=True, kw_only=True)
class BroadcastMessage:
    task_type: ClassVar = "broadcast_message"
    label: ClassVar = "Broadcast Message"
    target: str
    message: str
    sender: str


@dataclass(frozen=True, kw_only=True)
class AssignRole:
    task_type: ClassVar = "assign_role"
    label: ClassVar = "Assign Team Role"
    discord_id: int
    team_number: int


@dataclass(frozen=True, kw_only=True)
class AssignGroupRoles:
    task_type: ClassVar = "assign_group_roles"
    label: ClassVar = "Assign Group-Based Roles"
    discord_id: int
    authentik_groups: list[str]


@dataclass(frozen=True, kw_only=True)
class RemoveRole:
    """Remove a team's role (and the Blueteam role) from a Discord user."""

    task_type: ClassVar = "remove_role"
    label: ClassVar = "Remove Team Role"
    discord_id: int
    team_number: int


@dataclass(frozen=True, kw_only=True)
class SetupTeamInfrastructure:
    """Create a team's Discord role and channels."""

    task_type: ClassVar = "setup_team_infrastructure"
    label: ClassVar = "Setup Team Infrastructure"
    team_number: int


@dataclass(frozen=True, kw_only=True)
class LogToChannel:
    task_type: ClassVar = "log_to_channel"
    label: ClassVar = "Log to Ops Channel"
    message: str


@dataclass(frozen=True, kw_only=True)
class PostTicketUpdate:
    """A ticket status change (claimed, resolved, reopened, ...) for its Discord thread and the dashboard."""

    task_type: ClassVar = "post_ticket_update"
    label: ClassVar = "Post Ticket Update to Thread"
    action: str
    actor: str
    assignee: str = ""
    resolution_notes: str = ""
    points_charged: int = 0
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class TicketCreatedWeb:
    """A ticket opened on the web, which needs its Discord thread."""

    task_type: ClassVar = "ticket_created_web"
    label: ClassVar = "Ticket Created via Web"
    ticket_id: int
    ticket_number: str
    team_number: int
    category: str
    title: str
    created_by: str


@dataclass(frozen=True, kw_only=True)
class SyncRoles:
    """Add roles from Authentik groups to linked users; the counts land in DiscordTask.result."""

    task_type: ClassVar = "sync_roles"
    label: ClassVar = "Sync Roles from Authentik Groups"
    requested_by: str
    dry_run: bool


@dataclass(frozen=True, kw_only=True)
class AddUserToThread:
    task_type: ClassVar = "add_user_to_thread"
    label: ClassVar = "Add User to Thread"
    discord_id: int
    thread_id: int


@dataclass(frozen=True, kw_only=True)
class CleanupCompetition:
    """Tear down the competition (see bot.competition_actions.run_competition_cleanup)."""

    task_type: ClassVar = "cleanup_competition"
    label: ClassVar = "Clean Up Competition"
    requested_by: str


type TaskPayload = (
    PostComment
    | BroadcastMessage
    | AssignRole
    | AssignGroupRoles
    | RemoveRole
    | SetupTeamInfrastructure
    | LogToChannel
    | PostTicketUpdate
    | TicketCreatedWeb
    | SyncRoles
    | AddUserToThread
    | CleanupCompetition
)

PAYLOAD_TYPES: dict[str, type[TaskPayload]] = {cls.task_type: cls for cls in get_args(TaskPayload.__value__)}
