import contextlib
import json
import secrets
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from gifts.demo import demo_scope_forbidden_response, has_same_demo_scope

from .models import (
    EventList,
    Gift,
    GuestReservation,
    SecretSantaAssignment,
    SecretSantaExclusion,
    SecretSantaGuestParticipant,
)

_SECURE_RANDOM = secrets.SystemRandom()

# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_json_body(request):
    try:
        return json.loads(request.body), None
    except (ValueError, TypeError) as e:
        return None, JsonResponse({"success": False, "error": str(e)}, status=400)


def _get_event_or_404(token):
    return get_object_or_404(EventList, access_token=token)


def _check_event_demo_scope(request, event):
    if request.user.is_authenticated and not has_same_demo_scope(request.user, event):
        return demo_scope_forbidden_response()
    return None


def _get_guest_identity(request):
    """Return (reserver_user, reserver_name, session_key) for the current request."""
    if request.user.is_authenticated and not request.user.is_managed:
        return request.user, request.user.nickname, ""
    name = request.session.get("guest_name", "")
    sk = request.session.session_key or ""
    return None, name, sk


def _is_surprise_author(gift, reserver_user, session_key):
    if not gift.is_surprise:
        return False
    if reserver_user:
        return gift.created_by_id == reserver_user.id and not gift.guest_author_session_key
    return bool(session_key) and gift.guest_author_session_key == session_key


def _parse_gift_payload(data):
    """Return (fields, error_response) from a JSON gift payload (title, description, url, price)."""
    title = (data.get("title") or "").strip()[:200]
    if not title:
        return None, JsonResponse({"success": False, "error": _("Title required")}, status=400)
    price = None
    price_str = (data.get("price") or "").strip().replace(",", ".")
    if price_str:
        with contextlib.suppress(InvalidOperation):
            price = Decimal(price_str)
    url = (data.get("url") or "").strip()[:1000]
    if url and not url.lower().startswith(("http://", "https://")):
        return None, JsonResponse({"success": False, "error": _("Invalid link")}, status=400)
    return {
        "title": title,
        "description": (data.get("description") or "").strip(),
        "url": url,
        "price": price,
    }, None


def _parse_money(value):
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return amount


def _draw_secret_santa(participant_keys, exclusions):
    if len(participant_keys) < 2:
        return None

    excluded_pairs = set(exclusions)
    givers = participant_keys[:]
    _SECURE_RANDOM.shuffle(givers)
    receivers = participant_keys[:]
    _SECURE_RANDOM.shuffle(receivers)
    assignments = {}

    def assign(index):
        if index == len(givers):
            return True
        giver_key = givers[index]
        candidates = receivers[:]
        _SECURE_RANDOM.shuffle(candidates)
        candidates.sort(
            key=lambda receiver_key: (giver_key == receiver_key, (giver_key, receiver_key) in excluded_pairs)
        )
        for receiver_key in candidates:
            if receiver_key == giver_key or (giver_key, receiver_key) in excluded_pairs:
                continue
            assignments[giver_key] = receiver_key
            receivers.remove(receiver_key)
            if assign(index + 1):
                return True
            receivers.append(receiver_key)
            del assignments[giver_key]
        return False

    return assignments if assign(0) else None


def _participant_key(participant):
    if isinstance(participant, SecretSantaGuestParticipant):
        return f"guest:{participant.id}"
    return f"user:{participant.id}"


def _secret_santa_participant_choices(event):
    users = list(event.secret_santa_participants())
    guests = list(event.secret_santa_guest_participants.order_by("name"))
    choices = [
        {"key": _participant_key(user), "name": user.nickname, "kind": "user", "user": user, "guest": None}
        for user in users
    ]
    choices.extend(
        {"key": _participant_key(guest), "name": guest.name, "kind": "guest", "user": None, "guest": guest}
        for guest in guests
    )
    return choices


def _participant_kwargs(key, prefix):
    key = _normalize_participant_key(key)
    kind, raw_id = (key or "").split(":", 1)
    if kind not in {"user", "guest"}:
        raise ValueError
    participant_id = int(raw_id)
    if kind == "user":
        return {f"{prefix}_id": participant_id, f"{prefix}_guest_id": None}
    return {f"{prefix}_id": None, f"{prefix}_guest_id": participant_id}


def _normalize_participant_key(key):
    if key and ":" not in key:
        return f"user:{key}"
    return key


# ── Public views ─────────────────────────────────────────────────────────────


@require_GET
def event_detail(request, token):
    event = _get_event_or_404(token)
    err = _check_event_demo_scope(request, event)
    if err:
        return err
    is_owner = request.user.is_authenticated and request.user == event.owner

    reserver_user, guest_name, session_key = _get_guest_identity(request)
    has_identity = bool(reserver_user or guest_name)

    # Owner: all their own wishes, but never surprises. Visitors: visible wishes and all surprises.
    gifts_qs = event.gifts.filter(is_surprise=False) if is_owner else event.gifts.filter(is_hidden=False)
    gifts_qs = gifts_qs.order_by("created_at")

    # Annotate each gift with reservation state. Reservations are never loaded for the
    # owner so nothing about them can leak into the page.
    gifts_list = []
    for gift in gifts_qs:
        my_res = None
        other_res = []
        if not is_owner:
            reservations = list(gift.guest_reservations.select_related("reserver_user").all())
            if reserver_user:
                my_res = next((r for r in reservations if r.reserver_user_id == reserver_user.id), None)
            elif session_key:
                my_res = next((r for r in reservations if r.session_key == session_key), None)
            other_res = [r for r in reservations if r != my_res]
        gifts_list.append(
            {
                "gift": gift,
                "my_reservation": my_res,
                "other_reservations": other_res,
                "has_exclusive_other": any(r.exclusivity for r in other_res),
                "is_my_surprise": not is_owner and _is_surprise_author(gift, reserver_user, session_key),
            }
        )

    # Existing participant names (for identity modal "pick from list")
    participants = []
    if not is_owner:
        participants = (
            GuestReservation.objects.filter(gift__event_list=event)
            .exclude(reserver_name="")
            .values_list("reserver_name", flat=True)
            .distinct()
            .order_by("reserver_name")
        )
        if reserver_user:
            participants = [p for p in participants if p != reserver_user.nickname]

    # Track authenticated visitors so the list appears on their dashboard
    if request.user.is_authenticated and not is_owner:
        event.participants.add(request.user)

    secret_santa_participants = []
    secret_santa_participant_choices = []
    secret_santa_exclusions = []
    secret_santa_assignments = []
    secret_santa_guest_assignments = []
    my_secret_santa_assignment = None
    if event.is_secret_santa:
        secret_santa_participants = list(event.secret_santa_participants())
        secret_santa_participant_choices = _secret_santa_participant_choices(event)
        secret_santa_exclusions = list(
            event.secret_santa_exclusions.select_related("giver", "receiver", "giver_guest", "receiver_guest")
        )
        secret_santa_assignments = list(
            event.secret_santa_assignments.select_related("giver", "receiver", "giver_guest", "receiver_guest")
        )
        secret_santa_guest_assignments = [
            assignment for assignment in secret_santa_assignments if assignment.giver_guest_id
        ]
        if request.user.is_authenticated:
            my_secret_santa_assignment = next(
                (assignment for assignment in secret_santa_assignments if assignment.giver_id == request.user.id),
                None,
            )

    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "is_owner": is_owner,
            "gifts_list": gifts_list,
            "has_identity": has_identity,
            "guest_name": guest_name,
            "reserver_user": reserver_user,
            "participants": list(participants),
            "total_gifts": len(gifts_list),
            "hidden_count": event.gifts.filter(is_hidden=True, is_surprise=False).count() if is_owner else 0,
            "reserved_count": sum(1 for g in gifts_list if g["my_reservation"] or g["other_reservations"]),
            "has_my_reservations": any(g["my_reservation"] for g in gifts_list),
            "secret_santa_participants": secret_santa_participants,
            "secret_santa_participant_choices": secret_santa_participant_choices,
            "secret_santa_exclusions": secret_santa_exclusions,
            "secret_santa_assignments": secret_santa_assignments,
            "secret_santa_guest_assignments": secret_santa_guest_assignments,
            "my_secret_santa_assignment": my_secret_santa_assignment,
        },
    )


@require_POST
def set_guest_name(request, token):
    event = _get_event_or_404(token)
    err = _check_event_demo_scope(request, event)
    if err:
        return err
    data, err = _parse_json_body(request)
    if err:
        return err
    name = (data.get("name") or "").strip()[:100]
    if not name:
        return JsonResponse({"success": False, "error": _("Name required")}, status=400)
    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key
    request.session["guest_name"] = name
    # Transfer anonymous reservations made under this name to the current session
    GuestReservation.objects.filter(
        gift__event_list=event,
        reserver_name__iexact=name,
        reserver_user=None,
    ).exclude(session_key=session_key).update(session_key=session_key)
    Gift.objects.filter(
        event_list=event,
        is_surprise=True,
        guest_author_name__iexact=name,
    ).exclude(guest_author_session_key="").update(guest_author_session_key=session_key)
    return JsonResponse({"success": True, "name": name})


@require_POST
def reserve_event_gift(request, token, gift_id):
    event = _get_event_or_404(token)
    err = _check_event_demo_scope(request, event)
    if err:
        return err
    gift = get_object_or_404(Gift, id=gift_id, event_list=event, is_hidden=False)

    reserver_user, reserver_name, session_key = _get_guest_identity(request)
    if not reserver_user and not reserver_name:
        return JsonResponse({"success": False, "error": _("Identity required")}, status=400)

    exclusivity = False
    with contextlib.suppress(ValueError, TypeError):
        body = json.loads(request.body)
        exclusivity = bool(body.get("exclusivity", False))

    if reserver_user:
        existing = GuestReservation.objects.filter(gift=gift, reserver_user=reserver_user).first()
    else:
        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key
        existing = GuestReservation.objects.filter(gift=gift, session_key=session_key).first()

    if existing:
        existing.delete()
        return JsonResponse({"success": True, "reserved": False})
    else:
        GuestReservation.objects.create(
            gift=gift,
            reserver_user=reserver_user,
            reserver_name=reserver_name,
            session_key=session_key,
            exclusivity=exclusivity,
        )
        return JsonResponse({"success": True, "reserved": True, "reserver_name": reserver_name})


# ── Visitor surprises ────────────────────────────────────────────────────────


def _get_visitor_identity_or_error(request, event):
    """Return (reserver_user, name, session_key, error_response) for a visitor allowed to add surprises."""
    if request.user.is_authenticated and request.user == event.owner:
        return (
            None,
            "",
            "",
            JsonResponse({"success": False, "error": _("You can't add a surprise to your own list")}, status=403),
        )
    reserver_user, name, session_key = _get_guest_identity(request)
    if not reserver_user and not name:
        return None, "", "", JsonResponse({"success": False, "error": _("Identity required")}, status=400)
    if not reserver_user and not session_key:
        request.session.create()
        session_key = request.session.session_key
    return reserver_user, name, session_key, None


@require_POST
def add_event_surprise(request, token):
    event = _get_event_or_404(token)
    err = _check_event_demo_scope(request, event)
    if err:
        return err
    reserver_user, name, session_key, err = _get_visitor_identity_or_error(request, event)
    if err:
        return err
    data, err = _parse_json_body(request)
    if err:
        return err
    fields, err = _parse_gift_payload(data)
    if err:
        return err

    gift = Gift.objects.create(
        owner=event.owner,
        created_by=reserver_user or event.owner,
        event_list=event,
        is_surprise=True,
        guest_author_name=name,
        guest_author_session_key="" if reserver_user else session_key,
        **fields,
    )
    return JsonResponse({"success": True, "gift_id": gift.id})


def _get_own_surprise_or_error(request, token, gift_id):
    event = _get_event_or_404(token)
    err = _check_event_demo_scope(request, event)
    if err:
        return None, err
    reserver_user, _name, session_key, err = _get_visitor_identity_or_error(request, event)
    if err:
        return None, err
    gift = get_object_or_404(Gift, id=gift_id, event_list=event, is_surprise=True)
    if not _is_surprise_author(gift, reserver_user, session_key):
        return None, JsonResponse({"success": False, "error": _("You don't have permission to do this")}, status=403)
    return gift, None


@require_POST
def edit_event_surprise(request, token, gift_id):
    gift, err = _get_own_surprise_or_error(request, token, gift_id)
    if err:
        return err
    data, err = _parse_json_body(request)
    if err:
        return err
    fields, err = _parse_gift_payload(data)
    if err:
        return err
    for key, value in fields.items():
        setattr(gift, key, value)
    gift.save()
    return JsonResponse({"success": True})


@require_POST
def delete_event_surprise(request, token, gift_id):
    gift, err = _get_own_surprise_or_error(request, token, gift_id)
    if err:
        return err
    gift.delete()
    return JsonResponse({"success": True})


# ── Owner-only views ─────────────────────────────────────────────────────────


@login_required
@require_POST
def create_event_list(request):
    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()
    date_str = request.POST.get("event_date", "").strip()
    mode = request.POST.get("mode", EventList.MODE_WISHLIST)
    if mode not in dict(EventList.MODE_CHOICES):
        mode = EventList.MODE_WISHLIST

    if not name:
        return redirect("dashboard")

    event = EventList(
        name=name,
        owner=request.user,
        description=description,
        mode=mode,
        budget_max=_parse_money(request.POST.get("budget_max")),
        is_demo=request.user.is_demo,
    )
    if date_str:
        with contextlib.suppress(ValueError):
            event.event_date = date.fromisoformat(date_str)
    event.save()
    return redirect("event_detail", token=event.access_token)


def _check_owner(request, event):
    if request.user != event.owner:
        from django.http import HttpResponseForbidden  # noqa: PLC0415

        return HttpResponseForbidden(_("You don't have permission to do this"))
    return None


@login_required
@require_POST
def add_event_gift(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    data, parse_err = _parse_json_body(request)
    if parse_err:
        return parse_err

    title = (data.get("title") or "").strip()
    if not title:
        return JsonResponse({"success": False, "error": _("Title required")}, status=400)

    price = None
    price_str = (data.get("price") or "").strip().replace(",", ".")
    if price_str:
        with contextlib.suppress(InvalidOperation):
            price = Decimal(price_str)

    gift = Gift.objects.create(
        owner=event.owner,
        created_by=event.owner,
        title=title,
        description=(data.get("description") or "").strip(),
        url=(data.get("url") or "").strip(),
        price=price,
        event_list=event,
    )
    return JsonResponse({"success": True, "gift_id": gift.id})


@login_required
@require_POST
def edit_event_gift(request, token, gift_id):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    gift = get_object_or_404(Gift, id=gift_id, event_list=event, is_surprise=False)
    data, parse_err = _parse_json_body(request)
    if parse_err:
        return parse_err

    title = (data.get("title") or "").strip()
    if not title:
        return JsonResponse({"success": False, "error": _("Title required")}, status=400)

    price_str = (data.get("price") or "").strip().replace(",", ".")
    price = None
    if price_str:
        with contextlib.suppress(InvalidOperation):
            price = Decimal(price_str)

    gift.title = title
    gift.description = (data.get("description") or "").strip()
    gift.url = (data.get("url") or "").strip()
    gift.price = price
    gift.save()
    return JsonResponse({"success": True})


@login_required
@require_POST
def delete_event_gift(request, token, gift_id):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    gift = get_object_or_404(Gift, id=gift_id, event_list=event, is_surprise=False)
    gift.delete()
    return redirect("event_detail", token=token)


@login_required
@require_POST
def toggle_event_gift_hidden(request, token, gift_id):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    gift = get_object_or_404(Gift, id=gift_id, event_list=event, is_surprise=False)
    gift.is_hidden = not gift.is_hidden
    gift.save()
    return JsonResponse({"success": True, "hidden": gift.is_hidden})


@login_required
@require_POST
def transfer_event_gift(request, token, gift_id):
    """Move a gift from the event list to the owner's personal wish list (shared with all groups)."""
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    gift = get_object_or_404(Gift, id=gift_id, event_list=event, is_surprise=False)
    gift.guest_reservations.all().delete()
    gift.event_list = None
    gift.save()
    user_groups = request.user.gift_groups.all()
    if user_groups.exists():
        gift.visible_in.set(user_groups)
    from django.contrib import messages

    messages.success(request, _("Gift transferred to your wish list."))
    return redirect("event_detail", token=token)


@login_required
@require_POST
def regenerate_event_token(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    event.access_token = uuid.uuid4().hex[:8].upper()
    event.save()
    return redirect("event_detail", token=event.access_token)


@login_required
def event_photo_upload(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    if request.method == "POST":
        uploaded = request.FILES.get("photo")
        if not uploaded:
            return JsonResponse({"success": False, "error": "No file"}, status=400)
        event.image = uploaded
        event.save(update_fields=["image"])
        return JsonResponse({"success": True})
    return render(
        request,
        "photos/photo_upload.html",
        {
            "context_type": "event",
            "event_obj": event,
            "back_url": reverse("event_detail", args=[token]),
        },
    )


@login_required
@require_POST
def edit_event_info(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    name = request.POST.get("name", "").strip()
    if name:
        event.name = name
    event.description = request.POST.get("description", "").strip()
    date_str = request.POST.get("event_date", "").strip()
    if date_str:
        with contextlib.suppress(ValueError):
            event.event_date = date.fromisoformat(date_str)
    else:
        event.event_date = None
    event.budget_max = _parse_money(request.POST.get("budget_max"))
    event.save()
    return redirect("event_detail", token=event.access_token)


@login_required
@require_POST
def add_secret_santa_guest_participant(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    if not event.is_secret_santa:
        return redirect("event_detail", token=event.access_token)

    name = (request.POST.get("name") or "").strip()[:100]
    if not name:
        messages.error(request, _("Guest name is required."))
        return redirect("event_detail", token=event.access_token)

    guest, _created = SecretSantaGuestParticipant.objects.get_or_create(event=event, name=name)
    SecretSantaExclusion.objects.get_or_create(event=event, giver_guest=guest, receiver=event.owner)
    event.secret_santa_assignments.all().delete()
    messages.success(request, _("Guest participant added."))
    return redirect("event_detail", token=event.access_token)


@login_required
@require_POST
def delete_secret_santa_guest_participant(request, token, guest_id):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    SecretSantaGuestParticipant.objects.filter(id=guest_id, event=event).delete()
    event.secret_santa_assignments.all().delete()
    messages.success(request, _("Guest participant deleted."))
    return redirect("event_detail", token=event.access_token)


@login_required
@require_POST
def add_secret_santa_exclusion(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    if not event.is_secret_santa:
        return redirect("event_detail", token=event.access_token)

    participant_keys = {choice["key"] for choice in _secret_santa_participant_choices(event)}
    giver_key = _normalize_participant_key(request.POST.get("giver"))
    receiver_key = _normalize_participant_key(request.POST.get("receiver"))
    try:
        giver_kwargs = _participant_kwargs(giver_key, "giver")
        receiver_kwargs = _participant_kwargs(receiver_key, "receiver")
    except (AttributeError, TypeError, ValueError):
        messages.error(request, _("Please choose two participants."))
        return redirect("event_detail", token=event.access_token)

    if giver_key == receiver_key or giver_key not in participant_keys or receiver_key not in participant_keys:
        messages.error(request, _("This exclusion is not valid."))
        return redirect("event_detail", token=event.access_token)

    SecretSantaExclusion.objects.get_or_create(event=event, **giver_kwargs, **receiver_kwargs)
    if request.POST.get("both_directions"):
        SecretSantaExclusion.objects.get_or_create(
            event=event,
            **_participant_kwargs(receiver_key, "giver"),
            **_participant_kwargs(giver_key, "receiver"),
        )
    event.secret_santa_assignments.all().delete()
    messages.success(request, _("Exclusion added. Run the draw again when ready."))
    return redirect("event_detail", token=event.access_token)


@login_required
@require_POST
def delete_secret_santa_exclusion(request, token, exclusion_id):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    SecretSantaExclusion.objects.filter(id=exclusion_id, event=event).delete()
    event.secret_santa_assignments.all().delete()
    messages.success(request, _("Exclusion deleted. Run the draw again when ready."))
    return redirect("event_detail", token=event.access_token)


@login_required
@require_POST
def draw_secret_santa(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    if not event.is_secret_santa:
        return redirect("event_detail", token=event.access_token)

    participant_keys = [choice["key"] for choice in _secret_santa_participant_choices(event)]
    exclusions = [(exclusion.giver_key, exclusion.receiver_key) for exclusion in event.secret_santa_exclusions.all()]
    assignments = _draw_secret_santa(participant_keys, exclusions)
    if not assignments:
        messages.error(
            request,
            _("The draw is impossible with the current participants and exclusions."),
        )
        return redirect("event_detail", token=event.access_token)

    with transaction.atomic():
        event.secret_santa_assignments.all().delete()
        SecretSantaAssignment.objects.bulk_create(
            [
                SecretSantaAssignment(
                    event=event,
                    **_participant_kwargs(giver_key, "giver"),
                    **_participant_kwargs(receiver_key, "receiver"),
                )
                for giver_key, receiver_key in assignments.items()
            ]
        )
    messages.success(request, _("Secret Santa draw completed."))
    return redirect("event_detail", token=event.access_token)


@login_required
@require_POST
def leave_event_list(request, token):
    """Remove the current user from the event's participants (hides it from their dashboard)."""
    event = _get_event_or_404(token)
    event.participants.remove(request.user)
    return redirect("dashboard")


@login_required
@require_POST
def delete_event_list(request, token):
    event = _get_event_or_404(token)
    err = _check_owner(request, event)
    if err:
        return err
    event.delete()
    return redirect("dashboard")
