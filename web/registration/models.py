"""Models for team registration."""

import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


def generate_edit_token() -> str:
    """Generate a secure random token for registration editing."""
    return secrets.token_urlsafe(48)


class TeamRegistration(models.Model):
    """Team registration for competition."""

    STATUS_CHOICES = [
        ("pending", "Pending Review"),
        ("approved", "Approved"),
        ("paid", "Paid"),
        ("credentials_sent", "Credentials Sent"),
        ("rejected", "Rejected"),
    ]

    REGION_CHOICES = [
        ("wrccdc", "Western Regional (WRCCDC)"),
        ("prccdc", "Pacific Rim (PRCCDC)"),
        ("mwccdc", "Midwest (MWCCDC)"),
        ("rmccdc", "Rocky Mountain (RMCCDC)"),
        ("swccdc", "Southwest (SWCCDC)"),
        ("neccdc", "Northeast (NECCDC)"),
        ("maccdc", "Mid-Atlantic (MACCDC)"),
        ("seccdc", "Southeast (SECCDC)"),
        ("at_large", "At-Large"),
    ]

    school_name = models.CharField(max_length=255)
    region = models.CharField(max_length=20, choices=REGION_CHOICES, default="wrccdc")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    # Token for self-service editing
    edit_token = models.CharField(max_length=64, unique=True, default=generate_edit_token)
    edit_token_expires = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    credentials_sent_at = models.DateTimeField(null=True, blank=True)

    rejection_reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self) -> str:
        return f"{self.school_name} ({self.status})"

    def approve(self, user: User) -> None:
        """Approve the registration."""
        self.status = "approved"
        self.approved_at = timezone.now()
        self.approved_by = user
        self.save()

    def reject(self, reason: str) -> None:
        """Reject the registration."""
        self.status = "rejected"
        self.rejection_reason = reason
        self.save()

    def mark_as_paid(self) -> None:
        """Mark registration as paid."""
        self.status = "paid"
        self.paid_at = timezone.now()
        self.save()


class Season(models.Model):
    """Competition season (e.g., 2026 Season)."""

    name = models.CharField(max_length=100)
    year = models.IntegerField(unique=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year"]

    def __str__(self) -> str:
        return self.name


class EventQuerySet(models.QuerySet["Event"]):
    """Custom queryset for Event model."""

    def annotate_enrollment_count(self) -> EventQuerySet:
        """Annotate events with their enrollment count."""
        return self.annotate(enrollment_count=models.Count("enrollments"))


class EventManager(models.Manager["Event"]):
    """Custom manager for Event model."""

    def get_queryset(self) -> EventQuerySet:
        return EventQuerySet(self.model, using=self._db)

    def annotate_enrollment_count(self) -> EventQuerySet:
        return self.get_queryset().annotate_enrollment_count()


class Event(models.Model):
    """Competition event within a season."""

    EVENT_TYPE_CHOICES = [
        ("invitational", "Invitational"),
        ("qualifier", "Qualifier"),
        ("regional", "Regional"),
        ("state", "State"),
    ]

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="events")
    objects = EventManager()
    name = models.CharField(max_length=255)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)
    event_number = models.IntegerField(null=True, blank=True)
    date = models.DateField()
    start_time = models.TimeField(default="09:00")
    end_time = models.TimeField(default="17:00")
    registration_open = models.BooleanField(default=True)
    registration_deadline = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    is_finalized = models.BooleanField(default=False)
    max_teams = models.IntegerField(default=50)

    reminder_days = models.JSONField(default=list)
    last_reminder_sent = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["season", "date"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "event_type", "event_number"],
                name="unique_event_per_season",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.season.name})"


class RegistrationContact(models.Model):
    """Contact information for a team registration."""

    ROLE_CHOICES = [
        ("captain", "Team Captain"),
        ("co_captain", "Co-Captain"),
        ("coach", "Coach/Faculty Advisor"),
        ("site_judge", "Site Judge"),
    ]

    registration = models.ForeignKey(TeamRegistration, on_delete=models.CASCADE, related_name="contacts")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registration", "role"],
                name="unique_contact_role_per_registration",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_role_display()}) - {self.registration.school_name}"


class RegistrationEventEnrollment(models.Model):
    """Track which events a school registration signed up for."""

    registration = models.ForeignKey(TeamRegistration, on_delete=models.CASCADE, related_name="event_enrollments")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="enrollments")
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registration", "event"],
                name="unique_enrollment_per_registration",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.registration.school_name} → {self.event.name}"


class EventTeamAssignment(models.Model):
    """Per-event random team number assignment."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="team_assignments")
    registration = models.ForeignKey(TeamRegistration, on_delete=models.CASCADE, related_name="team_assignments")
    team = models.ForeignKey("team.Team", on_delete=models.CASCADE, related_name="event_assignments")
    assigned_at = models.DateTimeField(auto_now_add=True)
    credentials_sent_at = models.DateTimeField(null=True, blank=True)
    password_generated = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "registration"],
                name="unique_assignment_per_registration",
            ),
            models.UniqueConstraint(
                fields=["event", "team"],
                name="unique_team_per_event",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.registration.school_name} → Team {self.team.team_number:02d} ({self.event.name})"


class RegistrationRateLimit(models.Model):
    """One successful public registration submission, for per-client rate limiting.

    Stored in the database so every gunicorn worker shares the count.
    """

    LIMIT = 10
    WINDOW = timedelta(hours=1)

    ip = models.GenericIPAddressField(unpack_ipv4=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["ip", "-attempted_at"]),
        ]

    def __str__(self) -> str:
        return f"Registration from {self.ip} at {self.attempted_at}"

    @classmethod
    def over_limit(cls, ip: str) -> bool:
        """Whether this client has used up its submissions for the current window."""
        since = timezone.now() - cls.WINDOW
        return cls.objects.filter(ip=ip, attempted_at__gte=since).count() >= cls.LIMIT

    @classmethod
    def record(cls, ip: str) -> None:
        """Count a submission, pruning rows that have aged out of every window."""
        cls.objects.filter(attempted_at__lt=timezone.now() - cls.WINDOW).delete()
        cls.objects.create(ip=ip)
