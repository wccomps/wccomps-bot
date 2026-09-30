import logging
from typing import cast

from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.http import content_disposition_header

from core.auth_utils import get_user_team, has_permission
from ticketing.models import Ticket, TicketAttachment

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10MB

# MIME types safe for inline viewing (no XSS risk)
INLINE_SAFE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
}


def _save_attachment(
    ticket: Ticket, uploaded_file: UploadedFile[bytes] | None, uploaded_by: str
) -> HttpResponse | None:
    """Validate and save an attachment. Returns HttpResponse on error, None on success."""
    if not uploaded_file:
        return HttpResponse("No file provided", status=400)

    if uploaded_file.size is None or uploaded_file.size > MAX_ATTACHMENT_SIZE:
        uploaded_file.close()
        return HttpResponse("File too large (max 10MB)", status=400)

    if not uploaded_file.name:
        uploaded_file.close()
        return HttpResponse("File must have a name", status=400)

    try:
        TicketAttachment.objects.create(
            ticket=ticket,
            file_data=uploaded_file.read(),
            filename=uploaded_file.name[:255],
            mime_type=uploaded_file.content_type or "application/octet-stream",
            uploaded_by=uploaded_by,
        )
    finally:
        uploaded_file.close()
    return None


def ticket_attachment_upload(request: HttpRequest, ticket_number: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    user = cast(User, request.user)
    authentik_username = user.username
    team = get_user_team(user)
    is_ops = has_permission(user, "ticketing_support") or has_permission(user, "ticketing_admin")

    if not team and not is_ops:
        return HttpResponse("Access denied", status=403)

    try:
        ticket = Ticket.objects.select_related("team").get(ticket_number=ticket_number)
    except Ticket.DoesNotExist:
        return HttpResponse("Ticket not found", status=404)

    if not is_ops and ticket.team != team:
        return HttpResponse("Access denied", status=403)

    if error := _save_attachment(ticket, request.FILES.get("attachment"), authentik_username):
        return error

    logger.info(f"Attachment uploaded to ticket {ticket.ticket_number} by {authentik_username}")

    return redirect("ticket_detail", ticket_number=ticket.ticket_number)


def ticket_attachment_download(
    request: HttpRequest,
    attachment_id: int,
    ticket_number: str,
) -> HttpResponse:
    user = cast(User, request.user)
    team = get_user_team(user)
    is_ops = has_permission(user, "ticketing_support") or has_permission(user, "ticketing_admin")

    if not team and not is_ops:
        return HttpResponse("Access denied", status=403)

    try:
        attachment = TicketAttachment.objects.select_related("ticket", "ticket__team").get(
            id=attachment_id, ticket__ticket_number=ticket_number
        )
    except TicketAttachment.DoesNotExist:
        return HttpResponse("Attachment not found", status=404)

    if not is_ops and attachment.ticket.team != team:
        return HttpResponse("Access denied", status=403)

    # Only images and PDFs render inline; anything else downloads, to prevent XSS via HTML/SVG
    inline_requested = request.GET.get("inline") == "1"
    is_safe_for_inline = attachment.mime_type in INLINE_SAFE_MIME_TYPES
    as_attachment = not (inline_requested and is_safe_for_inline)

    response = HttpResponse(bytes(attachment.file_data), content_type=attachment.mime_type)
    response["Content-Disposition"] = str(
        content_disposition_header(as_attachment=as_attachment, filename=attachment.filename)
    )
    return response
