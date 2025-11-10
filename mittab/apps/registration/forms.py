from django import forms

from mittab.apps.registration.models import RegistrationConfig, RegistrationContent
from mittab.apps.tab.models import Team

NEW_CHOICE_VALUE = "__new__"
NOVICE_CHOICES = ((0, "Varsity"), (1, "Novice"))


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


class RegistrationForm(forms.Form):
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

    def _current_value(self, field_name):
        if self.is_bound:
            return self.data.get(self.add_prefix(field_name), "")
        return self.initial.get(field_name, "")

    def _reveal_input(self, widget):
        classes = widget.attrs.get("class", "")
        widget.attrs["class"] = " ".join(
            part for part in classes.split() if part != "d-none"
        ).strip()

    def get_school(self):
        value = self.cleaned_data["school"]
        label = self.cleaned_data.get("school_name") or self.choice_map.get(value)
        return parse_school(value, label)


class TeamForm(forms.Form):
    registration_team_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    team_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(max_length=30)
    is_free_seed = forms.BooleanField(required=False)
    seed_choice = forms.TypedChoiceField(
        choices=[
            (Team.UNSEEDED, "Unseeded"),
            (Team.HALF_SEED, "Half Seed"),
            (Team.FULL_SEED, "Full Seed"),
        ],
        coerce=int,
        initial=Team.UNSEEDED,
    )
    debater_one_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    debater_one_name = forms.CharField(max_length=30)
    debater_one_apda_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    debater_one_novice_status = forms.IntegerField(
        required=False, widget=forms.HiddenInput
    )
    debater_one_qualified = forms.BooleanField(required=False, widget=forms.HiddenInput)
    debater_one_school = forms.CharField()
    debater_one_school_name = forms.CharField(required=False, max_length=50)
    debater_two_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    debater_two_name = forms.CharField(max_length=30)
    debater_two_apda_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    debater_two_novice_status = forms.IntegerField(
        required=False, widget=forms.HiddenInput
    )
    debater_two_qualified = forms.BooleanField(required=False, widget=forms.HiddenInput)
    debater_two_school = forms.CharField()
    debater_two_school_name = forms.CharField(required=False, max_length=50)
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, school_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        school_choices = school_choices or []
        self.choice_map = dict(school_choices)
        base_choices = [("", "Select school")] + school_choices
        control_fields = [
            "name",
        ]
        for field in control_fields:
            self.fields[field].widget.attrs.setdefault("class", "form-control")
        self.fields["seed_choice"].widget.attrs.update(
            {
                "class": "form-control form-control-sm",
                "data-team-seed": "true",
            }
        )

        # Configure debater name fields as Select widgets
        self.fields["debater_one_name"].widget = forms.Select(
            choices=[("", "Select a school first")]
        )
        self.fields["debater_one_name"].widget.attrs.update(
            {
                "class": "form-control",
            }
        )
        self.fields["debater_two_name"].widget = forms.Select(
            choices=[("", "Select a school first")]
        )
        self.fields["debater_two_name"].widget.attrs.update(
            {
                "class": "form-control",
            }
        )

        self.fields["is_free_seed"].widget.attrs.setdefault("class", "form-check-input")
        self.fields["debater_one_qualified"].widget.attrs.setdefault("value", "")
        self.fields["debater_two_qualified"].widget.attrs.setdefault("value", "")
        self.fields["debater_one_novice_status"].initial = 0
        self.fields["debater_two_novice_status"].initial = 0

        first_value = self._current_value("debater_one_school")
        second_value = self._current_value("debater_two_school")

        self._configure_school_field(
            "debater_one_school",
            first_value,
            base_choices,
            self.fields["debater_one_school_name"].widget,
        )
        self._configure_school_field(
            "debater_two_school",
            second_value,
            base_choices,
            self.fields["debater_two_school_name"].widget,
        )

        self._configure_debater_inputs("debater_one")
        self._configure_debater_inputs("debater_two")

    def clean(self):
        data = super().clean()
        if data.get("DELETE"):
            return data
        if not data.get("debater_one_name") or not data.get("debater_two_name"):
            raise forms.ValidationError("Each team needs two debaters")
        return data

    def get_members(self):
        return [
            {
                "id": self.cleaned_data.get("debater_one_id"),
                "name": self.cleaned_data["debater_one_name"],
                "apda_id": self.cleaned_data.get("debater_one_apda_id"),
                "novice_status": int(self.cleaned_data["debater_one_novice_status"]),
                "qualified": bool(self.cleaned_data.get("debater_one_qualified")),
                "school": parse_school(
                    self.cleaned_data["debater_one_school"],
                    self.cleaned_data.get("debater_one_school_name")
                    or self.choice_map.get(self.cleaned_data["debater_one_school"]),
                ),
            },
            {
                "id": self.cleaned_data.get("debater_two_id"),
                "name": self.cleaned_data["debater_two_name"],
                "apda_id": self.cleaned_data.get("debater_two_apda_id"),
                "novice_status": int(self.cleaned_data["debater_two_novice_status"]),
                "qualified": bool(self.cleaned_data.get("debater_two_qualified")),
                "school": parse_school(
                    self.cleaned_data["debater_two_school"],
                    self.cleaned_data.get("debater_two_school_name")
                    or self.choice_map.get(self.cleaned_data["debater_two_school"]),
                ),
            },
        ]

    def get_payload(self):
        return {
            "registration_team_id": self.cleaned_data.get("registration_team_id"),
            "team_id": self.cleaned_data.get("team_id"),
            "name": self.cleaned_data["name"],
            "is_free_seed": bool(self.cleaned_data.get("is_free_seed")),
            "seed_choice": int(self.cleaned_data["seed_choice"]),
            "members": self.get_members(),
        }

    def _current_value(self, field_name):
        if self.is_bound:
            return self.data.get(self.add_prefix(field_name), "")
        return self.initial.get(field_name, "")

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
        list_id = f"{self.prefix}-{field_name.replace('_school', '')}-options"
        field.widget.attrs["data-list-id"] = list_id
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
        list_id = f"{self.prefix}-{prefix.replace('_', '-')}-options"
        name_widget = self.fields[name_field].widget
        # For select widgets we do not need autocomplete or list attributes for
        # functionality, but we set them so the template can access the list ID.
        name_widget.attrs.update(
            {
                "data-debater-input": prefix,
                "data-apda-target": self[apda_field].auto_id,
                "data-list": list_id,
                "list": list_id,  # Also set without data- prefix for template access
            }
        )
        self.fields[apda_field].widget.attrs.setdefault("id", self[apda_field].auto_id)

    def _reveal_input(self, widget):
        classes = widget.attrs.get("class", "")
        widget.attrs["class"] = " ".join(
            part for part in classes.split() if part != "d-none"
        ).strip()


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
