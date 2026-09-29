from collections.abc import Iterator

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_POST

from core.auth_utils import require_permission
from core.utils import ndjson_progress
from team.models import Team

from ..models import FinalScore


@require_permission("gold_team", error_message="Only Gold Team members can access this")
def export_index(request: HttpRequest) -> HttpResponse:
    from django.shortcuts import render

    return render(request, "scoring/export_index.html")


@require_permission("gold_team", error_message="Only Gold Team members can access this")
def export_dataset(request: HttpRequest, dataset: str) -> HttpResponse:
    from .. import export

    return export.export_dataset(dataset, request.GET.get("format", "csv").lower())


@require_permission("gold_team", error_message="Only Gold Team members can access this")
def export_all(request: HttpRequest) -> HttpResponse:
    """Export all scoring data as a zip file (admin only)."""
    from ..export import export_all_zip

    return export_all_zip()


@require_permission("gold_team", error_message="Only Gold Team members can access this")
def export_scorecards(request: HttpRequest) -> HttpResponse:
    """Export all team scorecards as a zip of PDFs."""
    import io
    import zipfile

    import weasyprint
    from django.template.loader import render_to_string
    from django.utils import timezone

    from ..calculator import calculate_team_score_detailed
    from ..models import FinalScore, RedTeamScore
    from .leaderboard import _compute_scorecard_stats

    scores = FinalScore.objects.filter(is_excluded=False, rank__isnull=False).select_related("team").order_by("rank")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for final_score in scores:
            team = final_score.team
            red_scores = (
                RedTeamScore.objects.filter(affected_teams=team, is_approved=True)
                .select_related("attack_type")
                .order_by("attack_type__name", "pk")
            )

            stats = _compute_scorecard_stats(team, final_score)
            detailed = calculate_team_score_detailed(team)

            context = {
                "team": team,
                "score": final_score,
                "red_scores": red_scores,
                "stats": stats,
                "red_total": sum(r.points_per_team for r in red_scores),
                "inject_total": sum(i["points"] for i in stats["inject_stats"]),
                "service_total": sum(s["points"] for s in stats["service_stats"]),
                "scaling": {
                    "service_raw": detailed["service_raw"],
                    "inject_raw": detailed["inject_raw"],
                    "orange_raw": detailed["orange_raw"],
                    "service_modifier": detailed["service_modifier"],
                    "inject_modifier": detailed["inject_modifier"],
                    "orange_modifier": detailed["orange_modifier"],
                    "service_weight": detailed["service_weight"],
                    "inject_weight": detailed["inject_weight"],
                    "orange_weight": detailed["orange_weight"],
                },
            }

            html_string = render_to_string("scoring/scorecard_print.html", context, request=request)
            pdf_bytes = weasyprint.HTML(string=html_string).write_pdf()
            zf.writestr(f"team-{team.team_number:02d}-scorecard.pdf", pdf_bytes)

    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    response = HttpResponse(buf.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="scorecards-{timestamp}.zip"'
    return response


def _send_scorecard_email(
    recipients: list[str], context: dict[str, object], team_number: int, pdf_bytes: bytes
) -> bool:
    """Send a scorecard email using Django's email backend."""
    import logging

    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    logger = logging.getLogger(__name__)

    try:
        subject = render_to_string("emails/scorecard_subject.txt", context).strip()
        text_content = render_to_string("emails/scorecard.txt", context)
        html_content = render_to_string("emails/scorecard.html", context)

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        email.attach_alternative(html_content, "text/html")
        email.attach(f"team-{team_number:02d}-scorecard.pdf", pdf_bytes, "application/pdf")
        email.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send scorecard email to Team %d", team_number)
        return False


def _build_email_context(team: Team, score: FinalScore, total_teams: int) -> dict[str, object]:
    """Build email template context for a team's scorecard."""
    from team.models import SchoolInfo

    from ..models import QuotientMetadataCache

    metadata = QuotientMetadataCache.objects.first()
    event_name = metadata.event_name if metadata else "Competition"

    try:
        school_info = team.school_info
        school_name = school_info.school_name
    except SchoolInfo.DoesNotExist:
        school_name = team.team_name

    return {
        "event_name": event_name,
        "event_date": score.calculated_at,
        "school_name": school_name,
        "team_number": team.team_number,
        "service_points": score.service_points,
        "inject_points": score.inject_points,
        "orange_points": score.orange_points,
        "red_deductions": score.red_deductions,
        "incident_recovery_points": score.incident_recovery_points,
        "sla_penalties": score.sla_penalties,
        "total_score": score.total_score,
        "rank": score.rank,
        "total_teams": total_teams,
        "scorecard_attached": True,
    }


def _generate_team_pdf(team: Team, score: FinalScore, request: HttpRequest) -> bytes:
    import weasyprint
    from django.template.loader import render_to_string

    from ..calculator import calculate_team_score_detailed
    from ..models import RedTeamScore
    from .leaderboard import _compute_scorecard_stats

    red_scores = (
        RedTeamScore.objects.filter(affected_teams=team, is_approved=True)
        .select_related("attack_type")
        .order_by("attack_type__name", "pk")
    )
    stats = _compute_scorecard_stats(team, score)
    detailed = calculate_team_score_detailed(team)

    context = {
        "team": team,
        "score": score,
        "red_scores": red_scores,
        "stats": stats,
        "red_total": sum(r.points_per_team for r in red_scores),
        "inject_total": sum(i["points"] for i in stats["inject_stats"]),
        "service_total": sum(s["points"] for s in stats["service_stats"]),
        "scaling": {
            "service_raw": detailed["service_raw"],
            "inject_raw": detailed["inject_raw"],
            "orange_raw": detailed["orange_raw"],
            "service_modifier": detailed["service_modifier"],
            "inject_modifier": detailed["inject_modifier"],
            "orange_modifier": detailed["orange_modifier"],
            "service_weight": detailed["service_weight"],
            "inject_weight": detailed["inject_weight"],
            "orange_weight": detailed["orange_weight"],
        },
    }
    html_string = render_to_string("scoring/scorecard_print.html", context, request=request)
    pdf_bytes: bytes = weasyprint.HTML(string=html_string).write_pdf()
    return pdf_bytes


def _stream_email_scorecards(request: HttpRequest) -> Iterator[str]:
    """Generator that sends scorecard emails and yields NDJSON progress."""
    import json
    import logging

    from team.models import SchoolInfo

    logger = logging.getLogger(__name__)

    scores = FinalScore.objects.filter(is_excluded=False, rank__isnull=False).select_related("team").order_by("rank")
    total_teams = scores.count()

    sendable = []
    for score in scores:
        team = score.team
        try:
            school_info = team.school_info
            emails = [school_info.contact_email]
            if school_info.secondary_email:
                emails.append(school_info.secondary_email)
            sendable.append((team, score, emails))
        except SchoolInfo.DoesNotExist:
            continue

    total = len(sendable)
    if total == 0:
        yield json.dumps({"done": True, "success": True, "message": "No teams with email addresses"}) + "\n"
        return

    sent = 0
    failed = 0
    for i, (team, score, emails) in enumerate(sendable, 1):
        email_ctx = _build_email_context(team, score, total_teams)
        pdf_bytes = _generate_team_pdf(team, score, request)
        success = _send_scorecard_email(emails, email_ctx, team.team_number, pdf_bytes)

        if success:
            sent += 1
            yield ndjson_progress(f"Sent to Team {team.team_number}", i, total)
        else:
            failed += 1
            logger.error("Failed to email scorecard to Team %d", team.team_number)
            yield ndjson_progress(f"Failed Team {team.team_number}", i, total, ok=False)

    message = f"Sent {sent}, failed {failed}" if failed else f"Emailed scorecards to {sent} teams"
    yield json.dumps({"done": True, "success": failed == 0, "message": message}) + "\n"


@require_permission("gold_team", error_message="Only Gold Team members can email scorecards")
def email_scorecards(request: HttpRequest) -> HttpResponse:
    """Email scorecards confirmation page (GET only)."""
    from django.contrib import messages
    from django.shortcuts import redirect, render

    from team.models import SchoolInfo

    scores = FinalScore.objects.filter(is_excluded=False, rank__isnull=False).select_related("team").order_by("rank")

    if not scores.exists():
        messages.error(request, "No scores available. Recalculate scores first.")
        return redirect("leaderboard_page")

    team_rows = []
    for score in scores:
        team = score.team
        try:
            school_info = team.school_info
            emails_list = [school_info.contact_email]
            if school_info.secondary_email:
                emails_list.append(school_info.secondary_email)
            team_rows.append(
                {
                    "team": team,
                    "score": score,
                    "school_name": school_info.school_name,
                    "emails": emails_list,
                    "has_email": True,
                }
            )
        except SchoolInfo.DoesNotExist:
            team_rows.append(
                {
                    "team": team,
                    "score": score,
                    "school_name": "",
                    "emails": [],
                    "has_email": False,
                }
            )

    teams_with_email = sum(1 for r in team_rows if r["has_email"])
    teams_without_email = sum(1 for r in team_rows if not r["has_email"])

    return render(
        request,
        "scoring/email_scorecards_confirm.html",
        {
            "team_rows": team_rows,
            "teams_with_email": teams_with_email,
            "teams_without_email": teams_without_email,
            "total_teams": scores.count(),
        },
    )


@require_POST
@require_permission("gold_team", error_message="Only Gold Team members can email scorecards")
def stream_email_scorecards(request: HttpRequest) -> StreamingHttpResponse:
    """Stream scorecard email sending progress as NDJSON."""
    return StreamingHttpResponse(
        _stream_email_scorecards(request),
        content_type="application/x-ndjson",
    )


@require_permission("gold_team", error_message="Only Gold Team members can email scorecards")
def email_scorecard(request: HttpRequest, team_number: int) -> HttpResponse:
    """Email scorecard to a single team. GET shows confirmation, POST sends."""
    from django.contrib import messages
    from django.shortcuts import get_object_or_404, redirect, render

    from team.models import SchoolInfo

    from ..models import FinalScore

    score = get_object_or_404(FinalScore, team__team_number=team_number)
    team = score.team
    total_teams = FinalScore.objects.filter(is_excluded=False, rank__isnull=False).count()

    try:
        school_info = team.school_info
        emails = [school_info.contact_email]
        if school_info.secondary_email:
            emails.append(school_info.secondary_email)
        school_name = school_info.school_name
    except SchoolInfo.DoesNotExist:
        messages.error(request, f"Team {team_number} has no school info / contact email.")
        return redirect("leaderboard_scorecard", team_number=team_number)

    if request.method == "POST":
        email_ctx = _build_email_context(team, score, total_teams)
        pdf_bytes = _generate_team_pdf(team, score, request)

        success = _send_scorecard_email(emails, email_ctx, team_number, pdf_bytes)

        if success:
            messages.success(request, f"Scorecard emailed to {', '.join(emails)}.")
        else:
            messages.error(request, f"Failed to email scorecard to Team {team_number}.")

        return redirect("leaderboard_scorecard", team_number=team_number)

    return render(
        request,
        "scoring/email_scorecard_confirm.html",
        {
            "team": team,
            "score": score,
            "school_name": school_name,
            "emails": emails,
        },
    )
