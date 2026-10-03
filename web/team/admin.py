from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse

from .models import (
    DiscordLink,
    LinkAttempt,
    LinkRateLimit,
    LinkToken,
    SchoolInfo,
    Team,
)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin[Team]):
    list_display = [
        "team_number",
        "team_name",
        "authentik_group",
        "is_active",
        "member_count",
        "created_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["team_name", "authentik_group"]
    ordering = ["team_number"]
    readonly_fields = ["created_at", "updated_at", "ticket_counter"]

    @admin.display(description="Members")
    def member_count(self, obj: Team) -> int:
        return obj.get_member_count()


@admin.register(DiscordLink)
class DiscordLinkAdmin(admin.ModelAdmin[DiscordLink]):
    list_display = [
        "discord_username",
        "get_username",
        "team",
        "is_active",
        "linked_at",
    ]
    list_filter = ["is_active", "team", "linked_at"]
    search_fields = ["discord_username", "user__username"]
    readonly_fields = ["linked_at", "unlinked_at"]
    ordering = ["-linked_at"]

    @admin.display(description="Authentik Username")
    def get_username(self, obj: DiscordLink) -> str:
        return obj.user.username if obj.user else ""


@admin.register(LinkToken)
class LinkTokenAdmin(admin.ModelAdmin[LinkToken]):
    list_display = ["token", "discord_username", "used", "expires_at", "created_at"]
    list_filter = ["used", "expires_at"]
    search_fields = ["discord_username", "token"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]


@admin.register(LinkAttempt)
class LinkAttemptAdmin(admin.ModelAdmin[LinkAttempt]):
    list_display = [
        "discord_username",
        "authentik_username",
        "team",
        "success",
        "created_at",
    ]
    list_filter = ["success", "team", "created_at"]
    search_fields = ["discord_username", "authentik_username"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(LinkRateLimit)
class LinkRateLimitAdmin(admin.ModelAdmin[LinkRateLimit]):
    list_display = ["discord_id", "attempted_at"]
    list_filter = ["attempted_at"]
    search_fields = ["discord_id"]
    readonly_fields = ["attempted_at"]
    ordering = ["-attempted_at"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(SchoolInfo)
class SchoolInfoAdmin(admin.ModelAdmin[SchoolInfo]):
    list_display = [
        "school_name",
        "team",
        "contact_email",
        "secondary_email",
        "updated_at",
    ]
    search_fields = ["school_name", "contact_email", "team__team_name"]
    # password mirrors the team account's Authentik password for packet emails; editing it here would change
    # only this copy, so it's set solely by reset_team_password (Reset team, or the first packet send)
    readonly_fields = ["created_at", "updated_at", "updated_by", "password"]
    ordering = ["team__team_number"]
    actions = ["export_as_csv", "import_from_csv"]

    @admin.action(description="Export as CSV")
    def export_as_csv(self, request: HttpRequest, queryset: QuerySet[SchoolInfo]) -> HttpResponse:
        import csv

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="school_info.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "team_number",
                "team_name",
                "school_name",
                "contact_email",
                "secondary_email",
                "notes",
            ]
        )

        for school_info in queryset.select_related("team"):
            writer.writerow(
                [
                    school_info.team.team_number,
                    school_info.team.team_name,
                    school_info.school_name,
                    school_info.contact_email,
                    school_info.secondary_email or "",
                    school_info.notes or "",
                ]
            )

        return response

    @admin.action(description="Import from CSV")
    def import_from_csv(self, request: HttpRequest, queryset: QuerySet[SchoolInfo]) -> HttpResponse:
        from django.shortcuts import redirect

        return redirect("/ops/school-info/import/")
