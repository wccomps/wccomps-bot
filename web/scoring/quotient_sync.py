from decimal import Decimal
from typing import TypedDict

from django.contrib.auth.models import User
from quotient.client import QuotientClient

from team.models import Team

from .models import QuotientMetadataCache, ServiceDetail, ServiceScore


class ServiceData(TypedDict):
    """Service metadata from Quotient."""

    name: str
    display_name: str
    type: str


class BoxData(TypedDict):
    """Box metadata from Quotient."""

    name: str
    ip: str
    services: list[ServiceData]


def clear_quotient_metadata() -> None:
    QuotientMetadataCache.objects.all().delete()


def sync_quotient_metadata(user: User | None = None) -> QuotientMetadataCache:
    """Sync the box, service and IP metadata behind the scoring form dropdowns from Quotient.

    Raises ValueError if Quotient is unreachable, after clearing the cached metadata so it can't go stale.
    """
    client = QuotientClient()
    infrastructure = client.get_infrastructure()

    if not infrastructure:
        clear_quotient_metadata()
        raise ValueError("Failed to retrieve infrastructure from Quotient")

    boxes_data: list[BoxData] = []
    services_data: list[ServiceData] = []

    for box in infrastructure.boxes:
        box_services: list[ServiceData] = []

        for service in box.services:
            service_dict: ServiceData = {
                "name": service.name,
                "display_name": service.display_name,
                "type": service.type,
            }
            box_services.append(service_dict)

            if service_dict not in services_data:
                services_data.append(service_dict)

        box_dict: BoxData = {
            "name": box.name,
            "ip": box.ip,
            "services": box_services,
        }
        boxes_data.append(box_dict)

    # Update or create metadata cache (singleton)
    metadata = QuotientMetadataCache.objects.first()
    if metadata:
        metadata.boxes = boxes_data
        metadata.services = services_data
        metadata.event_name = infrastructure.event_name
        metadata.team_count = infrastructure.team_count
        metadata.synced_by = user
        metadata.save()
    else:
        metadata = QuotientMetadataCache.objects.create(
            boxes=boxes_data,
            services=services_data,
            event_name=infrastructure.event_name,
            team_count=infrastructure.team_count,
            synced_by=user,
        )

    return metadata


def sync_service_scores(user: User | None = None) -> dict[str, int]:
    """Sync every team's aggregate ServiceScore and per-service ServiceDetail records from Quotient."""
    client = QuotientClient()

    export_data = client.get_service_export()
    if not export_data:
        return {"teams_created": 0, "teams_updated": 0, "total": 0, "details_synced": 0}

    uptimes_data = client.get_uptimes()
    uptimes_by_team: dict[int, dict[str, float]] = {}
    if uptimes_data:
        for tu in uptimes_data:
            uptimes_by_team[tu.team_number] = tu.uptimes

    teams_updated = 0
    teams_created = 0
    details_synced = 0

    for team_export in export_data:
        try:
            team = Team.objects.get(team_number=team_export.team_number)
        except Team.DoesNotExist:
            continue

        _service_score, created = ServiceScore.objects.update_or_create(
            team=team,
            defaults={
                "service_points": Decimal(str(team_export.gross_points)),
                "sla_violations": Decimal(str(-team_export.total_sla_penalty)),
                "synced_by": user,
            },
        )

        if created:
            teams_created += 1
        else:
            teams_updated += 1

        team_uptimes = uptimes_by_team.get(team_export.team_number, {})
        ServiceDetail.objects.filter(team=team).delete()
        details = [
            ServiceDetail(
                team=team,
                service_name=svc.service_name,
                points=Decimal(str(svc.service_points)),
                uptime=Decimal(str(team_uptimes.get(svc.service_name, 0))),
            )
            for svc in team_export.services
        ]
        ServiceDetail.objects.bulk_create(details)
        details_synced += len(details)

    return {
        "teams_created": teams_created,
        "teams_updated": teams_updated,
        "total": teams_created + teams_updated,
        "details_synced": details_synced,
    }


def get_box_metadata() -> dict[str, dict[str, object]]:
    """Each box's IP and service names from cached metadata, for the forms' IP auto-fill and service filter."""
    metadata = QuotientMetadataCache.objects.first()
    if not metadata:
        return {}
    return {
        box["name"]: {"ip": box["ip"], "services": [svc["name"] for svc in box.get("services", [])]}
        for box in metadata.boxes
    }


def get_box_choices() -> list[tuple[str, str]]:
    """Box dropdown choices from cached metadata; labels lead with the last IP octet to tell boxes apart."""
    metadata = QuotientMetadataCache.objects.first()
    if metadata:
        choices = []
        for box in metadata.boxes:
            ip = box.get("ip", "")
            last_octet = ip.split(".")[-1] if ip else ""
            label = f".{last_octet} {box['name']}" if last_octet else box["name"]
            choices.append((box["name"], label))
        return choices
    return []


def get_service_choices(box_name: str | None = None) -> list[tuple[str, str]]:
    """Service dropdown choices from cached metadata, limited to box_name's services when given."""
    metadata = QuotientMetadataCache.objects.first()
    if not metadata:
        return []

    if box_name:
        for box in metadata.boxes:
            if box["name"] == box_name:
                return [(s["name"], s["display_name"] or s["name"]) for s in box["services"]]
        return []
    else:
        return [(s["name"], s["display_name"] or s["name"]) for s in metadata.services]


def get_cached_team_count() -> int:
    """Team count from the last Quotient sync, or 50 if never synced."""
    metadata = QuotientMetadataCache.objects.first()
    if metadata and metadata.team_count > 0:
        return metadata.team_count
    return 50
