from typing import TYPE_CHECKING

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from ticketing.models import Ticket


class UserGroups(models.Model):
    """
    Stores Authentik groups for a user. Refreshed on every login and by the bot every 5 minutes.

    This is the single source of truth for user permissions.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    authentik_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Authentik user UUID (sub claim)",
    )
    groups = models.JSONField(
        default=list,
        help_text="List of Authentik group names",
    )

    class Meta:
        verbose_name = "User Groups"
        verbose_name_plural = "User Groups"

    def __str__(self) -> str:
        return f"{self.user.username} ({len(self.groups)} groups)"


class AuditLog(models.Model):
    action = models.CharField(max_length=50)
    admin_user = models.CharField(max_length=255)
    target_entity = models.CharField(max_length=50)
    target_id = models.BigIntegerField()
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} by {self.admin_user}"


class DiscordTask(models.Model):
    """Task queue for Discord API operations (rate limit resilience).

    Adding a new task type? Update all of:
      1. TASK_TYPE_CHOICES below
      2. required_keys in clean()
      3. Payload schema in this docstring
      4. Factory classmethod on this class
      5. Handler + dispatch entry in bot/discord_queue.py

    Payload schemas by task_type (clean() checks the required keys are present when a task is first saved):
        post_comment:            {"ticket_id": int, "comment_id": int}
        broadcast_message:       {"target": str, "message": str, "sender": str}
        assign_role:             {"discord_id": int, "team_number": int}
        assign_group_roles:      {"discord_id": int, "authentik_groups": list[str]}
        remove_role:             {"discord_id": int, "team_number": int}
        setup_team_infrastructure: {"team_number": int}
        log_to_channel:          {"message": str}
        post_ticket_update:      {"action": str, "actor": str, ...optional extras}
        ticket_created_web:      {"ticket_id": int, "ticket_number": str,
                                  "team_number": int, "category": str,
                                  "title": str, "created_by": str}
        sync_roles:              {"requested_by": str, "dry_run": bool}
        add_user_to_thread:      {"discord_id": int, "thread_id": int}
        cleanup_competition:     {"requested_by": str}
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    TASK_TYPE_CHOICES = [
        ("post_comment", "Post Comment to Thread"),
        ("broadcast_message", "Broadcast Message"),
        ("assign_role", "Assign Team Role"),
        ("assign_group_roles", "Assign Group-Based Roles"),
        ("remove_role", "Remove Team Role"),
        ("setup_team_infrastructure", "Setup Team Infrastructure"),
        ("log_to_channel", "Log to Ops Channel"),
        ("post_ticket_update", "Post Ticket Update to Thread"),
        ("ticket_created_web", "Ticket Created via Web"),
        ("sync_roles", "Sync Roles from Authentik Groups"),
        ("add_user_to_thread", "Add User to Thread"),
        ("cleanup_competition", "Clean Up Competition"),
    ]

    task_type = models.CharField(max_length=50, choices=TASK_TYPE_CHOICES)
    ticket = models.ForeignKey("ticketing.Ticket", null=True, blank=True, on_delete=models.CASCADE)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=5)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["task_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.task_type} ({self.status})"

    def clean(self) -> None:
        """Validate payload has required keys for the task type."""
        from django.core.exceptions import ValidationError

        required_keys: dict[str, set[str]] = {
            "post_comment": {"ticket_id", "comment_id"},
            "broadcast_message": {"target", "message", "sender"},
            "assign_role": {"discord_id", "team_number"},
            "assign_group_roles": {"discord_id", "authentik_groups"},
            "remove_role": {"discord_id", "team_number"},
            "setup_team_infrastructure": {"team_number"},
            "log_to_channel": {"message"},
            "post_ticket_update": {"action", "actor"},
            "ticket_created_web": {"ticket_id", "ticket_number", "team_number", "category", "title", "created_by"},
            "sync_roles": {"requested_by", "dry_run"},
            "add_user_to_thread": {"discord_id", "thread_id"},
            "cleanup_competition": {"requested_by"},
        }
        if self.task_type in required_keys:
            missing = required_keys[self.task_type] - set(self.payload.keys())
            if missing:
                raise ValidationError(f"Payload for {self.task_type} missing keys: {missing}")

    def save(self, *args: object, **kwargs: object) -> None:
        if not self.pk:
            self.clean()
        super().save(*args, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def create_assign_role(cls, discord_id: int, team_number: int) -> DiscordTask:
        return cls.objects.create(
            task_type="assign_role",
            payload={"discord_id": discord_id, "team_number": team_number},
            status="pending",
        )

    @classmethod
    def create_remove_role(cls, discord_id: int, team_number: int) -> DiscordTask:
        """Create a task to remove a team's role (and the Blueteam role) from a Discord user."""
        return cls.objects.create(
            task_type="remove_role",
            payload={"discord_id": discord_id, "team_number": team_number},
            status="pending",
        )

    @classmethod
    def create_cleanup_competition(cls, requested_by: str) -> DiscordTask:
        """Create a task for the bot to tear down the competition (see run_competition_cleanup)."""
        return cls.objects.create(
            task_type="cleanup_competition",
            payload={"requested_by": requested_by},
            status="pending",
        )

    @classmethod
    def create_assign_group_roles(cls, discord_id: int, authentik_groups: list[str]) -> DiscordTask:
        return cls.objects.create(
            task_type="assign_group_roles",
            payload={"discord_id": discord_id, "authentik_groups": authentik_groups},
            status="pending",
        )

    @classmethod
    def create_log_to_channel(cls, message: str) -> DiscordTask:
        """Create a task to log a message to the ops channel."""
        return cls.objects.create(
            task_type="log_to_channel",
            payload={"message": message},
            status="pending",
        )

    @classmethod
    def create_setup_team_infrastructure(cls, team_number: int) -> DiscordTask:
        """Create a task to set up Discord channels for a team."""
        return cls.objects.create(
            task_type="setup_team_infrastructure",
            payload={"team_number": team_number},
            status="pending",
        )

    @classmethod
    def create_broadcast_message(cls, target: str, message: str, sender: str) -> DiscordTask:
        return cls.objects.create(
            task_type="broadcast_message",
            payload={"target": target, "message": message, "sender": sender},
            status="pending",
        )

    @classmethod
    def create_sync_roles(cls, requested_by: str, dry_run: bool) -> DiscordTask:
        """Create a task to sync roles between guilds."""
        return cls.objects.create(
            task_type="sync_roles",
            payload={"requested_by": requested_by, "dry_run": dry_run},
            status="pending",
        )

    @classmethod
    def create_ticket_created_web(
        cls,
        ticket_id: int,
        ticket_number: str,
        team_number: int,
        category: str,
        title: str,
        created_by: str,
    ) -> DiscordTask:
        """Create a task to notify Discord about a web-created ticket."""
        return cls.objects.create(
            task_type="ticket_created_web",
            payload={
                "ticket_id": ticket_id,
                "ticket_number": ticket_number,
                "team_number": team_number,
                "category": category,
                "title": title,
                "created_by": created_by,
            },
            status="pending",
        )

    @classmethod
    def create_post_comment(cls, ticket: Ticket, ticket_id: int, comment_id: int) -> DiscordTask:
        """Create a task to post a comment to a ticket's Discord thread."""
        return cls.objects.create(
            task_type="post_comment",
            ticket=ticket,
            payload={"ticket_id": ticket_id, "comment_id": comment_id},
            status="pending",
        )

    @classmethod
    def create_post_ticket_update(cls, ticket: Ticket, action: str, actor: str, **extra: object) -> DiscordTask:
        """Create a task to post a ticket status update to Discord.

        Extra keyword arguments are merged into the payload (e.g. resolution_notes,
        points_charged, reason).
        """
        payload: dict[str, object] = {"action": action, "actor": actor, **extra}
        return cls.objects.create(
            task_type="post_ticket_update",
            ticket=ticket,
            payload=payload,
            status="pending",
        )

    @classmethod
    def create_add_user_to_thread(cls, ticket: Ticket, discord_id: int, thread_id: int) -> DiscordTask:
        return cls.objects.create(
            task_type="add_user_to_thread",
            ticket=ticket,
            payload={"discord_id": discord_id, "thread_id": thread_id},
            status="pending",
        )


class BotState(models.Model):
    """Bot state storage (dashboard message IDs, etc)."""

    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=255)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.key}: {self.value}"


class DashboardUpdate(models.Model):
    """Dashboard update tracking (for debouncing)."""

    needs_update = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    update_scheduled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Dashboard (needs_update={self.needs_update})"


class QueuedAnnouncement(models.Model):
    """Announcements queued for teams that don't have channels yet."""

    team = models.ForeignKey("team.Team", on_delete=models.CASCADE, related_name="queued_announcements")
    message = models.TextField()
    sender_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        status = "delivered" if self.delivered_at else "pending"
        return f"Announcement for Team {self.team.team_number} ({status})"


class CompetitionConfig(models.Model):
    max_team_members = models.IntegerField(default=10, help_text="Maximum members per team")

    competition_start_time = models.DateTimeField(
        null=True, blank=True, help_text="When applications should be enabled"
    )
    competition_end_time = models.DateTimeField(null=True, blank=True, help_text="When applications should be disabled")
    applications_enabled = models.BooleanField(default=False, help_text="Whether applications are currently enabled")

    controlled_applications = models.JSONField(
        default=list,
        help_text="List of Authentik application slugs to enable/disable (e.g., ['scoring', 'quotient2', 'semaphore'])",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    status_channel_id = models.BigIntegerField(
        null=True, blank=True, help_text="Discord channel ID for competition status display"
    )
    status_message_id = models.BigIntegerField(
        null=True, blank=True, help_text="Discord message ID for status embed (updated in place)"
    )

    class Meta:
        verbose_name = "Competition Configuration"
        verbose_name_plural = "Competition Configuration"

    def __str__(self) -> str:
        if self.competition_start_time:
            return f"Competition starts at {self.competition_start_time} (enabled={self.applications_enabled})"
        return "Competition not scheduled"

    def should_enable_applications(self) -> bool:
        if not self.competition_start_time:
            return False
        now = timezone.now()
        after_start = now >= self.competition_start_time
        before_end = self.competition_end_time is None or now < self.competition_end_time
        return after_start and before_end and not self.applications_enabled

    def should_disable_applications(self) -> bool:
        if not self.competition_end_time:
            return False
        return timezone.now() >= self.competition_end_time and self.applications_enabled

    def ensure_controlled_applications(self) -> None:
        """Fetch and cache controlled application slugs; delegates so API calls stay out of the model layer."""
        from core.services.competition import ensure_controlled_applications

        ensure_controlled_applications(self)

    @classmethod
    def get_config(cls) -> CompetitionConfig:
        """Get or create the singleton config instance (pk=1). Never create additional rows."""
        config, _ = cls.objects.get_or_create(pk=1)
        return config
