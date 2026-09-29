"""Scoring system models for CCDC competitions."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def format_boxes_display(boxes: list[str] | str | None) -> str:
    """Format an affected_boxes field value for display.

    Used by both RedTeamScore and IncidentReport models.
    """
    if not boxes:
        return ""
    if isinstance(boxes, str):
        return boxes
    return ", ".join(boxes)


def validate_file_size(file: UploadedFile[bytes]) -> UploadedFile[bytes]:
    """Validate that uploaded file is not larger than 50MB."""
    max_size_mb = 50
    if file.size and file.size > max_size_mb * 1024 * 1024:
        from django.core.exceptions import ValidationError

        raise ValidationError(f"File size cannot exceed {max_size_mb}MB")
    return file


class ScoringTemplate(models.Model):
    """Scoring configuration using category weights and raw maximums.

    The calculator derives a scaling modifier for each category at runtime:
        total_pool = max(raw_max / (weight/100) for each category)
        modifier = (weight/100) × total_pool / raw_max
        scaled = raw × modifier

    This produces the same result as manually entering modifiers, but the
    config page shows meaningful percentages instead of opaque multipliers.
    """

    # Category weights (percentage of total score; must sum to 100)
    service_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("40"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percentage weight for service uptime",
    )
    inject_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("40"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percentage weight for inject responses",
    )
    orange_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("20"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percentage weight for orange team checks",
    )

    # Maximum possible raw points in each category
    service_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("11454"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Maximum possible raw service points",
    )
    inject_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("3060"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Maximum possible raw inject points",
    )
    orange_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("160"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Maximum possible raw orange team check points",
    )

    # Optional: direct scaling modifiers (bypass weight-based derivation)
    service_modifier = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Direct scaling modifier for service (overrides weight-based calculation)",
    )
    inject_modifier = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Direct scaling modifier for injects (overrides weight-based calculation)",
    )
    orange_modifier = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Direct scaling modifier for orange team (overrides weight-based calculation)",
    )

    def clean(self) -> None:
        """Validate that weights sum to 100 (when not using direct modifiers)."""
        from django.core.exceptions import ValidationError

        if not (self.service_modifier and self.inject_modifier and self.orange_modifier):
            total = (self.service_weight or 0) + (self.inject_weight or 0) + (self.orange_weight or 0)
            if total != Decimal("100"):
                raise ValidationError(f"Weights must sum to 100 (currently {total})")

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scoring_template_updates",
    )

    class Meta:
        db_table = "scoring_template"
        verbose_name = "Scoring Template"
        verbose_name_plural = "Scoring Templates"

    def __str__(self) -> str:
        return "Scoring Template"


class QuotientMetadataCache(models.Model):
    """Cached metadata from Quotient for populating dropdowns."""

    # JSON fields for infrastructure data
    boxes = models.JSONField(
        default=list,
        help_text="List of boxes from Quotient metadata",
    )
    services = models.JSONField(
        default=list,
        help_text="List of services from Quotient metadata",
    )
    event_name = models.CharField(max_length=200, blank=True)
    team_count = models.IntegerField(default=0)

    # Sync tracking
    last_synced = models.DateTimeField(auto_now=True)
    synced_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "quotient_metadata_cache"
        verbose_name = "Quotient Metadata Cache"
        verbose_name_plural = "Quotient Metadata Caches"

    def __str__(self) -> str:
        return "Quotient Metadata Cache"


class RedTeamIPPool(models.Model):
    """Reusable pool of source IP addresses for red team members."""

    name = models.CharField(max_length=100)
    ip_addresses = models.TextField(help_text="Newline or comma-separated IP addresses used for rotating attacks")
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="ip_pools",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "red_team_ip_pool"
        verbose_name = "Red Team IP Pool"
        verbose_name_plural = "Red Team IP Pools"
        unique_together = [["name", "created_by"]]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.ip_count} IPs)"

    @property
    def ip_count(self) -> int:
        """Return count of valid IPs in the pool."""
        return len(self.get_ip_list())

    def get_ip_list(self) -> list[str]:
        """Parse and return list of IP addresses."""
        if not self.ip_addresses:
            return []
        # Split by newlines and commas, strip whitespace, filter empty
        ips = []
        for line in self.ip_addresses.replace(",", "\n").split("\n"):
            ip = line.strip()
            if ip:
                ips.append(ip)
        return ips

    def contains_ip(self, ip: str) -> bool:
        """Check if the given IP exists in this pool."""
        return ip.strip() in self.get_ip_list()


class AttackType(models.Model):
    """Predefined attack types for red team findings."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, help_text="Help text for red teamers")
    is_active = models.BooleanField(default=True, help_text="Can be selected for new findings")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attack_type"
        verbose_name = "Attack Type"
        verbose_name_plural = "Attack Types"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class RedTeamScore(models.Model):
    """Red team vulnerability score affecting one or more teams."""

    event = models.ForeignKey(
        "registration.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="red_team_scores",
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="red_scores_submitted",
    )
    contributors = models.ManyToManyField(
        User,
        related_name="contributed_findings",
        blank=True,
        help_text="All red teamers who submitted matching findings",
    )

    # Attack details
    attack_type = models.ForeignKey(
        AttackType,
        on_delete=models.PROTECT,
        related_name="findings",
        null=True,
        blank=True,
    )
    attack_vector = models.TextField(
        blank=True,
        help_text="Additional details about the attack (legacy field, use notes instead)",
    )
    source_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Red team source IP (use this OR source_ip_pool)",
    )
    source_ip_pool = models.ForeignKey(
        RedTeamIPPool,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="findings",
        help_text="Pool of rotating source IPs (use this OR source_ip)",
    )
    destination_ip_template = models.CharField(
        max_length=50,
        blank=True,
        help_text="IP template (e.g., 10.100.1X.22 where X is team number)",
    )

    # Affected infrastructure (from Quotient metadata)
    affected_boxes = models.JSONField(
        default=list,
        blank=True,
        help_text="List of affected box names",
    )
    affected_service = models.CharField(max_length=100, blank=True)

    # Flags
    universally_attempted = models.BooleanField(
        default=False,
        help_text="Attack was attempted against all teams",
    )
    persistence_established = models.BooleanField(
        default=False,
        help_text="Persistence was established on target",
    )

    # Outcome checkboxes - cumulative deductions per CCDC scoring guidelines
    # Access levels (only highest scored per attack)
    root_access = models.BooleanField(
        default=False,
        help_text="Root/Administrator level access obtained (-100 pts)",
    )
    user_access = models.BooleanField(
        default=False,
        help_text="User level access obtained (-25 pts, not scored if root_access)",
    )
    privilege_escalation = models.BooleanField(
        default=False,
        help_text="User escalated to Root/Admin (-100 pts additional)",
    )

    # Data recovery outcomes (cumulative)
    credentials_recovered = models.BooleanField(
        default=False,
        help_text="User IDs and passwords recovered (-50 pts)",
    )
    sensitive_files_recovered = models.BooleanField(
        default=False,
        help_text="Config files, corporate data recovered (-25 pts)",
    )
    credit_cards_recovered = models.BooleanField(
        default=False,
        help_text="Customer credit card numbers recovered (-50 pts)",
    )
    pii_recovered = models.BooleanField(
        default=False,
        help_text="PII recovered: name, address, CC# (-200 pts)",
    )
    encrypted_db_recovered = models.BooleanField(
        default=False,
        help_text="Encrypted customer data/database recovered (-25 pts)",
    )
    db_decrypted = models.BooleanField(
        default=False,
        help_text="Database was decrypted (-25 pts additional)",
    )

    # Affected teams and scoring
    affected_teams = models.ManyToManyField(
        "team.Team",
        related_name="red_team_scores",
        help_text="Teams affected by this finding",
    )
    points_per_team = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Points deducted per affected team (auto-calculated from outcomes)",
    )

    # Approval tracking
    is_approved = models.BooleanField(
        default=False,
        help_text="Whether this finding has been approved by Gold Team",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this finding was approved",
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="red_scores_approved",
        help_text="Gold Team member who approved this finding",
    )

    # Evidence
    notes = models.TextField(blank=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "red_team_finding"
        verbose_name = "Red Team Score"
        verbose_name_plural = "Red Team Scores"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        if self.attack_type:
            return f"RF-{self.pk}: {self.attack_type.name}"
        return f"RF-{self.pk}: {self.attack_vector[:50] if self.attack_vector else 'Unknown'}"

    @property
    def source_ip_display(self) -> str:
        """Return display string for source IP (single IP or pool name)."""
        if self.source_ip:
            return str(self.source_ip)
        if self.source_ip_pool:
            return f"Pool: {self.source_ip_pool.name} ({self.source_ip_pool.ip_count} IPs)"
        return "No source IP"

    @property
    def affected_boxes_display(self) -> str:
        """Return display string for affected boxes."""
        return format_boxes_display(self.affected_boxes)

    def calculate_points(self) -> Decimal:
        """Points each affected team loses, per CCDC guidelines, from the outcome checkboxes.

        Stored positive in points_per_team; the calculator subtracts it (get_approved_red_deductions).
        Effect on the team, from the National Scoring Guidelines:
        - Root/Admin access: -100
        - User access: -25 (only if no root access)
        - Privilege escalation: -100 (additional)
        - Credentials recovered: -50
        - Sensitive files recovered: -25
        - Credit cards recovered: -50
        - PII recovered: -200
        - Encrypted DB recovered: -25
        - DB decrypted: -25 (additional)
        """
        total = Decimal("0")

        # Access level (only highest scored)
        if self.root_access:
            total += Decimal("100")
        elif self.user_access:
            total += Decimal("25")

        # Privilege escalation is additional
        if self.privilege_escalation:
            total += Decimal("100")

        # Data recovery outcomes (cumulative)
        if self.credentials_recovered:
            total += Decimal("50")
        if self.sensitive_files_recovered:
            total += Decimal("25")
        if self.credit_cards_recovered:
            total += Decimal("50")
        if self.pii_recovered:
            total += Decimal("200")
        if self.encrypted_db_recovered:
            total += Decimal("25")
        if self.db_decrypted:
            total += Decimal("25")

        return total

    @property
    def outcomes_display(self) -> list[str]:
        """Return list of outcome labels for display."""
        outcomes = []
        if self.root_access:
            outcomes.append("Root Access (-100)")
        elif self.user_access:
            outcomes.append("User Access (-25)")
        if self.privilege_escalation:
            outcomes.append("Privilege Escalation (-100)")
        if self.credentials_recovered:
            outcomes.append("Credentials (-50)")
        if self.sensitive_files_recovered:
            outcomes.append("Sensitive Files (-25)")
        if self.credit_cards_recovered:
            outcomes.append("Credit Cards (-50)")
        if self.pii_recovered:
            outcomes.append("PII (-200)")
        if self.encrypted_db_recovered:
            outcomes.append("Encrypted DB (-25)")
        if self.db_decrypted:
            outcomes.append("DB Decrypted (-25)")
        return outcomes


class RedTeamScreenshot(models.Model):
    """Screenshots for red team findings (stored in database)."""

    finding = models.ForeignKey(
        RedTeamScore,
        on_delete=models.CASCADE,
        related_name="screenshots",
    )
    file_data = models.BinaryField(null=True, blank=True)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, default="image/png")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "red_team_screenshot"
        ordering = ["uploaded_at"]

    @property
    def has_data(self) -> bool:
        """Check if file data exists (for legacy records without data)."""
        return bool(self.file_data)

    def __str__(self) -> str:
        return f"{self.filename} ({self.finding})"


class IncidentReport(models.Model):
    """Blue team incident report submission."""

    event = models.ForeignKey(
        "registration.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="incident_reports",
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="incident_reports",
        help_text="Team submitting the incident report",
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="incidents_submitted",
    )

    # Attack details
    attack_description = models.TextField()
    source_ip = models.GenericIPAddressField()
    destination_ip = models.GenericIPAddressField(null=True, blank=True)

    # Affected infrastructure
    affected_boxes = models.JSONField(
        default=list,
        blank=True,
        help_text="List of affected box names",
    )
    affected_service = models.CharField(max_length=100, blank=True)

    # Timeline
    attack_detected_at = models.DateTimeField(help_text="When the attack was detected")
    attack_mitigated = models.BooleanField(
        default=False,
        help_text="Attack was successfully mitigated",
    )

    # Evidence
    evidence_notes = models.TextField(blank=True)

    # Gold team review
    is_approved = models.BooleanField(default=False)
    matched_to_red_score = models.ForeignKey(
        RedTeamScore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matched_incident_reports",
        help_text="Red team score this incident matched to",
    )
    points_returned = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Points awarded for detecting/reporting this incident",
    )
    approval_notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "incident_report"
        verbose_name = "Incident Report"
        verbose_name_plural = "Incident Reports"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["team"]),
            models.Index(fields=["is_approved"]),
        ]

    def __str__(self) -> str:
        return f"IR-{self.pk}: {self.team.team_name} - {self.attack_description[:50]}"

    @property
    def affected_boxes_display(self) -> str:
        """Return display string for affected boxes."""
        return format_boxes_display(self.affected_boxes)


class IncidentScreenshot(models.Model):
    """Screenshots for incident reports (stored in database)."""

    incident = models.ForeignKey(
        IncidentReport,
        on_delete=models.CASCADE,
        related_name="screenshots",
    )
    file_data = models.BinaryField(null=True, blank=True)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, default="image/png")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "incident_screenshot"
        ordering = ["uploaded_at"]

    @property
    def has_data(self) -> bool:
        """Check if file data exists (for legacy records without data)."""
        return bool(self.file_data)

    def __str__(self) -> str:
        return f"{self.filename} ({self.incident})"


class InjectScore(models.Model):
    """White/Gold team scoring of inject submissions."""

    event = models.ForeignKey(
        "registration.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="inject_grades",
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="inject_grades",
    )

    # Inject info (inject definitions come from Quotient)
    inject_id = models.CharField(max_length=100, help_text="Quotient inject ID")
    inject_name = models.CharField(max_length=200)
    max_points = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Grading
    points_awarded = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    notes = models.TextField(blank=True, help_text="Grader notes")

    # Team-facing feedback (synthesized from raw judge comments)
    feedback = models.TextField(
        blank=True,
        help_text="Professional feedback shown to teams on scorecard",
    )
    feedback_approved = models.BooleanField(
        default=False,
        help_text="Whether feedback has been approved for team viewing",
    )
    feedback_approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inject_feedback_approved",
        help_text="User who approved this feedback",
    )

    # Approval tracking
    is_approved = models.BooleanField(
        default=False,
        help_text="Grade has been approved by supervisor",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the grade was approved",
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="injects_approved",
        help_text="User who approved this grade",
    )

    # Audit
    graded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="injects_graded",
    )
    graded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # View-layer annotations (not DB fields) — set by outlier calculation in views
    is_outlier: bool = False
    std_devs_from_mean: float = 0.0

    class Meta:
        db_table = "inject_grade"
        verbose_name = "Inject Score"
        verbose_name_plural = "Inject Scores"
        unique_together = [["team", "inject_id"]]
        ordering = ["inject_name", "team__team_number"]

    def __str__(self) -> str:
        if self.max_points:
            return f"{self.team.team_name} - {self.inject_name}: {self.points_awarded}/{self.max_points}"
        return f"{self.team.team_name} - {self.inject_name}: {self.points_awarded}"


class OrangeTeamScore(models.Model):
    """Orange team checks for customer service evaluation (positive or negative)."""

    event = models.ForeignKey(
        "registration.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="orange_team_scores",
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="orange_team_scores",
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="orange_scores_submitted",
    )

    # Check details
    description = models.TextField(help_text="Description of the orange team check")
    points_awarded = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Points to add (positive) or deduct (negative)",
    )

    # Approval tracking
    is_approved = models.BooleanField(
        default=False,
        help_text="Whether this check has been approved",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this check was approved",
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orange_scores_approved",
        help_text="User who approved this check",
    )
    orange_check = models.ForeignKey(
        "orange_team.OrangeCheck",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orange_scores",
        help_text="Orange check this score was created from (null for manual adjustments)",
    )

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orange_team_bonus"
        verbose_name = "Orange Team Check"
        verbose_name_plural = "Orange Team Checks"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        sign = "+" if self.points_awarded >= 0 else ""
        return f"{self.team.team_name} - {self.description[:50]}: {sign}{self.points_awarded}"


class ServiceScore(models.Model):
    """Service uptime scores from Quotient."""

    event = models.ForeignKey(
        "registration.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="service_scores",
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="service_scores",
    )

    # Scores from Quotient
    service_points = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
    )
    sla_violations = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text="SLA penalty points (stored as negative)",
    )
    point_adjustments = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Manual point adjustments (stored as negative)",
    )

    # Sync tracking
    synced_at = models.DateTimeField(auto_now=True)
    synced_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "service_score"
        verbose_name = "Service Score"
        verbose_name_plural = "Service Scores"
        unique_together = [["team"]]
        ordering = ["team__team_number"]

    def __str__(self) -> str:
        return f"{self.team.team_name}: {self.service_points} (SLA: {self.sla_violations})"


class ServiceDetail(models.Model):
    """Per-service uptime scores (e.g., balrog-ntp, brassknuckles-imap)."""

    event = models.ForeignKey(
        "registration.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="service_details",
    )
    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="service_details",
    )
    service_name = models.CharField(max_length=100, help_text="Service identifier (e.g., balrog-ntp)")
    points = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    uptime = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0"),
        help_text="Uptime percentage (0.0000–1.0000)",
    )

    class Meta:
        db_table = "service_detail"
        verbose_name = "Service Detail"
        verbose_name_plural = "Service Details"
        unique_together = [["team", "event", "service_name"]]
        ordering = ["service_name", "team__team_number"]

    def __str__(self) -> str:
        return f"{self.team.team_name} - {self.service_name}: {self.points}"


class FinalScore(models.Model):
    """Calculated final scores for leaderboard."""

    team = models.ForeignKey(
        "team.Team",
        on_delete=models.CASCADE,
        related_name="final_scores",
    )

    # Component scores
    service_points = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    inject_points = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    orange_points = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    red_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    incident_recovery_points = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    sla_penalties = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    point_adjustments = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))

    # Total and rank
    total_score = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    rank = models.IntegerField(null=True, blank=True)
    is_excluded = models.BooleanField(
        default=False,
        help_text="Exclude from comparative analysis and leaderboard",
    )

    # Calculation tracking
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "final_score"
        verbose_name = "Final Score"
        verbose_name_plural = "Final Scores"
        unique_together = [["team"]]
        ordering = ["-total_score", "team__team_number"]
        indexes = []

    def __str__(self) -> str:
        rank_str = f"#{self.rank}" if self.rank else "Unranked"
        return f"{rank_str} - {self.team.team_name}: {self.total_score}"
