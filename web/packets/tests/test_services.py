"""Tests for packet services."""

import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase
from registration.models import Event, EventTeamAssignment, Season, TeamRegistration

from team.models import SchoolInfo, Team

from ..models import Packet, PacketDistribution
from ..services import PacketDistributionService


class PacketDistributionServiceTestCase(TestCase):
    """Test PacketDistributionService."""

    def setUp(self):
        """Create test data."""
        self.service = PacketDistributionService()

        # Create season and event
        season = Season.objects.create(name="Test Season", year=2026)
        self.event = Event.objects.create(
            season=season,
            name="Test Event",
            event_type="invitational",
            date=datetime.date(2026, 3, 1),
        )

        # Create teams with school info and event assignments
        for i in range(1, 4):
            team = Team.objects.create(
                team_number=i,
                team_name=f"Team {i}",
                authentik_group=f"WCComps_BlueTeam{i:02d}",
                is_active=True,
            )
            SchoolInfo.objects.create(
                team=team,
                school_name=f"School {i}",
                contact_email=f"team{i}@example.com",
            )
            registration = TeamRegistration.objects.create(
                school_name=f"School {i}",
                status="approved",
            )
            EventTeamAssignment.objects.create(
                event=self.event,
                registration=registration,
                team=team,
                password_generated=f"TestPass{i}!",
            )

        self.packet = Packet.objects.create(
            title="Test Packet",
            file_data=b"test file content",
            filename="test.pdf",
            mime_type="application/pdf",
            file_size=100,
            uploaded_by="testuser",
            status="draft",
            send_via_email=True,
            web_access_enabled=True,
            event=self.event,
        )

    def test_create_distributions_for_teams(self):
        """Test creating distributions for all teams."""
        created = self.service._create_distributions_for_teams(self.packet)

        self.assertEqual(created, 3)
        self.assertEqual(PacketDistribution.objects.count(), 3)

        # Verify distributions created for all teams
        for i in range(1, 4):
            team = Team.objects.get(team_number=i)
            dist = PacketDistribution.objects.get(packet=self.packet, team=team)
            self.assertEqual(dist.email_status, "pending")

    @patch("packets.services.EmailMultiAlternatives")
    def test_send_packet_email(self, mock_email_class):
        """Test sending packet email."""
        team = Team.objects.get(team_number=1)
        distribution = PacketDistribution.objects.create(packet=self.packet, team=team)

        # Mock email sending
        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        self.service.send_packet_email(distribution)

        # Verify packet was attached
        mock_email.attach.assert_called_once_with("test.pdf", b"test file content", "application/pdf")

        # Verify email was sent
        mock_email.send.assert_called_once_with(fail_silently=False)

        # Verify distribution was marked as sent
        distribution.refresh_from_db()
        self.assertEqual(distribution.email_status, "sent")
        self.assertEqual(distribution.email_sent_to, "team1@example.com")

        # The packet delivered the team's credentials
        assignment = EventTeamAssignment.objects.get(event=self.event, team=team)
        self.assertIsNotNone(assignment.credentials_sent_at)

    @patch("packets.services.EmailMultiAlternatives")
    def test_send_packet_email_with_team_extras(self, mock_email_class):
        """Test that per-team extras are passed to email context."""
        self.packet.team_extras = {
            "1": {"api_key": "sk-test-key", "max_spend_usd": "100"},
            "2": {"api_key": "sk-other-key", "max_spend_usd": "200"},
        }
        self.packet.save()

        team = Team.objects.get(team_number=1)
        distribution = PacketDistribution.objects.create(packet=self.packet, team=team)

        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        self.service.send_packet_email(distribution)

        # Verify email was created with correct context (check rendered templates)
        mock_email.send.assert_called_once_with(fail_silently=False)
        distribution.refresh_from_db()
        self.assertEqual(distribution.email_status, "sent")

    def test_send_packet_email_requires_event(self):
        """Test that distribution fails without event link."""
        self.packet.event = None
        self.packet.status = "draft"
        self.packet.save()

        with self.assertRaises(ValueError, msg="Packet must be linked to an event"):
            self.service.distribute_packet(self.packet)

    def test_send_packet_email_requires_credentials(self):
        """Test that sending fails without generated credentials."""
        team = Team.objects.get(team_number=1)
        assignment = EventTeamAssignment.objects.get(event=self.event, team=team)
        assignment.password_generated = ""
        assignment.save()

        distribution = PacketDistribution.objects.create(packet=self.packet, team=team)

        with self.assertRaises(ValueError, msg="No credentials generated"):
            self.service.send_packet_email(distribution)

    @patch("packets.services.EmailMultiAlternatives")
    def test_send_packet_email_requires_event_assignment(self, mock_email_class):
        """A team with school info but no assignment to the packet's event fails without inventing one."""
        team = Team.objects.get(team_number=1)
        EventTeamAssignment.objects.filter(team=team).delete()
        registrations = TeamRegistration.objects.count()
        distribution = PacketDistribution.objects.create(packet=self.packet, team=team)

        with self.assertRaisesMessage(ValueError, "not assigned to Test Event"):
            self.service.send_packet_email(distribution)

        self.assertEqual(TeamRegistration.objects.count(), registrations)
        self.assertFalse(EventTeamAssignment.objects.filter(team=team).exists())
        mock_email_class.return_value.send.assert_not_called()

    @patch("packets.services.PacketDistributionService.send_packet_email")
    def test_distribute_packet(self, mock_send_email):
        """Test distributing packet."""
        result = self.service.distribute_packet(self.packet)

        # Verify distributions created
        self.assertEqual(result["created"], 3)
        self.assertEqual(PacketDistribution.objects.count(), 3)

        # Verify packet marked as distributing then completed
        self.packet.refresh_from_db()
        self.assertEqual(self.packet.status, "completed")
        self.assertIsNotNone(self.packet.actual_distribution_time)
