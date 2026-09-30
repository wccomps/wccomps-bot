from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from .auth_utils import get_authentik_groups
from .models import (
    AuditLog,
    BotState,
    CompetitionConfig,
    DashboardUpdate,
    DiscordTask,
    UserGroups,
)


class AuthentikAdminSite(admin.AdminSite):
    """Admin site that checks Authentik groups for access."""

    site_header = "WCComps Portal Administration"
    site_title = "WCComps Portal Admin"
    index_title = "Competition Management"
    site_url = "/ops/tickets/"

    def has_permission(self, request: HttpRequest) -> bool:
        if not request.user.is_active or not request.user.is_authenticated:
            return False

        groups = get_authentik_groups(request.user)
        return "WCComps_Discord_Admin" in groups or "WCComps_Ticketing_Admin" in groups


admin.site = AuthentikAdminSite()
admin.sites.site = admin.site


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin[AuditLog]):
    list_display = ["action", "admin_user", "target_entity", "target_id", "created_at"]
    list_filter = ["action", "target_entity"]
    search_fields = ["admin_user", "action"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: AuditLog | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: AuditLog | None = None) -> bool:
        return False


@admin.register(DiscordTask)
class DiscordTaskAdmin(admin.ModelAdmin[DiscordTask]):
    list_display = ["task_type", "status", "retry_count", "created_at", "completed_at"]
    list_filter = ["status", "task_type"]
    search_fields = ["task_type", "error_message"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "completed_at"]

    actions = ["retry_failed_tasks"]

    @admin.action(description="Retry failed tasks")
    def retry_failed_tasks(self, request: HttpRequest, queryset: QuerySet[DiscordTask]) -> None:
        from django.utils import timezone

        updated = queryset.filter(status="failed").update(
            status="pending",
            retry_count=0,
            next_retry_at=timezone.now(),
            error_message="",
        )
        self.message_user(request, f"{updated} tasks reset for retry")


@admin.register(BotState)
class BotStateAdmin(admin.ModelAdmin[BotState]):
    list_display = ["key", "value", "updated_at"]
    search_fields = ["key", "value"]
    ordering = ["key"]
    readonly_fields = ["key", "value", "updated_at"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False  # Managed by Discord bot

    def has_delete_permission(self, request: HttpRequest, obj: BotState | None = None) -> bool:
        return False  # Internal bot state


@admin.register(DashboardUpdate)
class DashboardUpdateAdmin(admin.ModelAdmin[DashboardUpdate]):
    list_display = ["needs_update", "last_updated", "update_scheduled_at"]
    readonly_fields = ["needs_update", "last_updated", "update_scheduled_at"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False  # Singleton managed by system

    def has_delete_permission(self, request: HttpRequest, obj: DashboardUpdate | None = None) -> bool:
        return False  # System singleton


@admin.register(CompetitionConfig)
class CompetitionConfigAdmin(admin.ModelAdmin[CompetitionConfig]):
    list_display = [
        "competition_status",
        "competition_start_time",
        "competition_end_time",
        "applications_enabled",
        "max_team_members",
    ]
    readonly_fields = ["created_at", "updated_at", "applications_enabled"]

    fieldsets = (
        (
            "Competition Timing",
            {
                "fields": (
                    "competition_start_time",
                    "competition_end_time",
                    "applications_enabled",
                ),
                "description": "Set start/end times for automatic application enable/disable. "
                "Applications will be automatically enabled at start time and disabled at end time.",
            },
        ),
        (
            "Application Control",
            {
                "fields": ("controlled_applications",),
                "description": (
                    "List of Authentik application slugs to control (e.g., ['scoring', 'quotient2', 'semaphore'])."
                    " These applications will be enabled/disabled based on competition timing."
                ),
            },
        ),
        (
            "Team Settings",
            {
                "fields": ("max_team_members",),
                "description": "Maximum number of members allowed per team.",
            },
        ),
        (
            "System Info",
            {
                "fields": ("created_at", "updated_at"),
                "description": "Audit and system information.",
            },
        ),
    )

    @admin.display(description="Status")
    def competition_status(self, obj: CompetitionConfig) -> str:
        from django.utils import timezone

        if obj.applications_enabled:
            return "Active"
        if obj.competition_start_time and timezone.now() < obj.competition_start_time:
            return "Scheduled"
        if obj.competition_end_time and timezone.now() > obj.competition_end_time:
            return "Ended"
        return "Not Scheduled"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return not CompetitionConfig.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: CompetitionConfig | None = None) -> bool:
        return False  # Singleton - never delete


@admin.register(UserGroups)
class UserGroupsAdmin(admin.ModelAdmin[UserGroups]):
    list_display = ["user", "authentik_id", "group_count"]
    search_fields = ["user__username", "authentik_id"]
    readonly_fields = ["user", "authentik_id", "groups"]

    @admin.display(description="Groups")
    def group_count(self, obj: UserGroups) -> str:
        return str(len(obj.groups))

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False  # Created by OAuth flow

    def has_delete_permission(self, request: HttpRequest, obj: UserGroups | None = None) -> bool:
        # Deleting strips the user's permissions; their next login makes a new account and renames
        # the old one aside (with its Discord links), so prefer letting the group refresh update it.
        return True
