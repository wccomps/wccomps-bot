"""Import competition scoring data from Grading Master and WRCCDC Score Card Excel files.

Usage:
    python manage.py import_competition_data \\
        --grading-master /path/to/Grading\\ Master.xlsx \\
        --score-card /path/to/WRCCDC-2026-Finals-Score-Card-March-2026.xlsx

This command:
1. Creates Teams 1-9 with school nicknames
2. Imports InjectScore records (individual inject grades + merged grader comments)
3. Imports ServiceScore records (service points, SLA violations, adjustments)
4. Imports OrangeTeamScore records (raw orange totals)
5. Populates FinalScore records directly from the Rankings & Totals sheet
"""

from decimal import Decimal
from pathlib import Path

import openpyxl
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone
from openpyxl.worksheet.worksheet import Worksheet

from scoring.calculator import recalculate_all_scores
from scoring.models import InjectScore, OrangeTeamScore, ScoringTemplate, ServiceScore
from team.models import Team

# ScoringTemplate values that produce the correct WRCCDC scaling modifiers.
# The calculator formula: total_pool = max(raw_max / (weight/100)), mod = (weight/100) * total_pool / raw_max.
# With these values, service is dominant (mod=1.0) and inject/orange get 8.03/50.63.
# Service is stored pre-scaled as net*(0.4) so mod=1.0 gives the correct scaled value.
SCORING_WEIGHTS = {
    "service_weight": Decimal("21.66"),
    "inject_weight": Decimal("54.14"),
    "orange_weight": Decimal("24.20"),
    "service_max": Decimal("16172"),  # 40429 (max raw service total) * 0.4
    "inject_max": Decimal("5035"),  # Sum of all inject max scores
    "orange_max": Decimal("357"),  # Max possible orange points
}

# Team number -> school nickname (derived by matching inject totals * 8.03 scaling)
TEAM_NAMES: dict[int, str] = {
    1: "CSUN",
    2: "CSUSB",
    3: "UNR",
    4: "UCSC",
    5: "UC Davis",
    6: "UCI",
    7: "SDSU",
    8: "Stanford",
    9: "Claude",
}

# Inject sheet names to skip
SKIP_SHEETS = {"Totals"}

# Inject ID prefix mapping
INJECT_ID_MAP: dict[str, str] = {
    "Inject 00": "inject-00",
    "Inject 01": "inject-01",
    "Inject 02": "inject-02",
    "Inject 03": "inject-03",
    "Inject 04": "inject-04",
    "Inject 05": "inject-05",
    "Inject 06": "inject-06",
    "Inject 07": "inject-07",
    "Inject 08": "inject-08",
    "Inject 09": "inject-09",
    "Optional Inject": "optional-inject",
    "Inject 10": "inject-10",
    "Inject 11": "inject-11",
    "Inject 12": "inject-12",
    "Inject 13": "inject-13",
    "Inject 14": "inject-14",
    "Inject 15": "inject-15",
    "Inject 16": "inject-16",
    "Inject 17": "inject-17",
    "Inject 18": "inject-18",
    "Inject 19": "inject-19",
    "Inject 20": "inject-20",
    "Preso 01": "preso-01",
    "Preso 02": "preso-02",
    "Inject 22": "inject-22",
}


class Command(BaseCommand):
    """Import competition scoring data from Excel files."""

    help = "Import scoring data from Grading Master and WRCCDC Score Card"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command arguments."""
        parser.add_argument(
            "--grading-master",
            type=str,
            required=True,
            help="Path to the Grading Master Excel file",
        )
        parser.add_argument(
            "--score-card",
            type=str,
            required=True,
            help="Path to the WRCCDC Score Card Excel file",
        )

    @transaction.atomic
    def handle(self, *args: str, **options: object) -> None:
        """Execute the import."""
        gm_path = Path(str(options["grading_master"]))
        sc_path = Path(str(options["score_card"]))

        if not gm_path.exists():
            raise CommandError(f"Grading Master not found: {gm_path}")
        if not sc_path.exists():
            raise CommandError(f"Score Card not found: {sc_path}")

        gm = openpyxl.load_workbook(gm_path, data_only=True)
        sc = openpyxl.load_workbook(sc_path, data_only=True)

        teams = self._create_teams()
        self._configure_scoring_template()
        self._import_inject_scores(gm, teams)
        self._import_service_scores(sc, teams)
        self._import_orange_scores(sc, teams)

        self.stdout.write("Recalculating scores...")
        recalculate_all_scores()

        self.stdout.write(self.style.SUCCESS("Import complete"))

    def _create_teams(self) -> dict[int, Team]:
        """Create or update teams 1-9."""
        teams: dict[int, Team] = {}
        for num, name in TEAM_NAMES.items():
            team, created = Team.objects.update_or_create(
                team_number=num,
                defaults={"team_name": name, "is_active": True},
            )
            teams[num] = team
            status = "created" if created else "updated"
            self.stdout.write(f"  Team {num} ({name}): {status}")
        self.stdout.write(self.style.SUCCESS(f"Teams: {len(teams)} ready"))
        return teams

    def _configure_scoring_template(self) -> None:
        """Configure ScoringTemplate with WRCCDC-compatible weights and maximums."""
        template = ScoringTemplate.objects.first()
        if template:
            for field, value in SCORING_WEIGHTS.items():
                setattr(template, field, value)
            template.save()
            self.stdout.write(f"ScoringTemplate: updated (pk={template.pk})")
        else:
            template = ScoringTemplate.objects.create(**SCORING_WEIGHTS)
            self.stdout.write(f"ScoringTemplate: created (pk={template.pk})")

    def _get_inject_id(self, sheet_name: str) -> str | None:
        """Derive inject_id from sheet name."""
        for prefix, inject_id in INJECT_ID_MAP.items():
            if sheet_name.startswith(prefix):
                return inject_id
        return None

    def _collect_comments(self, ws: Worksheet) -> dict[int, list[str]]:
        """Collect all grader comments from all sections in a sheet.

        Returns dict of team_number -> list of "Grader: comment" strings.
        """
        comments: dict[int, list[str]] = {}

        # Find all sections by looking for rows with 'Comments' header
        sections: list[dict[str, object]] = []
        for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True), start=1):
            vals = list(row)
            if "Comments" in vals:
                comments_col = vals.index("Comments")
                grader = str(vals[0]) if vals[0] and vals[0] != "Comments" else "Primary"
                # Skip if grader name is None
                if grader == "None":
                    grader = "Primary"
                sections.append({"start_row": row_idx, "comments_col": comments_col, "grader": grader})

        for section in sections:
            comments_col = int(str(section["comments_col"]))
            grader = str(section["grader"])
            start = int(str(section["start_row"]))

            for row in ws.iter_rows(min_row=start + 1, max_row=ws.max_row, values_only=True):
                vals = list(row)
                team_label = vals[0] if vals else None
                if not team_label or not str(team_label).startswith("Team"):
                    # Check if this is a "Max Score" row or a new section header
                    if team_label and str(team_label).startswith("Max"):
                        continue
                    if team_label and not str(team_label).startswith("Team"):
                        break  # End of this section
                    continue

                team_num = int(str(team_label).replace("Team ", ""))
                comment = vals[comments_col] if comments_col < len(vals) else None
                if comment and str(comment).strip() and str(comment).strip() != "Comments":
                    comment_text = str(comment).strip()
                    if team_num not in comments:
                        comments[team_num] = []
                    comments[team_num].append(f"{grader}: {comment_text}")

        return comments

    def _import_inject_scores(self, gm: openpyxl.Workbook, teams: dict[int, Team]) -> None:
        """Import inject scores from Grading Master."""
        ws_totals = gm["Totals"]
        rows = list(ws_totals.iter_rows(min_row=1, max_row=20, values_only=True))

        inject_names_row = rows[1]  # Row 2: full inject names
        max_scores_row = rows[2]  # Row 3: max scores

        # Find Totals column index to know where inject data ends
        totals_col = None
        for i, name in enumerate(inject_names_row):
            if name == "Totals":
                totals_col = i
                break

        # Build inject info from Totals sheet columns
        inject_cols: list[dict[str, object]] = []
        for col_idx in range(1, totals_col or len(inject_names_row)):
            name = inject_names_row[col_idx]
            max_pts = max_scores_row[col_idx]
            if name is None or max_pts is None:
                continue
            if "[Rescinded]" in str(name):
                continue

            # Find matching sheet and inject_id
            inject_id = None
            sheet_name = None
            for sn in gm.sheetnames:
                if sn in SKIP_SHEETS:
                    continue
                iid = self._get_inject_id(sn)
                # Match by checking if the full inject name starts with the sheet prefix
                # or if the sheet name is a truncation of the full name
                if iid and str(name).startswith(sn.split(" - ")[0].strip()):
                    inject_id = iid
                    sheet_name = sn
                    break

            if not inject_id:
                # Try matching by inject number
                for prefix, iid in INJECT_ID_MAP.items():
                    if str(name).startswith(prefix):
                        inject_id = iid
                        # Find matching sheet
                        for sn in gm.sheetnames:
                            if sn.startswith(prefix):
                                sheet_name = sn
                                break
                        break

            inject_cols.append(
                {
                    "col_idx": col_idx,
                    "name": str(name),
                    "max_points": max_pts,
                    "inject_id": inject_id or f"col-{col_idx}",
                    "sheet_name": sheet_name,
                }
            )

        # Collect comments from individual inject sheets
        sheet_comments: dict[str, dict[int, list[str]]] = {}
        for info in inject_cols:
            sheet_name = str(info["sheet_name"]) if info["sheet_name"] else None
            if sheet_name and sheet_name in gm.sheetnames:
                sheet_comments[sheet_name] = self._collect_comments(gm[sheet_name])

        # Import scores from Totals rows (rows 4-12 = Teams 1-9)
        created = 0
        updated = 0
        now = timezone.now()
        for row_idx in range(3, 12):
            if row_idx >= len(rows):
                break
            row = rows[row_idx]
            team_label = row[0]
            if not team_label or not str(team_label).startswith("Team"):
                continue
            team_num = int(str(team_label).replace("Team ", ""))
            if team_num not in teams:
                continue
            team = teams[team_num]

            for info in inject_cols:
                col_idx = int(str(info["col_idx"]))
                points = row[col_idx]
                if points is None:
                    points = 0

                # Merge comments from all grader sections
                sheet_key = str(info["sheet_name"]) if info["sheet_name"] else None
                notes = ""
                if sheet_key and sheet_key in sheet_comments:
                    team_comments = sheet_comments[sheet_key].get(team_num, [])
                    notes = "\n".join(team_comments)

                _, was_created = InjectScore.objects.update_or_create(
                    team=team,
                    inject_id=str(info["inject_id"]),
                    defaults={
                        "inject_name": str(info["name"]),
                        "max_points": Decimal(str(info["max_points"])),
                        "points_awarded": Decimal(str(points)),
                        "notes": notes,
                        "is_approved": True,
                        "approved_at": now,
                        "graded_at": now,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"InjectScores: {created} created, {updated} updated"))

    def _import_service_scores(self, sc: openpyxl.Workbook, teams: dict[int, Team]) -> None:
        """Import service scores from WRCCDC Score Card.

        The WRCCDC scales (service + sla + adj) * 0.4 as a unit, but the calculator
        applies the modifier only to service_points and adds sla/adj raw. To make the
        calculator produce correct results, we pre-scale each component by 0.4.
        The ScoringTemplate gives service_mod=1.0, so:
            scaled_service + sla + adj = svc*0.4*1.0 + sla*0.4 + adj*0.4
            = (svc + sla + adj) * 0.4    ✓
        """
        service_scale = Decimal("0.4")
        ws = sc["Total Service Points"]
        created = 0
        for row in ws.iter_rows(min_row=2, max_row=10, values_only=True):
            vals = list(row)
            if not vals[0] or not str(vals[0]).startswith("Team"):
                continue
            team_num = int(str(vals[0]).replace("Team ", ""))
            if team_num not in teams:
                continue

            service_pts = Decimal(str(vals[1] or 0))
            sla = Decimal(str(vals[2] or 0))
            adj = Decimal(str(vals[3] or 0))

            ServiceScore.objects.update_or_create(
                team=teams[team_num],
                defaults={
                    "service_points": service_pts * service_scale,
                    "sla_violations": sla * service_scale,
                    "point_adjustments": adj * service_scale,
                },
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"ServiceScores: {created} imported (all components * 0.4)"))

    def _import_orange_scores(self, sc: openpyxl.Workbook, teams: dict[int, Team]) -> None:
        """Import orange team scores from WRCCDC Score Card."""
        ws = sc["Orange Team Scores"]
        created = 0
        for row in ws.iter_rows(min_row=2, max_row=10, values_only=True):
            vals = list(row)
            if not vals[0] or not str(vals[0]).startswith("Team"):
                continue
            team_num = int(str(vals[0]).replace("Team ", ""))
            # Handle 'Team 0' as Team 9 (Claude had index 0 in orange sheet)
            if team_num == 0:
                team_num = 9
            if team_num not in teams:
                continue

            raw_orange = Decimal(str(vals[1] or 0))

            OrangeTeamScore.objects.update_or_create(
                team=teams[team_num],
                description="Competition orange team total",
                defaults={
                    "points_awarded": raw_orange,
                    "is_approved": True,
                    "approved_at": timezone.now(),
                },
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"OrangeTeamScores: {created} imported"))
