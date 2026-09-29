"""Discord webhook logging handler for error alerts."""

import logging
import os
import traceback
from datetime import UTC, datetime

import httpx
from django.conf import settings


class DiscordWebhookHandler(logging.Handler):
    """Logging handler that sends error messages to the DISCORD_ERROR_WEBHOOK_URL Discord webhook."""

    def __init__(self) -> None:
        super().__init__()
        webhook_url = os.environ.get("DISCORD_ERROR_WEBHOOK_URL", "")
        self.webhook_url = webhook_url if webhook_url.startswith("https://") else ""
        self.hostname = os.environ.get("HOSTNAME", "unknown")

    def emit(self, record: logging.LogRecord) -> None:
        if not self.webhook_url:
            return

        try:
            timestamp = datetime.now(UTC).isoformat()

            exc_info = ""
            if record.exc_info:
                exc_info = "".join(traceback.format_exception(*record.exc_info))

            embed: dict[str, object] = {
                "title": f"{record.levelname}: {record.getMessage()[:200]}",
                "color": 0xFF0000 if record.levelname == "ERROR" else 0xFFA500,
                "timestamp": timestamp,
                "fields": [
                    {"name": "Logger", "value": record.name, "inline": True},
                    {"name": "Host", "value": self.hostname, "inline": True},
                ],
            }

            fields = embed["fields"]
            if not isinstance(fields, list):
                return

            if exc_info:
                # Truncate to fit Discord's 1024 char field limit
                exc_preview = exc_info[-1000:] if len(exc_info) > 1000 else exc_info
                fields.append(
                    {
                        "name": "Traceback",
                        "value": f"```\n{exc_preview}\n```",
                        "inline": False,
                    }
                )

            # Add request path if available (from django.request logger)
            if hasattr(record, "request"):
                request = record.request
                fields.insert(
                    0,
                    {
                        "name": "Request",
                        "value": f"{request.method} {request.path}",
                        "inline": True,
                    },
                )
                if hasattr(request, "user") and request.user.is_authenticated:
                    fields.insert(
                        1,
                        {
                            "name": "User",
                            "value": request.user.username,
                            "inline": True,
                        },
                    )

            payload = {"embeds": [embed]}

            # Send to Discord (sync, but logging handlers should be fast)
            httpx.post(self.webhook_url, json=payload, timeout=settings.DISCORD_WEBHOOK_TIMEOUT)

        except Exception:
            # Don't let logging errors break the app
            self.handleError(record)
