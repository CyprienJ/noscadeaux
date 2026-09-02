from datetime import timedelta
from smtplib import SMTPException

from anymail.exceptions import AnymailError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import send_mail
from django.core.management import call_command
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from gifts.auth_throttle import AuthRateLimitError, enforce_auth_rate_limit
from gifts.demo import DEMO_EMAIL, is_demo_user
from gifts.forms import LocalUserCreationForm, OnboardingProfileForm, UserProfileForm
from gifts.models import AuthThrottleEvent, User
from gifts.onboarding import (
    get_onboarding_next_url,
    get_pending_group_invite,
    remember_pending_group_invite,
)
from gifts.onboarding_metrics import record_onboarding_event
from gifts.photo_presets import is_valid_photo_preset, list_photo_presets
from gifts.turnstile import verify_turnstile


class OnboardingLoginView(LoginView):
    def get_success_url(self):
        pending = get_pending_group_invite(self.request.user, self.request)
        if pending:
            remember_pending_group_invite(self.request, pending)
        next_step = get_onboarding_next_url(self.request.user, self.request)
        if next_step != reverse("dashboard"):
            return next_step
        return super().get_success_url()


def send_verification_email(request, user):
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    domain = get_current_site(request).domain

    link = f"https://{domain}{reverse('verify_email_confirm', kwargs={'uidb64': uid, 'token': token})}"

    context = {"nickname": user.nickname, "verification_link": link}
    subject = _("Verify your email address on Nos Cadeaux!")
    message_txt = render_to_string("emails/verify_email.txt", context)
    message_html = render_to_string("emails/verify_email.html", context)

    try:
        send_mail(subject, message_txt, None, [user.email], html_message=message_html)
    except (SMTPException, AnymailError, OSError):
        messages.error(request, _("The verification email could not be sent. Please try again shortly."))
        return False
    user.verification_email_sent_at = timezone.now()
    user.save(update_fields=["verification_email_sent_at"])
    return True


def delete_avatar_file(storage, name):
    if name:
        storage.delete(name)


def save_profile_form(request, form, old_email, old_avatar_storage, old_avatar_name):
    user = form.save(commit=False)

    if "avatar" in request.FILES:
        delete_avatar_file(old_avatar_storage, old_avatar_name)
        user.avatar_preset = ""

    if user.email != old_email:
        user.is_verified = False
        user.save()
        send_verification_email(request, user)
        messages.success(request, _("Profile updated! Please verify your new email address."))
        return redirect("verify_email_sent")

    user.save()
    messages.success(request, _("Your profile has been updated!"))
    return redirect("account")


@login_required
@require_http_methods(["GET", "POST"])
def account(request):
    if is_demo_user(request.user) and request.method == "POST":
        return HttpResponseForbidden(_("The public demo account profile cannot be changed."))

    if request.method == "POST":
        old_email = request.user.email
        old_avatar = request.user.avatar
        old_avatar_name = old_avatar.name if old_avatar else ""
        old_avatar_storage = old_avatar.storage
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            return save_profile_form(request, form, old_email, old_avatar_storage, old_avatar_name)
    else:
        form = UserProfileForm(instance=request.user)

    return render(request, "account/account.html", {"form": form})


@login_required
@require_POST
def delete_account(request):
    if is_demo_user(request.user):
        return HttpResponseForbidden(_("The public demo account cannot be deleted."))

    user = request.user
    logout(request)
    user.delete()
    messages.success(request, _("Your account has been successfully deleted."))
    return redirect("welcome")


@require_GET
def verify_email_sent(request):
    if not request.user.is_authenticated:
        return redirect("welcome")
    if request.user.is_verified:
        return redirect(get_onboarding_next_url(request.user, request))
    return render(request, "gifts/verify_email_sent.html", {"email": request.user.email})


@require_GET
def verify_email_confirm(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        newly_verified = not user.is_verified
        user.is_verified = True
        user.save(update_fields=["is_verified"])
        if newly_verified:
            record_onboarding_event("verified", user)
        messages.success(request, _("Your account is now verified !"))
        # Verifying an address is not authentication; keep the current session identity.
        if request.user.is_authenticated and request.user.pk == user.pk:
            return redirect(get_onboarding_next_url(user, request))
        return redirect("login")
    else:
        return render(request, "gifts/verify_email_invalid.html", status=400)


@login_required
@require_POST
def resend_verification(request):
    if request.user.is_verified:
        messages.success(request, _("Your email is already verified."))
        return redirect(get_onboarding_next_url(request.user, request))

    try:
        enforce_auth_rate_limit(
            request,
            AuthThrottleEvent.ACTION_RESEND_VERIFICATION,
            settings.VERIFICATION_RESEND_MAX_ATTEMPTS_PER_IP_WINDOW,
        )
    except AuthRateLimitError:
        messages.warning(request, _("Please wait before requesting another verification email."))
        return redirect("verify_email_sent")

    now = timezone.now()
    resend_before = now - timedelta(seconds=settings.VERIFICATION_EMAIL_RESEND_COOLDOWN_SECONDS)
    claimed = (
        User.objects.filter(pk=request.user.pk, is_verified=False)
        .filter(Q(verification_email_sent_at__isnull=True) | Q(verification_email_sent_at__lte=resend_before))
        .update(verification_email_sent_at=now)
    )
    if not claimed:
        messages.warning(request, _("Please wait before requesting another verification email."))
        return redirect("verify_email_sent")

    request.user.verification_email_sent_at = now
    if send_verification_email(request, request.user):
        messages.success(request, _("A new verification email has been sent."))
    return redirect("verify_email_sent")


@login_required
@require_http_methods(["GET", "POST"])
def onboarding_profile(request):
    if not request.user.is_verified:
        return redirect("verify_email_sent")
    if request.user.profile_completed_at is not None:
        return redirect(get_onboarding_next_url(request.user, request))

    if request.method == "POST":
        form = OnboardingProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            user.profile_completed_at = timezone.now()
            user.save(update_fields=["nickname", "birthday_month", "birthday_day", "profile_completed_at"])
            record_onboarding_event("profile_completed", user)
            messages.success(request, _("Your profile is ready!"))
            return redirect(get_onboarding_next_url(user, request))
    else:
        form = OnboardingProfileForm(instance=request.user)

    return render(request, "registration/onboarding_profile.html", {"form": form})


def set_profile_preset(user, preset):
    if not is_valid_photo_preset("profile", preset):
        return JsonResponse({"success": False, "error": _("Invalid preset photo.")}, status=400)

    delete_avatar_file(user.avatar.storage, user.avatar.name)
    user.avatar = None
    user.avatar_preset = preset
    user.save(update_fields=["avatar", "avatar_preset"])
    return JsonResponse({"success": True, "url": user.display_avatar_url})


def set_profile_photo(user, uploaded):
    if not uploaded:
        return JsonResponse({"success": False, "error": "No file"}, status=400)

    delete_avatar_file(user.avatar.storage, user.avatar.name)
    user.avatar = uploaded
    user.avatar_preset = ""
    user.save(update_fields=["avatar", "avatar_preset"])
    return JsonResponse({"success": True})


@login_required
@require_http_methods(["GET", "POST"])
def photo_upload(request):
    if is_demo_user(request.user) and request.method == "POST":
        return JsonResponse(
            {"success": False, "error": _("The public demo profile picture cannot be changed.")},
            status=403,
        )

    if request.method == "POST":
        preset = request.POST.get("preset", "")
        if preset:
            return set_profile_preset(request.user, preset)

        uploaded = request.FILES.get("photo")
        return set_profile_photo(request.user, uploaded)
    return render(
        request,
        "photos/photo_upload.html",
        {
            "context_type": "profile",
            "photo_presets": list_photo_presets("profile"),
            "back_url": (
                reverse("onboarding_profile") if request.user.profile_completed_at is None else reverse("account")
            ),
            "is_onboarding": request.user.profile_completed_at is None,
        },
    )


def validate_registration(request, form):
    # Count every attempt before form validation, including invalid submissions.
    try:
        enforce_auth_rate_limit(
            request,
            AuthThrottleEvent.ACTION_REGISTER,
            settings.REGISTRATION_MAX_ATTEMPTS_PER_IP_WINDOW,
        )
    except AuthRateLimitError:
        form.add_error(None, _("Too many attempts from your network. Please try again later."))
        return False

    if not form.is_valid():
        return False
    if settings.TURNSTILE_ENABLED and not verify_turnstile(request, action="register"):
        form.add_error(None, _("Human verification failed. Please try again."))
        return False
    return True


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect(get_onboarding_next_url(request.user, request))

    if request.method == "POST":
        form = LocalUserCreationForm(request.POST)
        if validate_registration(request, form):
            user = form.save()
            record_onboarding_event("registered", user)
            user.last_seen_version = settings.APP_VERSION
            user.save(update_fields=["last_seen_version"])

            pending_invite = get_pending_group_invite(user, request)
            if pending_invite:
                remember_pending_group_invite(request, pending_invite, user=user)

            login(request, user, backend="gifts.backends.CaseInsensitiveModelBackend")
            send_verification_email(request, user)

            return redirect("verify_email_sent")
    else:
        form = LocalUserCreationForm()
    return render(
        request,
        "registration/register.html",
        {
            "form": form,
            "turnstile_site_key": settings.TURNSTILE_SITE_KEY if settings.TURNSTILE_ENABLED else "",
        },
    )


@require_GET
def demo_login(request):
    call_command("reset_demo", lazy=True)
    user = User.objects.get(email=DEMO_EMAIL, is_demo=True)
    login(request, user, backend="gifts.backends.CaseInsensitiveModelBackend")
    messages.info(request, _("You are using a public demo account. Demo data resets every 15 minutes."))
    return redirect("dashboard")


class DemoProtectedPasswordChangeView(PasswordChangeView):
    def dispatch(self, request, *args, **kwargs):
        if is_demo_user(request.user):
            return HttpResponseForbidden(_("The public demo account password cannot be changed."))
        return super().dispatch(request, *args, **kwargs)
