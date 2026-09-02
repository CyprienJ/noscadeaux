import datetime
import heapq
import json
import random
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from gifts.demo import demo_scope_forbidden_response, has_same_demo_scope, is_demo_user
from gifts.release_notes import (
    ReleaseNoteError,
    load_release_notes,
    localized_release_notes,
    localized_release_notes_between,
    parse_version,
)

from .models import (
    BalanceSettlement,
    EventList,
    Gift,
    GiftComment,
    GiftTag,
    Group,
    NotificationDigestPreference,
    Reservation,
    SecretSantaAssignment,
    Subscription,
    User,
)
from .onboarding import get_onboarding_next_url

OFFER_MODAL_CONTENT_PATH = "gifts/includes/_offer_modal_content.html"
RESERVE_MODAL_MODEL_PATH = "gifts/includes/_reserve_modal_content.html"
EDIT_AMOUNTS_PATH = "gifts/includes/_edit_offered_amounts_content.html"
USER_NOT_FOUND_TEMPLATE = "gifts/user_not_found.html"
ACCESS_REFUSED_MSG = "You don't have access to this list"
METHOD_NOT_AUTHORIZED_MESSAGE = "Method {} not authorized"
GROUP_NOT_FOUND = "Group not found."
PERMISSION_DENIED = "You don't have permission to do this"
INVALID_AMOUNT_FORMAT = _("Invalid amount format.")
UNSUBSCRIBE_SUCCESS_MSG = "You are no longer subscribed to %(name)s's list"
COMMENT_MAX_LENGTH = 1000
COMMENT_EMPTY_MESSAGE = _("Comment cannot be empty.")
COMMENT_TOO_LONG_MESSAGE = _("Comment is too long.")
INVALID_TAG_SELECTION_MESSAGE = _("Invalid tag selection.")


# --- Helpers ---


def _parse_json_body(request):
    try:
        return json.loads(request.body), None
    except ValueError:
        return None, JsonResponse({"success": False, "error": "ValueError: Please provide valid data"}, status=400)
    except TypeError:
        return None, JsonResponse({"success": False, "error": "TypeError: Please provide valid data"}, status=400)


def _redirect_to_referer_or(request, view_name, **kwargs):
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    return redirect(view_name, **kwargs)


def _reminder_days_from_post(request, field_name, default):
    try:
        value = int(request.POST.get(field_name, default))
    except (TypeError, ValueError):
        return default
    return value if 0 <= value <= 365 else default


def _subscription_removed_message(owner_name):
    return _(UNSUBSCRIBE_SUCCESS_MSG) % {"name": owner_name}


def _delivery_from_post(request):
    delivery = request.POST.get("delivery")
    return delivery if delivery in {"email", "rss", "both"} else "email"


def _gift_tags_from_post(request):
    requested_slugs = set(request.POST.getlist("tags"))
    if not requested_slugs:
        return GiftTag.objects.none()
    if not requested_slugs.issubset(set(GiftTag.Slug.values)):
        return None
    tags = GiftTag.objects.filter(slug__in=requested_slugs)
    if tags.count() != len(requested_slugs):
        return None
    return tags


def _check_gift_access(request, gift):
    if not has_same_demo_scope(request.user, gift.owner):
        return demo_scope_forbidden_response()

    is_owner = gift.owner == request.user
    if gift.shared_list_id and gift.shared_list.members.filter(id=request.user.id).exists():
        is_owner = True
    is_reserver = Reservation.objects.filter(gift=gift, reserver=request.user).exists()
    if gift.owner.is_managed:
        mm = getattr(gift.owner, "managed_member_profile", None)
        if mm and request.user in mm.group.members.all():
            return None
    if not (is_owner or is_reserver):
        return HttpResponseForbidden(_(PERMISSION_DENIED))
    return None


def _reservation_state(gift, reservations, user, group_id=None):
    user_res = next((r for r in reservations if r.reserver_id == user.id), None)
    exclusive_res = next((r for r in reservations if r.exclusivity), None)
    participant_count = len(reservations)
    group_id_str = str(group_id) if group_id else ""
    reserved_group_id = str(gift.group_reserved_on_id) if gift.group_reserved_on_id else ""
    reserved_elsewhere = bool(group_id_str and reserved_group_id and reserved_group_id != group_id_str)

    if not reservations:
        status = "available"
        label = _("Available")
        action_label = _("Reserve")
        icon = "bi-lock"
    elif reserved_elsewhere:
        status = "reserved_other_group"
        label = _("Reserved in another group")
        action_label = _("Reserved elsewhere")
        icon = "bi-lock-fill"
    elif exclusive_res and user_res:
        status = "reserved_by_me_exclusive"
        label = _("Reserved by you")
        action_label = _("My reservation")
        icon = "bi-person-check-fill"
    elif exclusive_res:
        status = "reserved_by_other_exclusive"
        label = _("Reserved exclusively")
        action_label = _("View reservation")
        icon = "bi-lock-fill"
    elif user_res:
        status = "participating_by_me"
        label = _("You participate")
        action_label = _("My participation")
        icon = "bi-people-fill"
    else:
        status = "participating_by_others"
        label = _("Participation open")
        action_label = _("Participate")
        icon = "bi-people"

    return {
        "status": status,
        "label": label,
        "action_label": action_label,
        "icon": icon,
        "participant_count": participant_count,
        "exclusive_reservation": exclusive_res,
        "is_reserved": bool(reservations),
        "is_exclusive": exclusive_res is not None,
        "is_mine": user_res is not None,
        "reserved_elsewhere": reserved_elsewhere,
        "can_open": not reserved_elsewhere,
        "can_join": status in {"available", "participating_by_others"},
        "can_offer": user_res is not None,
    }


def _gift_item(request, gift, reservations, group_id, other_members=None):
    user_res = next((r for r in reservations if r.reserver_id == request.user.id), None)
    participant_ids = {r.reserver_id for r in reservations}
    other_non_participants = []
    if other_members is not None:
        other_non_participants = [m for m in other_members if m.id not in participant_ids]

    return {
        "current_user": request.user,
        "gift": gift,
        "reservations": reservations,
        "num_reservations": len(reservations),
        "user_reservation": user_res,
        "other_non_participant": other_non_participants,
        "group_id": group_id,
        "comments": _gift_comments(gift, group_id),
        "reservation_state": _reservation_state(gift, reservations, request.user, group_id),
    }


def _get_reservation_group(request, gift, group_id):
    if gift.is_draft:
        return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    try:
        group = Group.objects.get(id=int(group_id))
    except (Group.DoesNotExist, TypeError, ValueError):
        return None, JsonResponse({"success": False, "error": _(GROUP_NOT_FOUND)}, status=400)

    if not group.members.filter(id=request.user.id).exists():
        return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    if gift.shared_list_id:
        if gift.shared_list.members.filter(id=request.user.id).exists():
            return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
        if not gift.shared_publications.filter(group=group).exists():
            return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    elif not group.members.filter(id=gift.owner_id).exists():
        return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    recipient = gift.shared_list if gift.shared_list_id else gift.owner
    if not has_same_demo_scope(request.user, group) or not has_same_demo_scope(request.user, recipient):
        return None, demo_scope_forbidden_response()

    if gift.group_reserved_on_id and gift.group_reserved_on_id != group.id:
        return None, JsonResponse(
            {"success": False, "error": _("This gift is already reserved in another group")},
            status=409,
        )

    return group, None


def _gift_is_visible_in_group(gift, group):
    if gift.is_draft:
        return False
    visible_group_ids = set(gift.visible_in.values_list("id", flat=True))
    return not visible_group_ids or group.id in visible_group_ids


def _get_comment_group(request, gift, group_id):
    if gift.owner == request.user or (
        gift.shared_list_id and gift.shared_list.members.filter(id=request.user.id).exists()
    ):
        return None, HttpResponseForbidden(_("Gift comments are hidden from the gift owner."))

    try:
        group = Group.objects.get(id=int(group_id))
    except (Group.DoesNotExist, TypeError, ValueError):
        return None, JsonResponse({"success": False, "error": _(GROUP_NOT_FOUND)}, status=400)

    if not group.members.filter(id=request.user.id).exists():
        return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    if gift.shared_list_id:
        if not gift.shared_publications.filter(group=group).exists():
            return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    elif not group.members.filter(id=gift.owner_id).exists():
        return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    recipient = gift.shared_list if gift.shared_list_id else gift.owner
    if not has_same_demo_scope(request.user, group) or not has_same_demo_scope(request.user, recipient):
        return None, demo_scope_forbidden_response()

    if not _gift_is_visible_in_group(gift, group):
        return None, HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    return group, None


def _gift_comments(gift, group_id):
    if not group_id:
        return GiftComment.objects.none()
    return (
        GiftComment.objects.filter(gift=gift, group_id=group_id)
        .select_related("author", "group")
        .order_by("created_at", "id")
    )


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _render_gift_comments_panel(request, gift, group_id):
    context = {
        "item": {
            "current_user": request.user,
            "gift": gift,
            "group_id": group_id,
            "comments": _gift_comments(gift, group_id),
        }
    }
    return render(request, "gifts/includes/_gift_comments_panel.html", context)


def _reservation_group_for_gift(gift, common_groups):
    if gift.group_reserved_on_id:
        return next((g for g in common_groups if g.id == gift.group_reserved_on_id), None)

    visible_group_ids = {group.id for group in gift.visible_in.all()}
    if visible_group_ids:
        return next((g for g in common_groups if g.id in visible_group_ids), None)

    return common_groups[0] if common_groups else None


def _render_reservation_modal(request, gift, group_id, reservations, extra_exclude_ids=None):
    user_res = next((r for r in reservations if r.reserver_id == request.user.id), None)
    group = get_object_or_404(Group, id=group_id)
    if gift.shared_list_id:
        recipient_ids = list(gift.shared_list.members.values_list("id", flat=True))
    else:
        recipient_ids = [gift.owner_id]
    exclude_ids = [request.user.id, *recipient_ids]
    if extra_exclude_ids:
        exclude_ids += list(extra_exclude_ids)
    other_members = group.members.exclude(id__in=exclude_ids)
    context = {
        "item": {
            "current_user": request.user,
            "gift": gift,
            "reservations": reservations,
            "user_reservation": user_res,
            "other_non_participant": other_members,
            "group_id": group_id,
            "comments": _gift_comments(gift, group_id),
            "reservation_state": _reservation_state(gift, reservations, request.user, group_id),
        }
    }
    return render(request, RESERVE_MODAL_MODEL_PATH, context)


def _build_amounts_modal_context(gift, offer_group, reservations):
    givers = list(offer_group.members.exclude(id=gift.owner_id)) if offer_group else []
    payer_res = next((r for r in reservations if r.amount_paid), None) or (reservations[0] if reservations else None)
    pre_payer_id = payer_res.reserver_id if payer_res else None
    split_qs = list(gift.expense_split.values_list("id", flat=True))
    pre_split_ids = split_qs if split_qs else [r.reserver_id for r in reservations]
    return {
        "gift": gift,
        "givers": givers,
        "pre_payer_id": pre_payer_id,
        "pre_split_ids": pre_split_ids,
        "actual_cost": float(gift.actual_cost) if gift.actual_cost else "",
    }


def _apply_payers(gift, payers):
    Reservation.objects.filter(gift=gift).update(amount_paid=None)
    for uid_str, amount_str in payers.items():
        try:
            uid = int(uid_str)
            amount = Decimal(str(amount_str).replace(",", ".")) if amount_str else None
            res, _ = Reservation.objects.get_or_create(gift=gift, reserver_id=uid)
            res.amount_paid = amount
            res.save()
        except (ValueError, InvalidOperation):
            return JsonResponse({"success": False, "error": INVALID_AMOUNT_FORMAT}, status=400)
    return None


def _resolve_offer_group(gift, group_id):
    if gift.group_reserved_on or not group_id:
        return gift.group_reserved_on, None
    try:
        return Group.objects.get(id=int(group_id)), None
    except Group.DoesNotExist:
        return None, JsonResponse({"success": False, "error": _(GROUP_NOT_FOUND)}, status=404)
    except (ValueError, TypeError):
        return None, JsonResponse({"success": False, "error": _("Invalid group ID.")}, status=400)


def _set_actual_cost(gift, actual_cost):
    if not actual_cost:
        return None
    try:
        gift.actual_cost = Decimal(str(actual_cost).replace(",", "."))
    except (InvalidOperation, ValueError):
        return JsonResponse({"success": False, "error": INVALID_AMOUNT_FORMAT}, status=400)
    return None


def _set_offer_group(gift, group_id):
    if not group_id or gift.group_reserved_on_id:
        return None
    try:
        gift.group_reserved_on = Group.objects.get(id=int(group_id))
    except (Group.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"success": False, "error": _(GROUP_NOT_FOUND)}, status=400)
    return None


def _resolve_receive_group(gift, group_id):
    if gift.group_reserved_on or not group_id or group_id == "none":
        return gift.group_reserved_on, None
    try:
        return Group.objects.get(id=int(group_id)), None
    except Group.DoesNotExist:
        return None, JsonResponse({"success": False, "error": _("GROUP_NOT_FOUND")}, status=404)
    except (ValueError, TypeError):
        return None, JsonResponse({"success": False, "error": _("Invalid group ID.")}, status=400)


def _mark_gift_offered(gift):
    gift.offered = True
    gift.offered_at = timezone.now()
    gift.save()


# --- Views ---


def redirect_dashboard():
    return redirect("dashboard")


def welcome(request):
    if request.user.is_authenticated:
        return redirect(get_onboarding_next_url(request.user, request))
    return render(request, "gifts/welcome.html")


@require_GET
def privacy(request):
    return render(request, "gifts/privacy.html", {"contact_email": "cyprien.jorant@pm.me"})


@require_GET
def changelog(request):
    releases = list(reversed(localized_release_notes(get_language())))
    page = Paginator(releases, 10).get_page(request.GET.get("page"))
    return render(request, "gifts/changelog.html", {"page": page})


@login_required
@require_POST
def unseen_release_notes(request):
    current_version = settings.APP_VERSION
    previous_version = request.user.last_seen_version

    try:
        current_key = parse_version(current_version)
        previous_key = parse_version(previous_version) if previous_version else None
        load_release_notes()
    except ReleaseNoteError as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    if previous_key is not None and previous_key >= current_key:
        return JsonResponse({"current_version": current_version, "releases": []})

    claimed = User.objects.filter(pk=request.user.pk, last_seen_version=previous_version).update(
        last_seen_version=current_version
    )
    if not claimed or previous_key is None:
        return JsonResponse({"current_version": current_version, "releases": []})

    releases = localized_release_notes_between(previous_version, current_version, get_language())
    response = JsonResponse({"current_version": current_version, "releases": releases})
    response.headers["Cache-Control"] = "no-store"
    return response


@login_required
def dashboard(request):
    user_groups = request.user.gift_groups.prefetch_related("members").all()
    user_event_lists = EventList.objects.filter(owner=request.user).order_by("-created_at")
    user_shared_lists = request.user.shared_lists.filter(deleted_at__isnull=True).prefetch_related("members")
    participating_event_lists = EventList.objects.filter(participants=request.user).exclude(owner=request.user)
    today = timezone.localdate()
    my_reservations_qs = Reservation.objects.filter(
        reserver=request.user,
        gift__offered=False,
        gift__is_draft=False,
        gift__event_list__isnull=True,
    )
    my_reservations = my_reservations_qs.select_related(
        "gift", "gift__owner", "gift__shared_list", "gift__group_reserved_on"
    ).order_by("-created_at")[:4]
    recent_group_gifts = (
        Gift.objects.filter(
            visible_in__members=request.user,
            offered=False,
            event_list__isnull=True,
            shared_list__isnull=True,
            is_draft=False,
        )
        .exclude(owner=request.user)
        .select_related("owner", "created_by")
        .order_by("-created_at")
        .distinct()[:4]
    )
    upcoming_events = (
        EventList.objects.filter(Q(owner=request.user) | Q(participants=request.user), event_date__gte=today)
        .distinct()
        .order_by("event_date")[:4]
    )
    upcoming_birthdays = _upcoming_birthdays(request.user, user_groups, today)
    balance_summaries = _dashboard_balance_summaries(request.user, user_groups)
    open_wish_count = Gift.objects.filter(
        owner=request.user,
        shared_list__isnull=True,
        created_by=request.user,
        offered=False,
        event_list__isnull=True,
        is_draft=False,
    ).count()
    event_count = user_event_lists.count() + participating_event_lists.count()
    my_reservation_count = my_reservations_qs.count()
    current_emoji_set = emojis()
    return render(
        request,
        "gifts/dashboard.html",
        {
            "user_groups": user_groups,
            "user_event_lists": user_event_lists,
            "user_shared_lists": user_shared_lists,
            "participating_event_lists": participating_event_lists,
            "open_wish_count": open_wish_count,
            "event_count": event_count,
            "my_reservation_count": my_reservation_count,
            "my_reservations": my_reservations,
            "recent_group_gifts": recent_group_gifts,
            "upcoming_events": upcoming_events,
            "upcoming_birthdays": upcoming_birthdays,
            "balance_summaries": balance_summaries,
            "greeting_emoji": random.choice(current_emoji_set),
            "rain_emojis": current_emoji_set,
        },
    )


def _upcoming_birthdays(user, groups, today):
    members = (
        User.objects.filter(gift_groups__in=groups, birthday_month__isnull=False, birthday_day__isnull=False)
        .exclude(id=user.id)
        .distinct()
    )
    upcoming = []
    for member in members:
        try:
            next_date = datetime.date(today.year, member.birthday_month, member.birthday_day)
        except ValueError:
            continue
        if next_date < today:
            next_date = datetime.date(today.year + 1, member.birthday_month, member.birthday_day)
        days_until = (next_date - today).days
        if days_until <= 45:
            upcoming.append({"member": member, "date": next_date, "days_until": days_until})
    return sorted(upcoming, key=lambda item: item["date"])[:4]


def _dashboard_balance_summaries(user, groups):
    summaries = []
    for group in groups:
        if not group.show_balance:
            continue
        balances, transactions, _ = compute_group_balances(group)
        my_balance = balances.get(user, Decimal("0.00"))
        if abs(my_balance) <= Decimal("0.01") and not transactions:
            continue
        summaries.append(
            {
                "group": group,
                "my_balance": my_balance,
                "my_balance_abs": abs(my_balance),
                "transactions": transactions[:2],
            }
        )
    return summaries[:3]


def emojis():
    christmas_emojis = ["🎄", "🎁", "🎅", "🤶", "🧑‍🎄", "⛄", "✨", "🌟", "🔔", "🦌"]
    gift_emojis = ["🎁", "🎈", "🎊", "🎉", "✨", "🍰", "🥳", "🎀"]
    if datetime.date.today().month == 12 and datetime.date.today().day <= 25:
        return christmas_emojis
    else:
        return gift_emojis


@login_required
def view_list(request: HttpRequest, user_id: int):
    target_user = User.objects.filter(id=user_id).first()
    if not target_user:
        return render(request, USER_NOT_FOUND_TEMPLATE, status=404)
    if not has_same_demo_scope(request.user, target_user):
        return render(request, USER_NOT_FOUND_TEMPLATE, status=403)

    is_owner = request.user.id == target_user.id
    from_group_id = request.GET.get("from_group")
    common_groups = []

    group = None
    if from_group_id:
        group = get_object_or_404(Group, id=from_group_id)

    if not is_owner:
        common_groups = list(Group.objects.filter(members=request.user).filter(members=target_user))
        has_secret_santa_access = SecretSantaAssignment.objects.filter(
            giver=request.user,
            receiver=target_user,
            giver_guest__isnull=True,
            receiver_guest__isnull=True,
        ).exists()
        if not common_groups and not has_secret_santa_access:
            return render(request, USER_NOT_FOUND_TEMPLATE, status=403)
        if group and group.id not in {g.id for g in common_groups}:
            return render(request, USER_NOT_FOUND_TEMPLATE, status=403)

    all_gifts_query: QuerySet[Gift] = Gift.objects.filter(
        owner=target_user,
        shared_list__isnull=True,
        offered=False,
    )
    if not is_owner:
        all_gifts_query = all_gifts_query.filter(is_draft=False)

    # Event gifts are only shown to the owner in a separate section
    event_gifts_by_event: list = []
    if is_owner:
        from itertools import groupby as _groupby

        event_qs = (
            all_gifts_query.filter(event_list__isnull=False, is_draft=False)
            .select_related("event_list")
            .order_by("event_list_id", "created_at")
        )
        for _eid, grp in _groupby(event_qs, key=lambda g: g.event_list_id):
            gift_group = list(grp)
            event_gifts_by_event.append({"event": gift_group[0].event_list, "gifts": gift_group})
        all_gifts_query = all_gifts_query.filter(event_list__isnull=True)
    else:
        all_gifts_query = all_gifts_query.filter(event_list__isnull=True)

    selected_tags = [tag for tag in request.GET.getlist("tags") if tag in GiftTag.Slug.values]
    if selected_tags:
        all_gifts_query = all_gifts_query.filter(tags__slug__in=selected_tags).distinct()

    selected_sort = request.GET.get("sort", "oldest")
    sort_options = {
        "newest": ("-created_at", "-id"),
        "oldest": ("created_at", "id"),
        "title": ("title", "id"),
    }
    if selected_sort not in sort_options:
        selected_sort = "oldest"
    all_gifts_query = all_gifts_query.order_by(*sort_options[selected_sort])

    if from_group_id and not is_owner:
        all_gifts_query = all_gifts_query.filter(
            Q(visible_in__isnull=True) | Q(visible_in__id=from_group_id)
        ).distinct()

    all_gifts = all_gifts_query.prefetch_related("visible_in", "tags")
    if target_user.is_managed:
        mm = getattr(target_user, "managed_member_profile", None)
        user_groups = Group.objects.filter(id=mm.group_id) if mm else Group.objects.none()
    elif not is_owner:
        user_groups = common_groups
    else:
        user_groups = request.user.gift_groups.all()

    gifts: list = []
    draft_gifts: list = []
    shared_gifts: list = []
    surprises: list = []

    if is_owner:
        for g in all_gifts:
            if g.created_by == g.owner:
                item = {"gift": g, "is_reserved": None, "reservation_state": None}
                if g.is_draft:
                    draft_gifts.append(item)
                else:
                    gifts.append(item)
    else:
        all_reservations = Reservation.objects.filter(gift__in=all_gifts).select_related("reserver")
        other_members = []
        if group:
            other_members = group.members.exclude(id__in=[request.user.id, target_user.id])

        for gift in all_gifts:
            gift_reservations = [r for r in all_reservations if r.gift_id == gift.id]
            reservation_group = group or _reservation_group_for_gift(gift, common_groups)
            item_group_id = str(reservation_group.id) if reservation_group else None
            item_other_members = []
            if reservation_group:
                item_other_members = reservation_group.members.exclude(id__in=[request.user.id, target_user.id])
            elif other_members:
                item_other_members = other_members
            item = _gift_item(request, gift, gift_reservations, item_group_id, item_other_members)
            # Managed user gifts are always shown as wishes, never as surprises
            if gift.created_by == gift.owner or target_user.is_managed:
                gifts.append(item)
            else:
                surprises.append(item)

    if group:
        shared_gifts_query = (
            Gift.objects.filter(
                shared_list__deleted_at__isnull=True,
                shared_list__members=target_user,
                shared_publications__group=group,
                shared_publications__published_by=target_user,
                offered=False,
                is_draft=False,
                event_list__isnull=True,
            )
            .select_related("created_by", "shared_list")
            .prefetch_related("tags", "shared_list__members")
            .distinct()
        )
        if selected_tags:
            shared_gifts_query = shared_gifts_query.filter(tags__slug__in=selected_tags).distinct()
        shared_gifts_query = shared_gifts_query.order_by(*sort_options[selected_sort])

        if is_owner:
            shared_gifts = [
                {
                    "gift": gift,
                    "is_shared_wish": True,
                    "is_reserved": None,
                    "reservation_state": None,
                    "comments": [],
                }
                for gift in shared_gifts_query
            ]
        else:
            shared_gift_list = list(shared_gifts_query)
            reservations_by_gift = defaultdict(list)
            for reservation in Reservation.objects.filter(gift__in=shared_gift_list).select_related("reserver"):
                reservations_by_gift[reservation.gift_id].append(reservation)
            for gift in shared_gift_list:
                shared_member_ids = [member.id for member in gift.shared_list.members.all()]
                if request.user.id in shared_member_ids:
                    item = {
                        "gift": gift,
                        "is_reserved": None,
                        "reservation_state": None,
                        "comments": [],
                        "hide_shared_reservation": True,
                    }
                else:
                    excluded_ids = shared_member_ids + [request.user.id]
                    item = _gift_item(
                        request,
                        gift,
                        reservations_by_gift[gift.id],
                        str(group.id),
                        group.members.exclude(id__in=excluded_ids),
                    )
                item["is_shared_wish"] = True
                shared_gifts.append(item)

    subscription = None
    rss_url = None
    if not is_owner:
        subscription = Subscription.objects.filter(subscriber=request.user, owner=target_user).first()
        if subscription and subscription.rss_enabled:
            rss_url = request.build_absolute_uri(reverse("subscription_feed", args=[subscription.feed_token]))

    return render(
        request,
        "gifts/view_list.html",
        {
            "user": target_user,
            "user_being_viewed": target_user,
            "group": group,
            "gifts": gifts,
            "draft_gifts": draft_gifts,
            "shared_gifts": shared_gifts,
            "surprises": surprises,
            "event_gifts_by_event": event_gifts_by_event,
            "is_owner": is_owner,
            "from_group_id": from_group_id,
            "user_groups": user_groups,
            "is_subscribed": subscription is not None,
            "subscription": subscription,
            "rss_url": rss_url,
            "available_tags": GiftTag.objects.all(),
            "selected_tags": selected_tags,
            "selected_sort": selected_sort,
            "has_list_filters": bool(selected_tags) or selected_sort != "oldest",
            "shared_lists_for_move": request.user.shared_lists.filter(deleted_at__isnull=True) if is_owner else [],
        },
    )


@login_required
@require_POST
def toggle_subscription(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    if is_demo_user(request.user) or getattr(target_user, "is_demo", False):
        return HttpResponseForbidden(_("Subscriptions are disabled for demo accounts."))

    if target_user == request.user:
        return HttpResponseForbidden(_("You cannot subscribe to yourself"))

    if not Group.objects.filter(members=request.user).filter(members=target_user).exists():
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    subscription = Subscription.objects.filter(subscriber=request.user, owner=target_user).first()
    delivery = request.POST.get("delivery")
    if request.POST.get("action") == "unsubscribe" or (subscription and not delivery):
        subscription.delete()
        messages.success(request, _subscription_removed_message(target_user.nickname))
    else:
        delivery = _delivery_from_post(request)
        Subscription.objects.update_or_create(
            subscriber=request.user,
            owner=target_user,
            defaults={
                "email_enabled": delivery in {"email", "both"},
                "rss_enabled": delivery in {"rss", "both"},
                "birthday_reminder": request.POST.get("birthday_reminder") == "on",
                "birthday_reminder_days_before": _reminder_days_from_post(request, "birthday_reminder_days_before", 14),
                "christmas_reminder": request.POST.get("christmas_reminder") == "on",
                "christmas_reminder_days_before": _reminder_days_from_post(
                    request, "christmas_reminder_days_before", 30
                ),
            },
        )
        messages.success(request, _("You are now subscribed to %(name)s's list") % {"name": target_user.nickname})

    # Keep the originating group context (notably ?from_group=...) so the
    # group sidebar remains rendered after toggling the subscription.
    return _redirect_to_referer_or(request, "view_list", user_id=user_id)


def _update_notification_subscription_from_post(request, subscription):
    delivery = _delivery_from_post(request)
    subscription.email_enabled = delivery in {"email", "both"}
    subscription.rss_enabled = delivery in {"rss", "both"}
    subscription.birthday_reminder = request.POST.get("birthday_reminder") == "on"
    subscription.birthday_reminder_days_before = _reminder_days_from_post(
        request, "birthday_reminder_days_before", subscription.birthday_reminder_days_before
    )
    subscription.christmas_reminder = request.POST.get("christmas_reminder") == "on"
    subscription.christmas_reminder_days_before = _reminder_days_from_post(
        request, "christmas_reminder_days_before", subscription.christmas_reminder_days_before
    )
    subscription.save(
        update_fields=[
            "email_enabled",
            "rss_enabled",
            "birthday_reminder",
            "birthday_reminder_days_before",
            "christmas_reminder",
            "christmas_reminder_days_before",
        ]
    )


def _handle_notification_subscription_post(request, action):
    subscription = get_object_or_404(
        Subscription.objects.select_related("owner"),
        id=request.POST.get("subscription_id"),
        subscriber=request.user,
    )
    if action == "unsubscribe":
        owner_name = subscription.owner.nickname
        subscription.delete()
        messages.success(request, _subscription_removed_message(owner_name))
        return

    _update_notification_subscription_from_post(request, subscription)
    messages.success(request, _("Notification preferences updated."))


def _handle_notification_digest_post(request, digest_preference):
    frequency = request.POST.get("frequency")
    valid_frequencies = {choice[0] for choice in NotificationDigestPreference.FREQUENCY_CHOICES}
    if frequency not in valid_frequencies:
        frequency = NotificationDigestPreference.FREQUENCY_NONE
    digest_preference.frequency = frequency
    digest_preference.save(update_fields=["frequency", "updated_at"])
    messages.success(request, _("Digest preference updated."))


@login_required
def notification_center(request):
    if is_demo_user(request.user):
        return HttpResponseForbidden(_("Notifications are disabled for demo accounts."))

    digest_preference = NotificationDigestPreference.objects.get_or_create(user=request.user)[0]

    if request.method == "POST":
        action = request.POST.get("action")

        if action in {"update_subscription", "unsubscribe"}:
            _handle_notification_subscription_post(request, action)

        elif action == "update_digest":
            _handle_notification_digest_post(request, digest_preference)

        return redirect("notification_center")

    subscriptions = (
        Subscription.objects.filter(subscriber=request.user)
        .select_related("owner")
        .order_by("owner__nickname", "owner__email")
    )
    user_groups = request.user.gift_groups.prefetch_related("members").all()
    today = timezone.localdate()
    recent_group_gifts = (
        Gift.objects.filter(
            visible_in__members=request.user,
            shared_list__isnull=True,
            offered=False,
            event_list__isnull=True,
            is_draft=False,
        )
        .exclude(owner=request.user)
        .select_related("owner", "created_by")
        .order_by("-created_at")
        .distinct()[:8]
    )
    my_reservations = (
        Reservation.objects.filter(
            reserver=request.user, gift__offered=False, gift__is_draft=False, gift__event_list__isnull=True
        )
        .select_related("gift", "gift__owner", "gift__shared_list", "gift__group_reserved_on")
        .order_by("-created_at")[:8]
    )

    return render(
        request,
        "gifts/notification_center.html",
        {
            "subscriptions": subscriptions,
            "digest_preference": digest_preference,
            "frequency_choices": NotificationDigestPreference.FREQUENCY_CHOICES,
            "reminder_day_choices": Subscription.REMINDER_DAY_CHOICES,
            "upcoming_birthdays": _upcoming_birthdays(request.user, user_groups, today),
            "recent_group_gifts": recent_group_gifts,
            "my_reservations": my_reservations,
            "balance_summaries": _dashboard_balance_summaries(request.user, user_groups),
        },
    )


@require_GET
def unsubscribe_token(request, owner_id, uidb64, token):
    try:
        subscriber_id = force_str(urlsafe_base64_decode(uidb64))
        subscriber = get_object_or_404(User, id=subscriber_id)
        owner = get_object_or_404(User, id=owner_id)
        if subscriber.is_demo or owner.is_demo:
            return redirect_dashboard()

        if not default_token_generator.check_token(subscriber, token):
            messages.error(request, _("The unsubscription link is invalid"))
            return redirect_dashboard()

        if subscriber.subscriptions.filter(id=owner.id).exists():
            subscriber.subscriptions.remove(owner)
            messages.success(request, _subscription_removed_message(owner.nickname))

        return redirect("view_list", user_id=owner.id)
    except (TypeError, ValueError, OverflowError):
        messages.error(request, _("The unsubscription link is invalid"))
        return redirect_dashboard()


@login_required
@require_POST
def add_gift_comment(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id, offered=False, event_list__isnull=True)
    group, err = _get_comment_group(request, gift, request.POST.get("group_id"))
    if err:
        return err

    body = request.POST.get("body", "").strip()
    if not body:
        if _is_ajax(request):
            return HttpResponse(_(COMMENT_EMPTY_MESSAGE), status=400)
        messages.error(request, _(COMMENT_EMPTY_MESSAGE))
        return _redirect_to_referer_or(request, "view_list", user_id=gift.owner_id)

    if len(body) > COMMENT_MAX_LENGTH:
        if _is_ajax(request):
            return HttpResponse(_(COMMENT_TOO_LONG_MESSAGE), status=400)
        messages.error(request, _(COMMENT_TOO_LONG_MESSAGE))
        return _redirect_to_referer_or(request, "view_list", user_id=gift.owner_id)

    GiftComment.objects.create(gift=gift, group=group, author=request.user, body=body)
    if _is_ajax(request):
        return _render_gift_comments_panel(request, gift, group.id)

    messages.success(request, _("Comment added."))
    return _redirect_to_referer_or(request, "view_list", user_id=gift.owner_id)


@login_required
@require_POST
def delete_gift_comment(request, comment_id):
    comment = get_object_or_404(GiftComment.objects.select_related("gift", "group", "author"), id=comment_id)
    _comment_group, err = _get_comment_group(request, comment.gift, comment.group_id)
    if err:
        return err

    if comment.author_id != request.user.id:
        return HttpResponseForbidden(_(PERMISSION_DENIED))

    comment.is_deleted = True
    comment.deleted_by = request.user
    comment.save(update_fields=["is_deleted", "deleted_by", "updated_at"])
    if _is_ajax(request):
        return _render_gift_comments_panel(request, comment.gift, comment.group_id)

    messages.success(request, _("Comment deleted."))
    return _redirect_to_referer_or(request, "view_list", user_id=comment.gift.owner_id)


@login_required
@require_POST
def edit_gift_comment(request, comment_id):
    comment = get_object_or_404(GiftComment.objects.select_related("gift", "group", "author"), id=comment_id)
    _comment_group, err = _get_comment_group(request, comment.gift, comment.group_id)
    if err:
        return err

    if comment.author_id != request.user.id:
        return HttpResponseForbidden(_(PERMISSION_DENIED))

    if comment.is_deleted:
        return HttpResponseForbidden(_("Deleted comments cannot be edited."))

    body = request.POST.get("body", "").strip()
    if not body:
        if _is_ajax(request):
            return HttpResponse(_(COMMENT_EMPTY_MESSAGE), status=400)
        messages.error(request, _(COMMENT_EMPTY_MESSAGE))
        return _redirect_to_referer_or(request, "view_list", user_id=comment.gift.owner_id)

    if len(body) > COMMENT_MAX_LENGTH:
        if _is_ajax(request):
            return HttpResponse(_(COMMENT_TOO_LONG_MESSAGE), status=400)
        messages.error(request, _(COMMENT_TOO_LONG_MESSAGE))
        return _redirect_to_referer_or(request, "view_list", user_id=comment.gift.owner_id)

    comment.body = body
    comment.edited_at = timezone.now()
    comment.save(update_fields=["body", "edited_at", "updated_at"])
    if _is_ajax(request):
        return _render_gift_comments_panel(request, comment.gift, comment.group_id)

    messages.success(request, _("Comment updated."))
    return _redirect_to_referer_or(request, "view_list", user_id=comment.gift.owner_id)


@login_required
@require_POST
def add_gift(request, owner_id):
    owner = get_object_or_404(User, id=owner_id)

    if not has_same_demo_scope(request.user, owner):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    if owner != request.user and not Group.objects.filter(members=request.user).filter(members=owner).exists():
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    selected_tags = _gift_tags_from_post(request)
    if selected_tags is None:
        return HttpResponseBadRequest(INVALID_TAG_SELECTION_MESSAGE)

    group_ids = request.POST.getlist("visible_in")
    valid_groups = Group.objects.none()
    if group_ids:
        try:
            requested_group_ids = {int(group_id) for group_id in group_ids}
        except (TypeError, ValueError):
            return HttpResponseForbidden(_(PERMISSION_DENIED))

        allowed_groups = Group.objects.filter(members=request.user, is_demo=request.user.is_demo)
        if owner != request.user:
            allowed_groups = allowed_groups.filter(members=owner)
        valid_groups = allowed_groups.filter(id__in=requested_group_ids)
        if valid_groups.count() != len(requested_group_ids):
            return HttpResponseForbidden(_(PERMISSION_DENIED))

    title = request.POST.get("title", "").strip()
    if title:
        is_draft = owner == request.user and request.POST.get("is_draft") == "1"
        gift = Gift.objects.create(
            owner=owner,
            title=title,
            description=request.POST.get("description", "").strip(),
            url=request.POST.get("url", "").strip(),
            created_by=request.user,
            is_draft=is_draft,
        )
        if group_ids and not is_draft:
            gift.visible_in.set(valid_groups)
        gift.tags.set(selected_tags)

        subscription_records = owner.subscriber_records.filter(
            email_enabled=True,
            subscriber__is_demo=False,
        ).select_related("subscriber")
        if not is_draft and not owner.is_demo and subscription_records.exists():
            protocol = "https" if request.is_secure() else "http"
            domain = get_current_site(request).domain
            list_url = f"{protocol}://{domain}{reverse('view_list', args=[owner.id])}"

            for subscription in subscription_records:
                subscriber = subscription.subscriber
                if subscriber == request.user:
                    continue
                if Group.objects.filter(members=owner).filter(members=subscriber).exists():
                    gift_groups = gift.visible_in.all()
                    if gift_groups.exists() and not gift_groups.filter(members=subscriber).exists():
                        continue

                    uid = urlsafe_base64_encode(force_bytes(subscriber.pk))
                    token = default_token_generator.make_token(subscriber)
                    unsubscribe_url = (
                        f"{protocol}://{domain}{reverse('unsubscribe_token', args=[owner.pk, uid, token])}"
                    )

                    context = {
                        "subscriber": subscriber,
                        "owner": owner,
                        "gift": gift,
                        "list_url": list_url,
                        "unsubscribe_url": unsubscribe_url,
                    }
                    subject = _("New gift on %(name)s's list!") % {"name": owner.nickname}
                    html_message = render_to_string("emails/gift_added_notification.html", context)
                    plain_message = render_to_string("emails/gift_added_notification.txt", context)
                    send_mail(
                        subject,
                        plain_message,
                        settings.DEFAULT_FROM_EMAIL,
                        [subscriber.email],
                        html_message=html_message,
                    )

    return _redirect_to_referer_or(request, "view_list", user_id=owner.id)


@login_required
@require_POST
def delete_gift(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id)
    if not has_same_demo_scope(request.user, gift.owner):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    # Managed member gifts: any group member can delete
    if gift.owner.is_managed:
        mm = getattr(gift.owner, "managed_member_profile", None)
        if not mm or request.user not in mm.group.members.all():
            return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
        owner_id = gift.owner.id
        gift.delete()
        return _redirect_to_referer_or(request, "view_list", user_id=owner_id)
    if gift.created_by != request.user:
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    owner_id = gift.owner.id
    gift.delete()
    return _redirect_to_referer_or(request, "view_list", user_id=owner_id)


@login_required
@require_POST
def edit_gift(request: HttpRequest, gift_id: int):
    gift = get_object_or_404(Gift, id=gift_id)
    if not has_same_demo_scope(request.user, gift.owner):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    if gift.owner.is_managed:
        mm = getattr(gift.owner, "managed_member_profile", None)
        if not mm or request.user not in mm.group.members.all():
            return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    elif gift.created_by != request.user:
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    selected_tags = _gift_tags_from_post(request)
    if selected_tags is None:
        return HttpResponseBadRequest(INVALID_TAG_SELECTION_MESSAGE)

    title = request.POST.get("title", "").strip()
    if title:
        is_draft = (
            gift.owner == request.user
            and not gift.offered
            and gift.event_list_id is None
            and request.POST.get("is_draft") == "1"
        )
        gift.title = title
        gift.description = request.POST.get("description", "").strip()
        gift.url = request.POST.get("url", "").strip()
        gift.is_draft = is_draft
        if is_draft:
            gift.group_reserved_on = None
        gift.save()
        gift.tags.set(selected_tags)

        group_ids = request.POST.getlist("visible_in")
        if group_ids and not is_draft:
            valid_groups = Group.objects.filter(id__in=group_ids, members=request.user, is_demo=request.user.is_demo)
            gift.visible_in.set(valid_groups)
        else:
            gift.visible_in.clear()
        if is_draft:
            Reservation.objects.filter(gift=gift).delete()

    return _redirect_to_referer_or(request, "view_list", user_id=gift.owner.id)


@login_required
@require_POST
def edit_gift_price(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id)
    if not has_same_demo_scope(request.user, gift.owner):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    price_raw = request.POST.get("price", "").strip().replace(",", ".")

    try:
        if price_raw:
            gift.price = Decimal(price_raw)
        else:
            gift.price = None
        gift.save()
        messages.success(request, _("Estimated price updated!"))
    except (InvalidOperation, ValueError):
        messages.error(request, _("Invalid price format."))

    return redirect("view_list", user_id=gift.owner.id)


def _validate_reservation_participants(request, gift, user, group):
    recipient = gift.shared_list if gift.shared_list_id else gift.owner
    if not has_same_demo_scope(request.user, recipient) or not has_same_demo_scope(request.user, user):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    if gift.owner == request.user and not gift.shared_list_id:
        return HttpResponseForbidden("Impossible on your own list")
    if gift.shared_list_id:
        if gift.shared_list.members.filter(id__in=[request.user.id, user.id]).exists():
            return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    elif not Group.objects.filter(members=request.user).filter(members=gift.owner).exists():
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    if not group.members.filter(id=user.id).exists():
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    if user == gift.owner and not gift.shared_list_id:
        return JsonResponse({"success": False, "error": _("The gift owner cannot reserve this gift")}, status=400)
    if Reservation.objects.filter(gift=gift, reserver=user).exists():
        return JsonResponse({"success": False, "error": "This person already joined this gift"}, status=409)
    if Reservation.objects.filter(gift=gift, exclusivity=True).exists():
        return JsonResponse({"success": False, "error": "Someone else reserved exclusively this gift"}, status=409)
    return None


@login_required
def reserve_gift(request: HttpRequest, gift_id: int):
    if request.method != "POST":
        return JsonResponse({"error": METHOD_NOT_AUTHORIZED_MESSAGE.format(request.method)}, status=405)

    data, err = _parse_json_body(request)
    if err:
        return err

    exclusivity = data.get("exclusivity")
    user_id = data.get("user_id")
    gift = get_object_or_404(Gift, id=gift_id)
    user = get_object_or_404(User, id=user_id)

    group_id = data.get("group_id")
    group, err = _get_reservation_group(request, gift, group_id)
    if err:
        return err
    validation_error = _validate_reservation_participants(request, gift, user, group)
    if validation_error:
        return validation_error

    Reservation.objects.create(gift=gift, reserver=user, exclusivity=exclusivity)

    gift.group_reserved_on = group
    gift.save()

    reservations = Reservation.objects.filter(gift=gift).select_related("reserver").order_by("id")
    participant_ids = {r.reserver.id for r in reservations}
    return _render_reservation_modal(request, gift, group_id, reservations, extra_exclude_ids=participant_ids)


@login_required
def modify_reservation(request: HttpRequest, gift_id: int):
    if request.method != "POST":
        return JsonResponse({"error": METHOD_NOT_AUTHORIZED_MESSAGE.format(request.method)}, status=405)

    data, err = _parse_json_body(request)
    if err:
        return err

    gift = get_object_or_404(Gift, id=gift_id)
    recipient = gift.shared_list if gift.shared_list_id else gift.owner
    if not has_same_demo_scope(request.user, recipient):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    group_id = data.get("group_id")
    group, err = _get_reservation_group(request, gift, group_id)
    if err:
        return err

    try:
        reservation_user_id_to_modify: User = data.get("reservation_user_id_to_modify")
        reservation_user_to_modify = User.objects.get(id=reservation_user_id_to_modify)
        reservation = get_object_or_404(Reservation, gift=gift, reserver=reservation_user_to_modify)
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Unable to find user or reservation"}, status=400)

    if reservation.exclusivity:
        reservation.exclusivity = False
        reservation.save()
    else:
        Reservation.objects.filter(gift=gift).exclude(reserver=reservation_user_to_modify).delete()
        reservation.exclusivity = True
        reservation.save()

    reservations = Reservation.objects.filter(gift=gift).select_related("reserver").order_by("id")
    if not reservations:
        gift.group_reserved_on = None
        gift.save()
    elif gift.group_reserved_on_id is None:
        gift.group_reserved_on = group
        gift.save()
    return _render_reservation_modal(request, gift, group_id, reservations)


@login_required
@require_POST
def delete_reservation(request, gift_id):
    if request.method != "POST":
        return JsonResponse({"error": METHOD_NOT_AUTHORIZED_MESSAGE.format(request.method)}, status=405)

    data, err = _parse_json_body(request)
    if err:
        return err

    gift = get_object_or_404(Gift, id=gift_id)
    recipient = gift.shared_list if gift.shared_list_id else gift.owner
    if not has_same_demo_scope(request.user, recipient):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    group_id = data.get("group_id")
    group, err = _get_reservation_group(request, gift, group_id)
    if err:
        return err

    try:
        reservation_user_id_to_delete: User = data.get("reservation_user_id_to_delete")
        reservation_user_to_delete = User.objects.get(id=reservation_user_id_to_delete)
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Unable to find user or reservation"}, status=400)

    Reservation.objects.filter(gift=gift, reserver=reservation_user_to_delete).delete()

    reservations = Reservation.objects.filter(gift=gift).select_related("reserver").order_by("id")
    if reservations:
        if gift.group_reserved_on_id is None:
            gift.group_reserved_on = group
            gift.save()
    else:
        gift.group_reserved_on = None
        gift.save()
    return _render_reservation_modal(request, gift, group_id, reservations)


@login_required
@require_POST
def offer_gift(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id)
    if gift.is_draft:
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    if not has_same_demo_scope(request.user, gift.owner):
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))

    if gift.owner == request.user:
        return HttpResponseForbidden(_("You cannot mark your own gift as offered"))

    data, err = _parse_json_body(request)
    if err:
        return err

    reservations = list(Reservation.objects.filter(gift=gift).select_related("reserver").order_by("id"))
    group_id = data.get("group_id")
    confirm = data.get("confirm", False)

    offer_group, err = _resolve_offer_group(gift, group_id)
    if err:
        return err
    history_disabled = offer_group is not None and not offer_group.show_history

    if not confirm:
        context = {
            **_build_amounts_modal_context(gift, offer_group, reservations),
            "group_id": group_id,
            "history_disabled": history_disabled,
        }
        return render(request, OFFER_MODAL_CONTENT_PATH, context)

    if history_disabled:
        gift.delete()
        return JsonResponse({"success": True})

    err = _set_actual_cost(gift, data.get("actual_cost", ""))
    if err:
        return err

    err = _apply_payers(gift, data.get("payers", {}))
    if err:
        return err

    split_ids = data.get("split_participants", [])
    if split_ids and offer_group:
        gift.expense_split.set(User.objects.filter(id__in=split_ids, gift_groups=offer_group).exclude(id=gift.owner_id))

    res_count = Reservation.objects.filter(gift=gift).count()
    Reservation.objects.filter(gift=gift).update(exclusivity=(res_count == 1))

    err = _set_offer_group(gift, group_id)
    if err:
        return err

    _mark_gift_offered(gift)

    return JsonResponse({"success": True})


@login_required
@require_GET
def history_view(request, group_id=None):
    if not group_id:
        return redirect("dashboard")

    group = get_object_or_404(Group, id=group_id)

    if not has_same_demo_scope(request.user, group):
        return demo_scope_forbidden_response()

    if not group.members.filter(id=request.user.id).exists():
        return render(
            request, "groups/history_access_denied.html", {"group": group, "reason": "not_member"}, status=403
        )

    if not group.show_history:
        return render(
            request,
            "gifts/history.html",
            {
                "group": group,
                "viewing_history": True,
                "history_disabled": True,
                "gifts": [],
                "user_reserved_ids": set(),
            },
        )

    gifts = list(
        Gift.objects.filter(group_reserved_on=group, offered=True)
        .select_related("owner")
        .prefetch_related("reservation__reserver")
        .order_by("-created_at")
    )
    user_reserved_ids = set(
        Reservation.objects.filter(gift__in=gifts, reserver=request.user).values_list("gift_id", flat=True)
    )

    return render(
        request,
        "gifts/history.html",
        {
            "group": group,
            "viewing_history": True,
            "gifts": gifts,
            "user_reserved_ids": user_reserved_ids,
        },
    )


@login_required
@require_POST
def un_offer_gift(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id, offered=True)
    if not has_same_demo_scope(request.user, gift.owner):
        return HttpResponseForbidden(_(PERMISSION_DENIED))

    group = gift.group_reserved_on

    is_owner = gift.owner == request.user
    is_reserver = Reservation.objects.filter(gift=gift, reserver=request.user).exists()
    is_group_member = group is not None and group.members.filter(id=request.user.id).exists()

    if not (is_owner or is_reserver or is_group_member):
        return HttpResponseForbidden(_(PERMISSION_DENIED))

    Reservation.objects.filter(gift=gift).update(amount_paid=None)
    gift.expense_split.clear()
    gift.actual_cost = None
    gift.offered = False
    gift.offered_at = None
    gift.save()

    messages.success(request, _("Gift put back in the list."))
    if group:
        return redirect("history_group", group_id=group.id)
    return redirect("dashboard")


@login_required
@require_POST
def delete_offered_gift(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id, offered=True)

    err = _check_gift_access(request, gift)
    if err:
        return err

    group = gift.group_reserved_on
    gift.delete()
    messages.success(request, _("Gift deleted."))
    if group:
        return redirect("history_group", group_id=group.id)
    return redirect("dashboard")


@login_required
@require_POST
def edit_offered_amounts(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id, offered=True)

    err = _check_gift_access(request, gift)
    if err:
        return err

    data, err = _parse_json_body(request)
    if err:
        return err

    reservations = list(Reservation.objects.filter(gift=gift).select_related("reserver").order_by("id"))
    offer_group = gift.group_reserved_on

    if not data.get("save", False):
        return render(request, EDIT_AMOUNTS_PATH, _build_amounts_modal_context(gift, offer_group, reservations))

    actual_cost_str = data.get("actual_cost", "")
    if actual_cost_str:
        try:
            gift.actual_cost = Decimal(str(actual_cost_str).replace(",", "."))
        except (InvalidOperation, ValueError):
            return JsonResponse({"success": False, "error": INVALID_AMOUNT_FORMAT}, status=400)
    else:
        gift.actual_cost = None

    err = _apply_payers(gift, data.get("payers", {}))
    if err:
        return err

    split_ids = data.get("split_participants", [])
    if split_ids and offer_group:
        gift.expense_split.set(User.objects.filter(id__in=split_ids, gift_groups=offer_group).exclude(id=gift.owner_id))
    elif not split_ids:
        gift.expense_split.clear()

    gift.save()
    return JsonResponse({"success": True})


@login_required
@require_POST
def mark_received(request, gift_id):
    gift = get_object_or_404(Gift, id=gift_id)
    if gift.is_draft:
        return HttpResponseForbidden(_(ACCESS_REFUSED_MSG))
    if not has_same_demo_scope(request.user, gift.owner):
        return HttpResponseForbidden(_("Only the gift owner can mark it as received"))

    if gift.owner != request.user:
        return HttpResponseForbidden(_("Only the gift owner can mark it as received"))

    data, err = _parse_json_body(request)
    if err:
        return err

    confirm = data.get("confirm", False)
    group_id = data.get("group_id")
    reservations = list(Reservation.objects.filter(gift=gift).select_related("reserver").order_by("id"))

    receive_group, err = _resolve_receive_group(gift, group_id)
    if err:
        return err
    history_disabled = receive_group is not None and not receive_group.show_history

    if not confirm:
        all_groups = list(request.user.gift_groups.all())
        context = {
            "gift": gift,
            "reservations": reservations,
            "group_id": group_id,
            "history_disabled": history_disabled,
            "gift_groups": all_groups,
            "current_group_id": receive_group.id if receive_group else None,
        }
        return render(request, "gifts/includes/_mark_received_content.html", context)

    if group_id == "none":
        gift.group_reserved_on = None
        _mark_gift_offered(gift)
        return JsonResponse({"success": True})

    if history_disabled:
        gift.delete()
        return JsonResponse({"success": True})

    if group_id:
        try:
            gift.group_reserved_on = Group.objects.get(id=int(group_id), members=request.user)
        except (Group.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"success": False, "error": _("GROUP_NOT_FOUND")}, status=400)

    _mark_gift_offered(gift)

    return JsonResponse({"success": True})


def _calculate_member_balances(group):
    balances = defaultdict(Decimal)
    gifts = (
        Gift.objects.filter(group_reserved_on=group, offered=True, actual_cost__isnull=False)
        .exclude(actual_cost=0)
        .prefetch_related("expense_split", "reservation__reserver")
    )

    for gift in gifts:
        split_users = list(gift.expense_split.all())
        if not split_users:
            continue
        share = gift.actual_cost / len(split_users)
        for u in split_users:
            balances[u.id] -= share
        for res in gift.reservation.all():
            if res.amount_paid:
                balances[res.reserver_id] += res.amount_paid

    for s in BalanceSettlement.objects.filter(group=group):
        balances[s.payer_id] += s.amount
        balances[s.payee_id] -= s.amount
    return balances


def _build_balance_transactions(balances, members):

    pos = [(-v, k) for k, v in balances.items() if v > Decimal("0.01")]
    neg = [(v, k) for k, v in balances.items() if v < Decimal("-0.01")]
    heapq.heapify(pos)
    heapq.heapify(neg)

    transactions = []
    while pos and neg:
        credit, cid = heapq.heappop(pos)
        credit = -credit
        debt, did = heapq.heappop(neg)
        amount = min(credit, -debt)  # both credit and -debt are positive
        transactions.append((members.get(did), members.get(cid), amount.quantize(Decimal("0.01"))))
        if credit - amount > Decimal("0.01"):
            heapq.heappush(pos, (-(credit - amount), cid))
        if -debt - amount > Decimal("0.01"):
            heapq.heappush(neg, (debt + amount, did))

    # Unmatched debtors: expense_split was recorded but payer wasn't → show with no creditor
    while neg:
        debt, did = heapq.heappop(neg)
        transactions.append((members.get(did), None, (-debt).quantize(Decimal("0.01"))))

    # Unmatched creditors: payer recorded but no split → show with no debtor
    while pos:
        credit, cid = heapq.heappop(pos)
        transactions.append((None, members.get(cid), credit.quantize(Decimal("0.01"))))
    return transactions


def compute_group_balances(group):
    members = {member.id: member for member in group.members.all()}
    balances = _calculate_member_balances(group)
    transactions = _build_balance_transactions(balances, members)

    return (
        {members[uid]: bal.quantize(Decimal("0.01")) for uid, bal in balances.items() if uid in members},
        transactions,
        list(members.values()),
    )


@login_required
@require_GET
def balance_view(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if not has_same_demo_scope(request.user, group):
        return demo_scope_forbidden_response()

    if not group.members.filter(id=request.user.id).exists():
        return render(
            request, "groups/history_access_denied.html", {"group": group, "reason": "not_member"}, status=403
        )
    ctx = {"group": group, "viewing_balance": True}
    if not group.show_balance:
        return render(request, "gifts/balance.html", {**ctx, "balance_disabled": True})
    balances, transactions, members = compute_group_balances(group)
    full_balances = {m: balances.get(m, Decimal("0.00")) for m in members}
    other_members = [m for m in members if m.id != request.user.id]
    all_members = list(members)
    settlements = BalanceSettlement.objects.filter(group=group).select_related("payer", "payee").order_by("-created_at")
    gift_history = (
        Gift.objects.filter(group_reserved_on=group, offered=True)
        .select_related("owner")
        .prefetch_related("reservation__reserver", "expense_split")
        .order_by("-offered_at")
    )
    my_balance = balances.get(request.user, Decimal(0))
    return render(
        request,
        "gifts/balance.html",
        {
            **ctx,
            "balances": full_balances,
            "transactions": transactions,
            "other_members": other_members,
            "all_members": all_members,
            "settlements": settlements,
            "gift_history": gift_history,
            "my_balance": my_balance,
        },
    )


@login_required
@require_POST
def add_settlement(request, group_id):
    group = get_object_or_404(Group, id=group_id)
    if not has_same_demo_scope(request.user, group):
        return demo_scope_forbidden_response()

    if not group.members.filter(id=request.user.id).exists():
        return HttpResponseForbidden(_(PERMISSION_DENIED))
    try:
        payee = group.members.get(id=int(request.POST.get("payee_id", "")))
        payer_id = request.POST.get("payer_id")
        payer = group.members.get(id=int(payer_id)) if payer_id else request.user
        if payer == payee:
            raise ValueError("payer == payee")
        amount = Decimal(request.POST.get("amount", "").replace(",", "."))
        if amount <= 0:
            raise ValueError
    except Exception:
        messages.error(request, _("Invalid settlement data."))
        return redirect("balance_group", group_id=group_id)
    BalanceSettlement.objects.create(group=group, payer=payer, payee=payee, amount=amount)
    messages.success(request, _("Settlement recorded."))
    return redirect("balance_group", group_id=group_id)
