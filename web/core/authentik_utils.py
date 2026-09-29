"""Utility functions for Authentik user management."""

import logging
import secrets

from team.models import MAX_TEAMS

from .authentik_manager import AuthentikUser

logger = logging.getLogger(__name__)


def validate_team_account(user_data: AuthentikUser, expected_username: str) -> tuple[bool, str]:
    """Validate that a user account is a legitimate team account."""
    retrieved_username = user_data.get("username", "")

    # Check username starts with "team"
    if not retrieved_username.startswith("team"):
        return (
            False,
            f"Security error: User {retrieved_username} is not a team account",
        )

    # Check it matches expected username
    if retrieved_username != expected_username:
        return (
            False,
            f"Security error: Username mismatch (expected {expected_username}, got {retrieved_username})",
        )

    return (True, "")


def reset_team_password(team_number: int) -> tuple[str | None, str]:
    """Give a team account a new random password and record it for credential and packet emails.

    The password is stored on the team's assignments for events not yet finalized, which is where
    those emails read it. Returns (password, "") on success, (None, error) otherwise.
    """
    from registration.models import EventTeamAssignment

    from .authentik_manager import AuthentikManager

    password = generate_blueteam_password()
    success, error = AuthentikManager().reset_blueteam_password(team_number, password)
    if not success:
        return None, error
    EventTeamAssignment.objects.filter(team__team_number=team_number, event__is_finalized=False).update(
        password_generated=password
    )
    return password, ""


def generate_blueteam_password() -> str:
    """Generate a readable password for blue team accounts using EFF wordlist.

    Returns:
        str: Password in format like "Correct-Horse-742!" or "Battery-@199-Staple"
    """
    from xkcdpass import xkcd_password as xp

    # Get EFF long wordlist (7,776 words)
    wordlist = xp.generate_wordlist(wordfile=xp.locate_wordfile())

    # Generate 2 random words
    words = xp.generate_xkcdpassword(wordlist, numwords=2, delimiter="-", case="capitalize")

    # Generate random number (100-999)
    number = secrets.randbelow(900) + 100

    # Select random special character
    special_chars = "!@#$%&*+"
    special_char = secrets.choice(special_chars)

    # Combine number and symbol (randomly choose order)
    insert_value = f"{number}{special_char}" if secrets.choice([True, False]) else f"{special_char}{number}"

    # Randomly choose position (0=before, 1=middle, 2=after)
    position = secrets.randbelow(3)

    # Insert number+symbol at chosen position
    word_parts = words.split("-")
    if position == 0:
        result = f"{insert_value}-{words}"
    elif position == 1:
        result = f"{word_parts[0]}-{insert_value}-{word_parts[1]}"
    else:  # position == 2
        result = f"{words}-{insert_value}"

    return result


def parse_team_range(range_str: str) -> list[int]:
    """
    Parse team range string like "1,3,5-10,15" into list of team numbers.

    Args:
        range_str: String with comma-separated numbers and ranges (e.g., "1,3,5-10,15")

    Returns:
        List of unique team numbers, sorted

    Examples:
        "1,3,5" -> [1, 3, 5]
        "1-5" -> [1, 2, 3, 4, 5]
        "1,3,5-10,15" -> [1, 3, 5, 6, 7, 8, 9, 10, 15]
    """
    team_numbers: set[int] = set()

    for raw_part in range_str.split(","):
        part = raw_part.strip()
        if not part:
            continue

        if "-" in part:
            # Range like "5-10"
            try:
                start_str, end_str = part.split("-", 1)
                start_num = int(start_str.strip())
                end_num = int(end_str.strip())
            except ValueError as e:
                raise ValueError(f"Invalid range format: {part}") from e

            if start_num > end_num:
                raise ValueError(f"Invalid range: {part} (start > end)")
            if start_num < 1 or end_num > MAX_TEAMS:
                raise ValueError(f"Team numbers must be 1-{MAX_TEAMS}, got: {part}")

            team_numbers.update(range(start_num, end_num + 1))
        else:
            # Single number
            try:
                num = int(part)
            except ValueError as e:
                raise ValueError(f"Invalid team number: {part}") from e

            if num < 1 or num > MAX_TEAMS:
                raise ValueError(f"Team number must be 1-{MAX_TEAMS}, got: {num}")
            team_numbers.add(num)

    return sorted(team_numbers)
