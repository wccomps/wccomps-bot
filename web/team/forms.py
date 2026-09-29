import csv
import io
import random
from typing import TypedDict, cast

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import validate_email

from team.models import SchoolInfo, Team


def _infer_header_mapping(fieldnames: list[str]) -> dict[str, str] | None:
    """Infer canonical column names by inspecting header text.

    Maps other email columns (contain 'email'), in order, to whichever of contact_email and
    secondary_email is still free; any beyond those are ignored. Assigns the remaining
    unmapped column as school_name. Returns None if headers already canonical.
    """
    canonical = {"school_name", "contact_email", "secondary_email", "notes"}
    normalized = {f: f.strip().lower().replace(" ", "_") for f in fieldnames}

    if set(normalized.values()) <= canonical:
        return None

    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    skipped: list[str] = []
    # Email slots not already taken by a column named exactly that
    email_columns = iter(c for c in ("contact_email", "secondary_email") if c not in normalized.values())
    for raw in fieldnames:
        norm = raw.strip().lower()
        if norm.replace(" ", "_") in canonical:
            mapping[raw] = norm.replace(" ", "_")
        elif "email" in norm:
            mapping[raw] = next(email_columns, norm.replace(" ", "_"))
        elif "team" in norm or norm.replace(" ", "").replace("#", "").isdigit():
            # Skip team number columns — teams are assigned randomly
            skipped.append(raw)
        else:
            unmapped.append(raw)

    # Single remaining unmapped column is the school name
    if len(unmapped) == 1 and "school_name" not in mapping.values():
        mapping[unmapped[0]] = "school_name"
    else:
        for raw in unmapped:
            mapping[raw] = raw.strip().lower().replace(" ", "_")

    # Include skipped columns so they appear in the warning but get ignored
    for raw in skipped:
        mapping[raw] = raw.strip().lower().replace(" ", "_")

    return mapping


class CSVRowData(TypedDict, total=False):
    school_name: str
    contact_email: str
    secondary_email: str
    notes: str
    team_number: int
    _team: Team


class CSVParseResult(TypedDict):
    rows: list[CSVRowData]
    errors: list[str]
    warnings: list[str]


class CSVValidationResult(TypedDict):
    teams_to_create: list[CSVRowData]
    errors: list[str]
    warnings: list[str]


class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV File",
        help_text=(
            "Upload a CSV file with team school information. "
            "Required columns: school_name, contact_email. "
            "Optional: secondary_email, notes. "
            "Column names are auto-detected from headers."
        ),
    )

    def clean_csv_file(self) -> UploadedFile[bytes]:
        csv_file = cast(UploadedFile[bytes], self.cleaned_data["csv_file"])

        if not csv_file.name or not csv_file.name.endswith(".csv"):
            raise ValidationError("File must be a CSV file (.csv)")

        if csv_file.size and csv_file.size > 10 * 1024 * 1024:
            raise ValidationError("File size must be less than 10MB")

        return csv_file


def parse_csv_file(csv_file: UploadedFile[bytes]) -> CSVParseResult:
    """Parse CSV file and validate contents."""
    rows: list[CSVRowData] = []
    errors: list[str] = []
    warnings: list[str] = []

    try:
        content = csv_file.read().decode("utf-8")
        csv_file.seek(0)

        reader = csv.DictReader(io.StringIO(content))

        required_headers = {"school_name", "contact_email"}
        optional_headers = {"secondary_email", "notes"}
        all_headers = required_headers | optional_headers

        if not reader.fieldnames:
            errors.append("CSV file is empty or has no headers")
            return {"rows": rows, "errors": errors, "warnings": warnings}

        header_mapping = _infer_header_mapping(list(reader.fieldnames))
        if header_mapping:
            mapped_names = set(header_mapping.values())
            warnings.append(
                "Columns auto-detected: " + ", ".join(f"{raw} \u2192 {canon}" for raw, canon in header_mapping.items())
            )
        else:
            mapped_names = set(reader.fieldnames)

        headers = mapped_names

        missing_headers = required_headers - headers
        if missing_headers:
            errors.append(f"Missing required columns: {', '.join(sorted(missing_headers))}")
            return {"rows": rows, "errors": errors, "warnings": warnings}

        unknown_headers = headers - all_headers
        if unknown_headers:
            warnings.append(f"Unknown columns will be ignored: {', '.join(sorted(unknown_headers))}")

        for row_num, raw_row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            row = {header_mapping.get(k, k): v for k, v in raw_row.items()} if header_mapping else raw_row

            row_errors: list[str] = []
            row_data: CSVRowData = {}

            school_name = row.get("school_name", "").strip()
            if not school_name:
                row_errors.append(f"Row {row_num}: school_name is required")
            else:
                row_data["school_name"] = school_name

            contact_email = row.get("contact_email", "").strip()
            if not contact_email:
                row_errors.append(f"Row {row_num}: contact_email is required")
            else:
                try:
                    validate_email(contact_email)
                    row_data["contact_email"] = contact_email
                except ValidationError:
                    row_errors.append(f"Row {row_num}: contact_email is not a valid email address")

            secondary_email = row.get("secondary_email", "").strip()
            if secondary_email:
                try:
                    validate_email(secondary_email)
                    row_data["secondary_email"] = secondary_email
                except ValidationError:
                    row_errors.append(f"Row {row_num}: secondary_email is not a valid email address")
            else:
                row_data["secondary_email"] = ""

            notes = row.get("notes", "").strip()
            row_data["notes"] = notes

            if row_errors:
                errors.extend(row_errors)
            else:
                rows.append(row_data)

        if not rows and not errors:
            errors.append("CSV file contains no data rows")

    except UnicodeDecodeError:
        errors.append("File encoding error. Please ensure the file is UTF-8 encoded.")
    except Exception as e:
        errors.append(f"Error parsing CSV file: {str(e)}")

    return {"rows": rows, "errors": errors, "warnings": warnings}


def validate_csv_data(rows: list[CSVRowData]) -> CSVValidationResult:
    """Validate CSV data against database and assign random team numbers."""
    teams_to_create: list[CSVRowData] = []
    errors: list[str] = []
    warnings: list[str] = []

    num_rows = len(rows)
    existing_school_info_team_ids = set(SchoolInfo.objects.values_list("team_id", flat=True))
    available_teams = list(
        Team.objects.filter(is_active=True)
        .exclude(id__in=existing_school_info_team_ids)
        .order_by("team_number")[:num_rows]
    )

    if len(available_teams) < num_rows:
        errors.append(
            f"Not enough available teams. CSV has {num_rows} rows but only "
            f"{len(available_teams)} teams without school info."
        )
        return {
            "teams_to_create": teams_to_create,
            "errors": errors,
            "warnings": warnings,
        }

    random.shuffle(available_teams)

    for i, row in enumerate(rows):
        team = available_teams[i]
        row["_team"] = team
        row["team_number"] = team.team_number
        teams_to_create.append(row)

    return {
        "teams_to_create": teams_to_create,
        "errors": errors,
        "warnings": warnings,
    }


def apply_csv_import(
    teams_to_create: list[CSVRowData],
    updated_by: str,
) -> dict[str, int]:
    """Create SchoolInfo for each team and assign it to the active event, returning created/assigned counts."""
    from registration.models import Event, EventTeamAssignment, Season, TeamRegistration

    created = 0
    assigned = 0

    season = Season.objects.filter(is_active=True).first()
    event = Event.objects.filter(is_active=True, season=season).first() if season else None

    for row in teams_to_create:
        team = row["_team"]
        SchoolInfo.objects.create(
            team=team,
            school_name=row["school_name"],
            contact_email=row["contact_email"],
            secondary_email=row.get("secondary_email", ""),
            notes=row.get("notes", ""),
            updated_by=updated_by,
        )
        created += 1

        if event:
            registration, _ = TeamRegistration.objects.get_or_create(
                school_name=row["school_name"],
                defaults={"status": "approved"},
            )
            _, eta_created = EventTeamAssignment.objects.get_or_create(
                event=event,
                team=team,
                defaults={"registration": registration},
            )
            if eta_created:
                assigned += 1

    return {"created": created, "assigned": assigned}
