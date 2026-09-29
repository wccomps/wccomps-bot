import csv
import json
import zipfile
from io import BytesIO, StringIO

from django.http import HttpResponse
from django.utils import timezone

from .models import (
    FinalScore,
    IncidentReport,
    InjectScore,
    OrangeTeamScore,
    RedTeamScore,
)


def _serialize_red_scores_csv() -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "Attack Vector",
            "Source IP",
            "Destination IP Template",
            "Affected Boxes",
            "Affected Service",
            "Affected Teams",
            "Points Per Team",
            "Universally Attempted",
            "Persistence Established",
            "Approved",
            "Approved By",
            "Approved At",
            "Submitted By",
            "Created At",
        ]
    )
    findings = RedTeamScore.objects.prefetch_related("affected_teams", "approved_by", "submitted_by").order_by(
        "-created_at"
    )
    for finding in findings:
        affected_teams = ", ".join(team.team_name for team in finding.affected_teams.all())
        affected_boxes = ", ".join(finding.affected_boxes) if finding.affected_boxes else ""
        writer.writerow(
            [
                finding.id,
                finding.attack_vector,
                finding.source_ip,
                finding.destination_ip_template,
                affected_boxes,
                finding.affected_service,
                affected_teams,
                finding.points_per_team,
                finding.universally_attempted,
                finding.persistence_established,
                finding.is_approved,
                finding.approved_by.username if finding.approved_by else "",
                finding.approved_at.isoformat() if finding.approved_at else "",
                finding.submitted_by.username if finding.submitted_by else "",
                finding.created_at.isoformat(),
            ]
        )
    return output.getvalue()


def _serialize_red_scores_json() -> str:
    findings = RedTeamScore.objects.prefetch_related("affected_teams", "approved_by", "submitted_by").order_by(
        "-created_at"
    )
    data = []
    for finding in findings:
        affected_teams = [team.team_name for team in finding.affected_teams.all()]
        data.append(
            {
                "id": finding.id,
                "attack_vector": finding.attack_vector,
                "source_ip": finding.source_ip,
                "destination_ip_template": finding.destination_ip_template,
                "affected_boxes": finding.affected_boxes,
                "affected_service": finding.affected_service,
                "affected_teams": affected_teams,
                "points_per_team": str(finding.points_per_team),
                "universally_attempted": finding.universally_attempted,
                "persistence_established": finding.persistence_established,
                "is_approved": finding.is_approved,
                "approved_by": finding.approved_by.username if finding.approved_by else None,
                "approved_at": finding.approved_at.isoformat() if finding.approved_at else None,
                "submitted_by": finding.submitted_by.username if finding.submitted_by else None,
                "created_at": finding.created_at.isoformat(),
            }
        )
    return json.dumps({"red_findings": data}, indent=2)


def export_red_scores_csv() -> HttpResponse:
    response = HttpResponse(_serialize_red_scores_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="red_findings.csv"'
    return response


def export_red_scores_json() -> HttpResponse:
    response = HttpResponse(_serialize_red_scores_json(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="red_findings.json"'
    return response


def _serialize_incidents_csv() -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "Team",
            "Attack Description",
            "Source IP",
            "Destination IP",
            "Affected Boxes",
            "Affected Service",
            "Attack Detected At",
            "Attack Mitigated",
            "Points Returned",
            "Reviewed",
            "Matched Finding ID",
            "Reviewed By",
            "Reviewed At",
            "Submitted By",
            "Created At",
        ]
    )
    incidents = IncidentReport.objects.select_related(
        "team", "submitted_by", "approved_by", "matched_to_red_score"
    ).order_by("-created_at")
    for incident in incidents:
        writer.writerow(
            [
                incident.id,
                incident.team.team_name,
                incident.attack_description,
                incident.source_ip,
                incident.destination_ip or "",
                ", ".join(incident.affected_boxes) if incident.affected_boxes else "",
                incident.affected_service,
                incident.attack_detected_at.isoformat(),
                incident.attack_mitigated,
                incident.points_returned,
                incident.is_approved,
                incident.matched_to_red_score.id if incident.matched_to_red_score else "",
                incident.approved_by.username if incident.approved_by else "",
                incident.approved_at.isoformat() if incident.approved_at else "",
                incident.submitted_by.username if incident.submitted_by else "",
                incident.created_at.isoformat(),
            ]
        )
    return output.getvalue()


def _serialize_incidents_json() -> str:
    incidents = IncidentReport.objects.select_related(
        "team", "submitted_by", "approved_by", "matched_to_red_score"
    ).order_by("-created_at")
    data = [
        {
            "id": incident.id,
            "team": incident.team.team_name,
            "team_number": incident.team.team_number,
            "attack_description": incident.attack_description,
            "source_ip": incident.source_ip,
            "destination_ip": incident.destination_ip,
            "affected_boxes": incident.affected_boxes,
            "affected_service": incident.affected_service,
            "attack_detected_at": incident.attack_detected_at.isoformat(),
            "attack_mitigated": incident.attack_mitigated,
            "points_returned": str(incident.points_returned),
            "is_approved": incident.is_approved,
            "matched_to_red_score_id": (incident.matched_to_red_score.id if incident.matched_to_red_score else None),
            "approved_by": incident.approved_by.username if incident.approved_by else None,
            "approved_at": incident.approved_at.isoformat() if incident.approved_at else None,
            "submitted_by": incident.submitted_by.username if incident.submitted_by else None,
            "created_at": incident.created_at.isoformat(),
        }
        for incident in incidents
    ]
    return json.dumps({"incidents": data}, indent=2)


def export_incidents_csv() -> HttpResponse:
    response = HttpResponse(_serialize_incidents_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="incidents.csv"'
    return response


def export_incidents_json() -> HttpResponse:
    response = HttpResponse(_serialize_incidents_json(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="incidents.json"'
    return response


def _serialize_orange_adjustments_csv() -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ID",
            "Team",
            "Description",
            "Points",
            "Approved",
            "Approved By",
            "Approved At",
            "Submitted By",
            "Created At",
        ]
    )
    bonuses = OrangeTeamScore.objects.select_related("team", "submitted_by", "approved_by").order_by("-created_at")
    for bonus in bonuses:
        writer.writerow(
            [
                bonus.id,
                bonus.team.team_name,
                bonus.description,
                bonus.points_awarded,
                bonus.is_approved,
                bonus.approved_by.username if bonus.approved_by else "",
                bonus.approved_at.isoformat() if bonus.approved_at else "",
                bonus.submitted_by.username if bonus.submitted_by else "",
                bonus.created_at.isoformat(),
            ]
        )
    return output.getvalue()


def _serialize_orange_adjustments_json() -> str:
    bonuses = OrangeTeamScore.objects.select_related("team", "submitted_by", "approved_by").order_by("-created_at")
    data = [
        {
            "id": bonus.id,
            "team": bonus.team.team_name,
            "team_number": bonus.team.team_number,
            "description": bonus.description,
            "points_awarded": str(bonus.points_awarded),
            "is_approved": bonus.is_approved,
            "approved_by": bonus.approved_by.username if bonus.approved_by else None,
            "approved_at": bonus.approved_at.isoformat() if bonus.approved_at else None,
            "submitted_by": bonus.submitted_by.username if bonus.submitted_by else None,
            "created_at": bonus.created_at.isoformat(),
        }
        for bonus in bonuses
    ]
    return json.dumps({"orange_checks": data}, indent=2)


def export_orange_adjustments_csv() -> HttpResponse:
    response = HttpResponse(_serialize_orange_adjustments_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="orange_checks.csv"'
    return response


def export_orange_adjustments_json() -> HttpResponse:
    response = HttpResponse(_serialize_orange_adjustments_json(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="orange_checks.json"'
    return response


def _serialize_inject_grades_csv() -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Team",
            "Team Number",
            "Inject ID",
            "Inject Name",
            "Max Points",
            "Points Awarded",
            "Approved",
            "Approved By",
            "Approved At",
            "Graded By",
            "Graded At",
        ]
    )
    grades = InjectScore.objects.select_related("team", "graded_by", "approved_by").order_by(
        "inject_name", "team__team_number"
    )
    for grade in grades:
        writer.writerow(
            [
                grade.team.team_name,
                grade.team.team_number,
                grade.inject_id,
                grade.inject_name,
                grade.max_points,
                grade.points_awarded,
                grade.is_approved,
                grade.approved_by.username if grade.approved_by else "",
                grade.approved_at.isoformat() if grade.approved_at else "",
                grade.graded_by.username if grade.graded_by else "",
                grade.graded_at.isoformat(),
            ]
        )
    return output.getvalue()


def _serialize_inject_grades_json() -> str:
    grades = InjectScore.objects.select_related("team", "graded_by", "approved_by").order_by(
        "inject_name", "team__team_number"
    )
    data = [
        {
            "team": grade.team.team_name,
            "team_number": grade.team.team_number,
            "inject_id": grade.inject_id,
            "inject_name": grade.inject_name,
            "max_points": str(grade.max_points),
            "points_awarded": str(grade.points_awarded),
            "is_approved": grade.is_approved,
            "approved_by": grade.approved_by.username if grade.approved_by else None,
            "approved_at": grade.approved_at.isoformat() if grade.approved_at else None,
            "graded_by": grade.graded_by.username if grade.graded_by else None,
            "graded_at": grade.graded_at.isoformat(),
        }
        for grade in grades
    ]
    return json.dumps({"inject_grades": data}, indent=2)


def export_inject_grades_csv() -> HttpResponse:
    response = HttpResponse(_serialize_inject_grades_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="inject_grades.csv"'
    return response


def export_inject_grades_json() -> HttpResponse:
    response = HttpResponse(_serialize_inject_grades_json(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="inject_grades.json"'
    return response


def _serialize_final_scores_csv() -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Rank",
            "Team",
            "Team Number",
            "Total Score",
            "Service Points",
            "Inject Points",
            "Orange Points",
            "Red Deductions",
            "Incident Recovery Points",
            "SLA Penalties",
            "Calculated At",
        ]
    )
    scores = FinalScore.objects.select_related("team").order_by("-total_score", "team__team_number")
    for score in scores:
        writer.writerow(
            [
                score.rank or "",
                score.team.team_name,
                score.team.team_number,
                score.total_score,
                score.service_points,
                score.inject_points,
                score.orange_points,
                score.red_deductions,
                score.incident_recovery_points,
                score.sla_penalties,
                score.calculated_at.isoformat(),
            ]
        )
    return output.getvalue()


def _serialize_final_scores_json() -> str:
    scores = FinalScore.objects.select_related("team").order_by("-total_score", "team__team_number")
    data = [
        {
            "rank": score.rank,
            "team": score.team.team_name,
            "team_number": score.team.team_number,
            "total_score": str(score.total_score),
            "service_points": str(score.service_points),
            "inject_points": str(score.inject_points),
            "orange_points": str(score.orange_points),
            "red_deductions": str(score.red_deductions),
            "incident_recovery_points": str(score.incident_recovery_points),
            "sla_penalties": str(score.sla_penalties),
            "calculated_at": score.calculated_at.isoformat(),
        }
        for score in scores
    ]
    return json.dumps({"final_scores": data}, indent=2)


def export_final_scores_csv() -> HttpResponse:
    response = HttpResponse(_serialize_final_scores_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="final_scores.csv"'
    return response


def export_final_scores_json() -> HttpResponse:
    response = HttpResponse(_serialize_final_scores_json(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="final_scores.json"'
    return response


def _serialize_tickets_csv() -> str:
    from ticketing.models import Ticket

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Ticket Number",
            "Team",
            "Team Number",
            "Category",
            "Title",
            "Description",
            "Status",
            "Points Charged",
            "Hostname",
            "IP Address",
            "Service Name",
            "Assigned To",
            "Assigned At",
            "Resolved By",
            "Resolved At",
            "Resolution Notes",
            "Approved",
            "Approved By",
            "Approved At",
            "Created At",
        ]
    )
    tickets = Ticket.objects.select_related("team", "category", "assigned_to", "resolved_by", "approved_by").order_by(
        "-created_at"
    )
    for ticket in tickets:
        writer.writerow(
            [
                ticket.ticket_number,
                ticket.team.team_name,
                ticket.team.team_number,
                ticket.category.display_name if ticket.category else "",
                ticket.title,
                ticket.description,
                ticket.status,
                ticket.points_charged,
                ticket.hostname,
                ticket.ip_address or "",
                ticket.service_name,
                ticket.assigned_to.username if ticket.assigned_to else "",
                ticket.assigned_at.isoformat() if ticket.assigned_at else "",
                ticket.resolved_by.username if ticket.resolved_by else "",
                ticket.resolved_at.isoformat() if ticket.resolved_at else "",
                ticket.resolution_notes,
                ticket.is_approved,
                ticket.approved_by.username if ticket.approved_by else "",
                ticket.approved_at.isoformat() if ticket.approved_at else "",
                ticket.created_at.isoformat(),
            ]
        )
    return output.getvalue()


def _serialize_tickets_json() -> str:
    from ticketing.models import Ticket

    tickets = Ticket.objects.select_related("team", "category", "assigned_to", "resolved_by", "approved_by").order_by(
        "-created_at"
    )
    data = [
        {
            "ticket_number": ticket.ticket_number,
            "team": ticket.team.team_name,
            "team_number": ticket.team.team_number,
            "category": ticket.category.display_name if ticket.category else None,
            "title": ticket.title,
            "description": ticket.description,
            "status": ticket.status,
            "points_charged": ticket.points_charged,
            "hostname": ticket.hostname,
            "ip_address": ticket.ip_address,
            "service_name": ticket.service_name,
            "assigned_to": ticket.assigned_to.username if ticket.assigned_to else None,
            "assigned_at": ticket.assigned_at.isoformat() if ticket.assigned_at else None,
            "resolved_by": ticket.resolved_by.username if ticket.resolved_by else None,
            "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
            "resolution_notes": ticket.resolution_notes,
            "is_approved": ticket.is_approved,
            "approved_by": ticket.approved_by.username if ticket.approved_by else None,
            "approved_at": ticket.approved_at.isoformat() if ticket.approved_at else None,
            "created_at": ticket.created_at.isoformat(),
        }
        for ticket in tickets
    ]
    return json.dumps({"tickets": data}, indent=2)


def export_tickets_csv() -> HttpResponse:
    response = HttpResponse(_serialize_tickets_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="tickets.csv"'
    return response


def export_tickets_json() -> HttpResponse:
    response = HttpResponse(_serialize_tickets_json(), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="tickets.json"'
    return response


def export_all_zip() -> HttpResponse:
    """Export all scoring data as a zip file containing CSV and JSON files."""
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("red_findings.csv", _serialize_red_scores_csv())
        zip_file.writestr("red_findings.json", _serialize_red_scores_json())
        zip_file.writestr("incidents.csv", _serialize_incidents_csv())
        zip_file.writestr("incidents.json", _serialize_incidents_json())
        zip_file.writestr("orange_checks.csv", _serialize_orange_adjustments_csv())
        zip_file.writestr("orange_checks.json", _serialize_orange_adjustments_json())
        zip_file.writestr("inject_grades.csv", _serialize_inject_grades_csv())
        zip_file.writestr("inject_grades.json", _serialize_inject_grades_json())
        zip_file.writestr("final_scores.csv", _serialize_final_scores_csv())
        zip_file.writestr("final_scores.json", _serialize_final_scores_json())
        zip_file.writestr("tickets.csv", _serialize_tickets_csv())
        zip_file.writestr("tickets.json", _serialize_tickets_json())

    zip_buffer.seek(0)
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="wccomps_export_{timestamp}.zip"'
    return response
