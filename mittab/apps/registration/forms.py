from django import forms

from mittab.apps.registration.models import RegistrationConfig, RegistrationContent
from mittab.apps.tab.models import Team

NEW_CHOICE_VALUE = "__new__"
DEBATER_PREFIXES = ("debater_one", "debater_two")


def _build_debater_fields():
    fields = {}
    for prefix in DEBATER_PREFIXES:
        fields[f"{prefix}_id"] = forms.IntegerField(
            required=False, widget=forms.HiddenInput
        )
        fields[f"{prefix}_name"] = forms.CharField(max_length=30)
        fields[f"{prefix}_apda_id"] = forms.IntegerField(
            required=False, widget=forms.HiddenInput
        )
        fields[f"{prefix}_novice_status"] = forms.IntegerField(
            required=False, widget=forms.HiddenInput
        )
        fields[f"{prefix}_qualified"] = forms.BooleanField(
            required=False, widget=forms.HiddenInput
        )
        fields[f"{prefix}_school"] = forms.CharField()
        fields[f"{prefix}_school_name"] = forms.CharField(
            required=False, max_length=50
        )
    return fields


class ValueMixin:
    def _current_value(self, field_name):
        if self.is_bound:
            return self.data.get(self.add_prefix(field_name), "")
        return self.initial.get(field_name, "")

    @staticmethod
    def _reveal_input(widget):
        classes = widget.attrs.get("class", "")
        widget.attrs["class"] = " ".join(
            part for part in classes.split() if part != "d-none"
        ).strip()


def parse_school(value, name):
    if not value:
        raise forms.ValidationError("Select a school")
    if value.startswith("pk:"):
        return {"pk": int(value.split(":", 1)[1])}
    if value.startswith("apda:"):
        label = (name or "").strip()
        data = {"apda_id": int(value.split(":", 1)[1])}
        if label:
            data["name"] = label
        return data
    if value.startswith("custom:"):
        # Custom school created via the form - extract the name from the label
        label = (name or "").strip()
        custom_id = int(value.split(":", 1)[1])
        data = {"apda_id": custom_id}  # Use negative ID as apda_id
        if label:
            data["name"] = label
        return data
    if value == NEW_CHOICE_VALUE:
        label = (name or "").strip()
        if not label:
            raise forms.ValidationError("Enter a school name")
        return {"name": label}
    raise forms.ValidationError("Invalid school choice")


class RegistrationForm(ValueMixin, forms.Form):
    school = forms.CharField()
    school_name = forms.CharField(required=False, max_length=50)
    email = forms.EmailField(max_length=254)

    def __init__(self, *args, school_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = school_choices or []
        choice_values = [value for value, _ in choices]
        current = self._current_value("school")
        if current and current not in choice_values:
            label = self._current_value("school_name") or "Selected School"
            choices = [(current, label)] + choices
        final = [("", "Select school")] + choices
        self.fields["school"].widget = forms.Select(choices=final)
        self.choice_map = dict(choices)
        self.fields["school"].widget.attrs.update(
            {
                "class": "form-control",
                "data-school-select": "registration",
            }
        )
        self.fields["school_name"].widget.attrs.update(
            {
                "class": "form-control d-none",
                "placeholder": "School name",
            }
        )
        self.fields["school_name"].widget.attrs.setdefault(
            "data-related-select", self.fields["school"].widget.attrs.get("id")
        )
        if current == NEW_CHOICE_VALUE:
            self._reveal_input(self.fields["school_name"].widget)
        self.fields["email"].widget.attrs.update({"class": "form-control"})

    def get_school(self):
        value = self.cleaned_data["school"]
        label = self.cleaned_data.get("school_name") or self.choice_map.get(value)
        return parse_school(value, label)


class TeamForm(ValueMixin, forms.Form):
    team_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(max_length=30)
    seed_choice = forms.TypedChoiceField(
        choices=[
            (Team.UNSEEDED, "Unseeded"),
            (Team.FREE_SEED, "Free Seed"),
            (Team.HALF_SEED, "Half Seed"),
            (Team.FULL_SEED, "Full Seed"),
        ],
        coerce=int,
        initial=Team.UNSEEDED,
    )
    team_school_source = forms.ChoiceField(
        choices=[("debater_one", "Debater One"), ("debater_two", "Debater Two")],
        initial="debater_one",
    )
    hybrid_school_source = forms.ChoiceField(
        choices=[
            ("none", "No hybrid school"),
            ("debater_one", "Debater One"),
            ("debater_two", "Debater Two"),
        ],
        initial="none",
    )
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput)
    locals().update(_build_debater_fields())

    def __init__(self, *args, school_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        school_choices = school_choices or []
        self.choice_map = dict(school_choices)
        base_choices = [("", "Select school")] + school_choices
        self.fields["name"].widget.attrs.setdefault("class", "form-control")
        self.fields["seed_choice"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "data-team-seed": "true",
            }
        )
        self.fields["team_school_source"].widget.attrs.update(
            {"class": "form-control form-control-sm"}
        )
        self.fields["hybrid_school_source"].widget.attrs.update(
            {"class": "form-control form-control-sm"}
        )

        for prefix in DEBATER_PREFIXES:
            name_field = f"{prefix}_name"
            self.fields[name_field].widget = forms.Select(
                choices=[("", "Select a school first")]
            )
            self.fields[name_field].widget.attrs["class"] = "form-control"
            self.fields[f"{prefix}_qualified"].widget.attrs.setdefault("value", "")
            self.fields[f"{prefix}_novice_status"].initial = 0
            self._configure_school_field(
                f"{prefix}_school",
                self._current_value(f"{prefix}_school"),
                base_choices,
                self.fields[f"{prefix}_school_name"].widget,
            )
            self._configure_debater_inputs(prefix)

    def clean(self):
        data = super().clean()
        if data.get("DELETE"):
            return data
        if not data.get("debater_one_name") or not data.get("debater_two_name"):
            raise forms.ValidationError("Each team needs two debaters")
        primary = data.get("team_school_source") or "debater_one"
        if not data.get(f"{primary}_school"):
            raise forms.ValidationError("Select a school for the primary debater")
        hybrid = data.get("hybrid_school_source") or "none"
        if hybrid != "none" and not data.get(f"{hybrid}_school"):
            raise forms.ValidationError("Select a school for the hybrid designation")
        data["team_school_source"] = primary
        data["hybrid_school_source"] = hybrid
        return data

    def _member_payload(self, prefix):
        school_value = self.cleaned_data[f"{prefix}_school"]
        school_label = (
            self.cleaned_data.get(f"{prefix}_school_name")
            or self.choice_map.get(school_value)
        )
        return {
            "id": self.cleaned_data.get(f"{prefix}_id"),
            "name": self.cleaned_data[f"{prefix}_name"],
            "apda_id": self.cleaned_data.get(f"{prefix}_apda_id"),
            "novice_status": int(self.cleaned_data[f"{prefix}_novice_status"]),
            "qualified": bool(self.cleaned_data.get(f"{prefix}_qualified")),
            "school": parse_school(school_value, school_label),
        }

    def get_members(self):
        return [self._member_payload(prefix) for prefix in DEBATER_PREFIXES]

    def get_payload(self):
        return {
            "team_id": self.cleaned_data.get("team_id"),
            "name": self.cleaned_data["name"],
            "seed_choice": int(self.cleaned_data["seed_choice"]),
            "members": self.get_members(),
            "team_school_source": self.cleaned_data["team_school_source"],
            "hybrid_school_source": self.cleaned_data["hybrid_school_source"],
        }

    def _configure_school_field(self, field_name, current, base_choices, name_widget):
        field = self.fields[field_name]
        choices_list = list(base_choices)
        if current and current not in [value for value, _ in choices_list]:
            label = (
                self._current_value(field_name.replace("_school", "_school_name"))
                or self.choice_map.get(current)
                or "Selected School"
            )
            choices_list.insert(1, (current, label))
        # Don't add "Add New School" option - we have a dedicated form for that
        field.widget = forms.Select(choices=choices_list)
        field.widget.attrs.update(
            {
                "class": "form-control",
                "data-school-select": "team",
            }
        )
        name_id = self[field_name.replace("_school", "_name")].auto_id
        field.widget.attrs["data-name-id"] = name_id
        name_widget.attrs.update(
            {
                "class": "form-control mt-2 d-none",
                "placeholder": "School name",
            }
        )
        name_widget.attrs.setdefault(
            "data-related-select", field.widget.attrs.get("id")
        )
        if current == NEW_CHOICE_VALUE:
            self._reveal_input(name_widget)

    def _configure_debater_inputs(self, prefix):
        name_field = f"{prefix}_name"
        apda_field = f"{prefix}_apda_id"
        name_widget = self.fields[name_field].widget
        name_widget.attrs.update(
            {
                "data-debater-input": prefix,
                "data-apda-target": self[apda_field].auto_id,
            }
        )
        self.fields[apda_field].widget.attrs.setdefault("id", self[apda_field].auto_id)


class JudgeForm(forms.Form):
    registration_judge_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    judge_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(max_length=30)
    email = forms.EmailField(max_length=254)
    experience = forms.IntegerField(
        min_value=0,
        max_value=10,
        widget=forms.NumberInput(attrs={"min": "0", "max": "10", "step": "1"}),
    )
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.setdefault("class", "form-control")
        self.fields["email"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "Email",
            }
        )
        self.fields["experience"].widget.attrs.update(
            {"class": "form-control", "placeholder": "0-10"}
        )

    def get_payload(self):
        return {
            "registration_judge_id": self.cleaned_data.get("registration_judge_id"),
            "judge_id": self.cleaned_data.get("judge_id"),
            "name": self.cleaned_data["name"],
            "email": self.cleaned_data["email"],
            "experience": self.cleaned_data["experience"],
        }


class RegistrationSettingsForm(forms.Form):
    allow_new_registrations = forms.BooleanField(
        label="Allow New Registrations",
        required=False,
        help_text="Toggle whether schools can start a brand new registration.",
        widget=forms.CheckboxInput(attrs={"class": "custom-control-input"}),
    )
    allow_registration_edits = forms.BooleanField(
        label="Allow Registration Updates",
        required=False,
        help_text="Controls whether existing registration links can modify their data.",
        widget=forms.CheckboxInput(attrs={"class": "custom-control-input"}),
    )
    registration_description = forms.CharField(
        label="Homepage Registration Description",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Describe how teams should register "
                    "(links supported)."
                ),
            }
        ),
        help_text=(
            "Shown on the public homepage when new registrations are enabled."
        ),
    )
    registration_completion_message = forms.CharField(
        label="Post-Registration Instructions",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Provide follow-up instructions after submission "
                    "(links supported)."
                ),
            }
        ),
        help_text=(
            "Displayed inside the registration portal once a school submits "
            "their entry."
        ),
    )

    def __init__(self, *args, config=None, content=None, **kwargs):
        self.config = config or RegistrationConfig.get_or_create_active()
        self.content = content or RegistrationContent.get_solo()
        initial = {
            "allow_new_registrations": self.config.allow_new_registrations,
            "allow_registration_edits": self.config.allow_registration_edits,
            "registration_description": self.content.description,
            "registration_completion_message": self.content.completion_message,
        }
        kwargs.setdefault("initial", initial)
        super().__init__(*args, **kwargs)

    def save(self):
        self.config.allow_new_registrations = self.cleaned_data[
            "allow_new_registrations"
        ]
        self.config.allow_registration_edits = self.cleaned_data[
            "allow_registration_edits"
        ]
        self.config.save()
        self.content.description = self.cleaned_data["registration_description"]
        self.content.completion_message = self.cleaned_data[
            "registration_completion_message"
        ]
        self.content.save()
        return self.config, self.content
