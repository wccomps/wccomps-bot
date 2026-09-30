from team.models import team_username


def test_team_username_is_zero_padded() -> None:
    assert team_username(1) == "team01"
    assert team_username(50) == "team50"
