"""Forms for team registration."""

from django import forms

from .models import Event, RegistrationContact, Season, TeamRegistration


class SchoolInfoForm(forms.ModelForm[TeamRegistration]):
    """Form for school information (step 1)."""

    class Meta:
        model = TeamRegistration
        fields = ["school_name"]
        labels = {
            "school_name": "School Name",
        }


class ContactForm(forms.ModelForm[RegistrationContact]):
    """Base form for contact information."""

    class Meta:
        model = RegistrationContact
        fields = ["name", "email", "phone"]
        labels = {
            "name": "Full Name",
            "email": "Email Address",
            "phone": "Phone Number",
        }


class CaptainContactForm(ContactForm):
    """Form for team captain contact (required)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.fields["name"].required = True
        self.fields["email"].required = True
        self.fields["phone"].required = True


class CoachContactForm(ContactForm):
    """Form for coach/faculty advisor contact (required)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.fields["name"].required = True
        self.fields["email"].required = True
        self.fields["phone"].required = False


class OptionalContactForm(ContactForm):
    """Form for optional contacts (co-captain, site judge)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        for field in self.fields.values():
            field.required = False

    def has_data(self) -> bool:
        """Check if any data was provided."""
        if not self.is_bound:
            return False
        return bool(self.data.get(f"{self.prefix}-name") or self.data.get(f"{self.prefix}-email"))


class EventSelectionForm(forms.Form):
    """Form for selecting events to register for."""

    events = forms.ModelMultipleChoiceField(
        queryset=Event.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Select Events",
        help_text="Choose which events your team will participate in.",
    )

    def __init__(self, *args: object, season: Season | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        events_field = self.fields["events"]
        if isinstance(events_field, forms.ModelMultipleChoiceField):
            if season:
                events_field.queryset = Event.objects.filter(
                    season=season,
                    registration_open=True,
                ).order_by("date")
            else:
                events_field.queryset = Event.objects.filter(
                    season__is_active=True,
                    registration_open=True,
                ).order_by("date")


class RegistrationForm(forms.ModelForm[TeamRegistration]):
    """Combined form for registration with captain, coach, events, and rules."""

    # Team Captain fields
    captain_name = forms.CharField(max_length=255, label="Full Name")
    captain_email = forms.EmailField(label="Email Address")
    captain_phone = forms.CharField(max_length=50, label="Phone Number")

    # Coach/Faculty Advisor fields
    coach_name = forms.CharField(max_length=255, label="Full Name")
    coach_email = forms.EmailField(label="Email Address")
    coach_phone = forms.CharField(max_length=50, label="Phone Number", required=False)

    # Event selection
    events = forms.ModelMultipleChoiceField(
        queryset=Event.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Select Events",
    )

    # Rules agreement
    agree_to_rules = forms.BooleanField(
        required=True,
        label="I agree to the competition rules",
    )

    class Meta:
        model = TeamRegistration
        fields = ["school_name", "region"]
        labels = {
            "school_name": "School Name",
            "region": "CCDC Region",
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        # Populate events from active season with open registration
        events_field = self.fields["events"]
        if isinstance(events_field, forms.ModelMultipleChoiceField):
            events_field.queryset = Event.objects.filter(
                season__is_active=True,
                registration_open=True,
            ).order_by("date")

    def clean(self) -> dict[str, object]:
        """Validate that non-WRCCDC teams only register for invitationals."""
        cleaned_data = super().clean() or {}
        region = cleaned_data.get("region")
        events = cleaned_data.get("events")

        if region and region != "wrccdc" and events:
            restricted_events = [e for e in events if e.event_type in ("qualifier", "regional")]
            if restricted_events:
                event_names = ", ".join(e.name for e in restricted_events)
                raise forms.ValidationError(
                    f"Only Western Regional (WRCCDC) teams can register for Qualifiers and Regionals. "
                    f"Please remove: {event_names}"
                )

        return cleaned_data

    def save(self, commit: bool = True) -> TeamRegistration:
        """Save registration, contacts, and event enrollments."""
        from .models import RegistrationEventEnrollment

        registration = super().save(commit=commit)
        if commit:
            # Create captain contact
            RegistrationContact.objects.update_or_create(
                registration=registration,
                role="captain",
                defaults={
                    "name": self.cleaned_data["captain_name"],
                    "email": self.cleaned_data["captain_email"],
                    "phone": self.cleaned_data["captain_phone"],
                },
            )
            # Create coach contact
            RegistrationContact.objects.update_or_create(
                registration=registration,
                role="coach",
                defaults={
                    "name": self.cleaned_data["coach_name"],
                    "email": self.cleaned_data["coach_email"],
                    "phone": self.cleaned_data.get("coach_phone", ""),
                },
            )
            # Create event enrollments
            for event in self.cleaned_data["events"]:
                RegistrationEventEnrollment.objects.get_or_create(
                    registration=registration,
                    event=event,
                )
        return registration


class SeasonForm(forms.ModelForm[Season]):
    """Form for creating/editing seasons."""

    class Meta:
        model = Season
        fields = ["name", "year", "is_active"]
        labels = {
            "name": "Season Name",
            "year": "Year",
            "is_active": "Active Season",
        }
        help_texts = {
            "is_active": "Only one season should be active at a time.",
        }


class EventForm(forms.ModelForm[Event]):
    """Form for creating/editing events."""

    class Meta:
        model = Event
        fields = [
            "name",
            "event_type",
            "event_number",
            "date",
            "start_time",
            "end_time",
            "registration_open",
            "registration_deadline",
            "max_teams",
        ]
        labels = {
            "name": "Event Name",
            "event_type": "Event Type",
            "event_number": "Event Number",
            "date": "Event Date",
            "start_time": "Start Time",
            "end_time": "End Time",
            "registration_open": "Registration Open",
            "registration_deadline": "Registration Deadline",
            "max_teams": "Maximum Teams",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "registration_deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }


class RejectRegistrationForm(forms.Form):
    reason = forms.CharField()
