"""School-info CSV headers that aren't the canonical names still import every email."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from team.forms import parse_csv_file


def _parse(text: str):
    return parse_csv_file(SimpleUploadedFile("schools.csv", text.encode(), content_type="text/csv"))


def test_two_email_columns_keep_both_emails():
    result = _parse("School,Team Captain Email,Coach Email\nExample U,captain@example.edu,coach@example.edu\n")

    assert result["errors"] == []
    assert result["rows"] == [
        {
            "school_name": "Example U",
            "contact_email": "captain@example.edu",
            "secondary_email": "coach@example.edu",
            "notes": "",
        }
    ]


@pytest.mark.parametrize(
    ("header", "row"),
    [
        ("school_name,contact_email,Coach Email", "Example U,captain@example.edu,coach@example.edu"),
        ("school_name,Coach Email,contact_email", "Example U,coach@example.edu,captain@example.edu"),
    ],
)
def test_named_contact_column_wins_and_other_email_is_secondary(header, row):
    result = _parse(f"{header}\n{row}\n")

    assert result["errors"] == []
    assert result["rows"][0]["contact_email"] == "captain@example.edu"
    assert result["rows"][0]["secondary_email"] == "coach@example.edu"
