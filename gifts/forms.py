import calendar
import re

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import validate_email
from django.utils.translation import gettext as _

from .models import Group, User

PROFILE_INPUT_CLASS = "form-control form-control-lg rounded-4 border-1"
PROFILE_SELECT_CLASS = "form-select form-select-lg rounded-4 border-1"


class LocalUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, label="Email")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            # Accepted trade-off (plan.md lot 6, task A2): this explicit message lets
            # someone probe whether an address has an account, which §5.6 discourages.
            # For a gift-list app the clearer wording wins; the per-IP rate limit on
            # /register (gifts.auth_throttle) blunts automated enumeration.
            raise forms.ValidationError(_("A user with that email already exists."), code="unique")
        return email


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name",)
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control rounded-pill border-0 bg-body-tertiary p-3",
                    "placeholder": "ex: Famille Smith",
                }
            ),
        }


class OnboardingJoinGroupForm(forms.Form):
    code = forms.CharField(
        max_length=12,
        label=_("Group code"),
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg rounded-4 text-uppercase",
                "autocomplete": "off",
                "placeholder": _("Enter the group code"),
            }
        ),
    )

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class GroupInvitationEmailForm(forms.Form):
    emails = forms.CharField(
        label=_("Email addresses"),
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control rounded-4",
                "rows": 4,
                "placeholder": _("alex@example.com, sam@example.com"),
                "autocomplete": "email",
            }
        ),
        help_text=_("Separate addresses with commas, spaces, semicolons or line breaks."),
    )

    def clean_emails(self):
        values = [value.strip().lower() for value in re.split(r"[,;\s]+", self.cleaned_data["emails"]) if value]
        recipients = list(dict.fromkeys(values))
        if len(recipients) > settings.GROUP_INVITATION_MAX_RECIPIENTS_PER_REQUEST:
            raise forms.ValidationError(
                _("You can invite up to %(count)s people at a time."),
                params={"count": settings.GROUP_INVITATION_MAX_RECIPIENTS_PER_REQUEST},
            )
        for recipient in recipients:
            try:
                validate_email(recipient)
            except forms.ValidationError as error:
                raise forms.ValidationError(_("Enter valid email addresses.")) from error
        return recipients


class BirthdayValidationMixin:
    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data
        month = cleaned_data.get("birthday_month")
        day = cleaned_data.get("birthday_day")
        if bool(month) != bool(day):
            raise forms.ValidationError(_("Please provide both a birthday month and day."))
        if month and day and day > calendar.monthrange(2000, month)[1]:
            self.add_error("birthday_day", _("Please enter a valid birthday."))
        return cleaned_data


class OnboardingProfileForm(BirthdayValidationMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["nickname", "birthday_month", "birthday_day"]
        labels = {
            "nickname": _("Nickname"),
            "birthday_month": _("Birthday month"),
            "birthday_day": _("Birthday day"),
        }
        widgets = {
            "nickname": forms.TextInput(
                attrs={
                    "class": PROFILE_INPUT_CLASS,
                    "autocomplete": "nickname",
                }
            ),
            "birthday_month": forms.Select(
                choices=[("", _("Month"))] + [(month, calendar.month_name[month]) for month in range(1, 13)],
                attrs={"class": PROFILE_SELECT_CLASS},
            ),
            "birthday_day": forms.Select(
                choices=[("", _("Day"))] + [(day, day) for day in range(1, 32)],
                attrs={"class": PROFILE_SELECT_CLASS},
            ),
        }

    def clean_nickname(self):
        nickname = self.cleaned_data["nickname"].strip()
        if not nickname:
            raise forms.ValidationError(_("This field is required."), code="required")
        return nickname


class UserProfileForm(BirthdayValidationMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["nickname", "email", "birthday_month", "birthday_day", "avatar"]
        labels = {
            "nickname": _("Nickname"),
            "email": _("Email"),
            "birthday_month": _("Birthday month"),
            "birthday_day": _("Birthday day"),
            "avatar": _("Profile picture"),
        }
        widgets = {
            "nickname": forms.TextInput(attrs={"class": PROFILE_INPUT_CLASS}),
            "email": forms.EmailInput(attrs={"class": PROFILE_INPUT_CLASS}),
            "birthday_month": forms.Select(
                choices=[("", _("Month"))] + [(month, calendar.month_name[month]) for month in range(1, 13)],
                attrs={"class": PROFILE_SELECT_CLASS},
            ),
            "birthday_day": forms.Select(
                choices=[("", _("Day"))] + [(day, day) for day in range(1, 32)],
                attrs={"class": PROFILE_SELECT_CLASS},
            ),
        }
