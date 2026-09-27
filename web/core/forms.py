"""Django forms for core views and admin views."""

from django import forms

from core.authentik_utils import parse_team_range


class SchoolInfoEditForm(forms.Form):
    school_name = forms.CharField(max_length=255)
    contact_email = forms.EmailField()
    secondary_email = forms.EmailField(required=False)
    notes = forms.CharField(required=False)


class BroadcastForm(forms.Form):
    target = forms.CharField()
    message = forms.CharField()


class CategoryForm(forms.Form):
    display_name = forms.CharField(max_length=100)
    points = forms.IntegerField(initial=0)
    required_fields = forms.TypedMultipleChoiceField(
        coerce=str,
        required=False,
        choices=[
            ("hostname", "Hostname"),
            ("ip_address", "IP Address"),
            ("service_name", "Service Name"),
            ("description", "Description"),
        ],
    )
    optional_fields = forms.TypedMultipleChoiceField(
        coerce=str,
        required=False,
        choices=[
            ("hostname", "Hostname"),
            ("ip_address", "IP Address"),
            ("service_name", "Service Name"),
            ("description", "Description"),
        ],
    )
    variable_points = forms.BooleanField(required=False)
    variable_cost_note = forms.CharField(required=False)
    min_points = forms.IntegerField(initial=0, required=False)
    max_points = forms.IntegerField(initial=0, required=False)
    user_creatable = forms.BooleanField(required=False)
    sort_order = forms.IntegerField(initial=0, required=False)


class SetMaxMembersForm(forms.Form):
    max_members = forms.IntegerField(min_value=1, max_value=20)


class AppSlugForm(forms.Form):
    app_slug = forms.CharField(max_length=100)

    def clean_app_slug(self) -> str:
        slug: str = self.cleaned_data["app_slug"].strip().lower()
        if not slug:
            raise forms.ValidationError("App slug is required")
        return slug


class SetTimeForm(forms.Form):
    datetime = forms.CharField()
    timezone = forms.CharField(initial="America/Los_Angeles")


class SetScheduleForm(forms.Form):
    start_datetime = forms.CharField(required=False)
    start_timezone = forms.CharField(required=False, initial="America/Los_Angeles")
    end_datetime = forms.CharField(required=False)
    end_timezone = forms.CharField(required=False, initial="America/Los_Angeles")

    def clean(self) -> dict[str, str]:
        cleaned = super().clean()
        if cleaned is None:
            return {}
        start = (cleaned.get("start_datetime") or "").strip()
        end = (cleaned.get("end_datetime") or "").strip()
        cleaned["start_datetime"] = start
        cleaned["end_datetime"] = end
        if not start and not end:
            raise forms.ValidationError("Please set at least one time")
        return cleaned


class ResetPasswordsForm(forms.Form):
    team_numbers = forms.CharField(required=False)

    def clean_team_numbers(self) -> list[int] | None:
        raw = self.cleaned_data.get("team_numbers", "").strip()
        if not raw:
            return None
        try:
            return parse_team_range(raw)
        except ValueError as e:
            raise forms.ValidationError(str(e)) from e


class ActionForm(forms.Form):
    """Simple action dispatcher form."""

    action = forms.CharField()


class ReadinessFixForm(forms.Form):
    """Form for readiness fix dispatch."""

    fix = forms.CharField()


class TeamActionForm(forms.Form):
    action = forms.CharField()
    discord_id = forms.IntegerField(required=False)


class TeamsBulkActionForm(forms.Form):
    action = forms.CharField()
    team_numbers = forms.CharField()

    def clean_team_numbers(self) -> list[int]:
        raw = self.cleaned_data["team_numbers"]
        try:
            return parse_team_range(raw)
        except ValueError as e:
            raise forms.ValidationError(str(e)) from e


class LinkConfirmForm(forms.Form):
    token = forms.CharField(max_length=64)
