from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.utils.translation import gettext as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RangeDateFilter

from .models import (
    BalanceSettlement,
    EventList,
    Gift,
    GiftComment,
    Group,
    GuestReservation,
    ManagedMember,
    NotificationDigestPreference,
    Reservation,
    SharedGiftPublication,
    SharedList,
    SharedListMembership,
    Subscription,
    User,
)


class ReservationInline(TabularInline):
    model = Reservation
    extra = 0
    fields = ("reserver", "exclusivity", "amount_paid", "created_at")
    readonly_fields = ("created_at",)


class GuestReservationInline(TabularInline):
    model = GuestReservation
    extra = 0
    fields = ("reserver_user", "reserver_name", "exclusivity", "created_at")
    readonly_fields = ("created_at",)


class GiftCommentInline(TabularInline):
    model = GiftComment
    extra = 0
    fields = ("author", "group", "body", "is_deleted", "deleted_by", "created_at", "edited_at")
    readonly_fields = ("created_at", "edited_at")


class ManagedMemberInline(TabularInline):
    model = ManagedMember
    extra = 0
    fields = ("name", "color", "user")


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    list_display = (
        "email",
        "nickname",
        "birthday",
        "date_joined",
        "is_staff",
        "is_active",
        "is_verified",
        "is_managed",
        "avatar_preview",
    )
    list_filter = ("is_staff", "is_active", "is_verified", "is_managed")
    search_fields = ("email", "nickname")
    ordering = ("email",)
    list_editable = ("is_verified",)

    fieldsets = BaseUserAdmin.fieldsets + (
        (
            _("More information"),
            {
                "fields": (
                    "nickname",
                    ("birthday_month", "birthday_day"),
                    "is_verified",
                    "is_managed",
                    "last_seen_version",
                    "avatar",
                    "avatar_preset",
                )
            },
        ),
    )
    filter_horizontal = ("groups", "user_permissions")

    @admin.display(description=_("Avatar"))
    def avatar_preview(self, obj):
        if obj.display_avatar_url:
            return format_html(
                '<img src="{}" style="width:32px;height:32px;border-radius:50%;object-fit:cover;">',
                obj.display_avatar_url,
            )
        return "—"


@admin.register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    list_display = (
        "subscriber",
        "owner",
        "email_enabled",
        "rss_enabled",
        "birthday_reminder",
        "birthday_reminder_days_before",
        "christmas_reminder",
        "christmas_reminder_days_before",
        "created_at",
    )
    list_filter = ("email_enabled", "rss_enabled", "birthday_reminder", "christmas_reminder")
    search_fields = ("subscriber__email", "subscriber__nickname", "owner__email", "owner__nickname")
    readonly_fields = ("feed_token", "created_at")


@admin.register(NotificationDigestPreference)
class NotificationDigestPreferenceAdmin(ModelAdmin):
    list_display = ("user", "frequency", "last_sent_at", "updated_at")
    list_filter = ("frequency",)
    search_fields = ("user__email", "user__nickname")
    readonly_fields = ("updated_at",)


@admin.register(Group)
class GroupAdmin(ModelAdmin):
    list_display = ("name", "group_token", "created_by", "created_at", "member_count", "show_history", "show_balance")
    list_filter = ("show_history", "show_balance")
    search_fields = ("name", "description", "group_token")
    readonly_fields = ("group_token", "created_at")
    filter_horizontal = ("members",)
    inlines = [ManagedMemberInline]

    fieldsets = (
        (None, {"fields": ("name", "description", "image", "image_preset", "group_token")}),
        (_("Members"), {"fields": ("created_by", "members")}),
        (_("Options"), {"fields": ("show_history", "show_balance")}),
        (_("Dates"), {"fields": ("created_at",)}),
    )

    @admin.display(description=_("Members"))
    def member_count(self, obj):
        return obj.members.count()


@admin.register(Gift)
class GiftAdmin(ModelAdmin):
    list_display = (
        "title",
        "owner",
        "shared_list",
        "created_by",
        "price",
        "offered",
        "is_draft",
        "is_hidden",
        "created_at",
    )
    list_filter = ("offered", "is_draft", "is_hidden", ("created_at", RangeDateFilter))
    search_fields = ("title", "description", "owner__nickname", "owner__email")
    readonly_fields = ("created_at", "offered_at")
    date_hierarchy = "created_at"
    filter_horizontal = ("visible_in", "tags", "expense_split")
    inlines = [ReservationInline, GuestReservationInline, GiftCommentInline]

    fieldsets = (
        (None, {"fields": ("title", "description", "url", "price")}),
        (_("Ownership"), {"fields": ("owner", "shared_list", "created_by", "managed_member")}),
        (_("Visibility"), {"fields": ("visible_in", "tags", "is_draft", "is_hidden")}),
        (_("Event"), {"fields": ("event_list",)}),
        (_("Reservation"), {"fields": ("group_reserved_on",)}),
        (_("Status"), {"fields": ("offered", "offered_at", "actual_cost", "expense_split")}),
        (_("Dates"), {"fields": ("created_at",)}),
    )


@admin.register(Reservation)
class ReservationAdmin(ModelAdmin):
    list_display = ("gift", "reserver", "exclusivity", "amount_paid", "created_at")
    list_filter = ("exclusivity", ("created_at", RangeDateFilter))
    search_fields = ("gift__title", "reserver__nickname", "reserver__email")
    readonly_fields = ("created_at",)


class SharedListMembershipInline(TabularInline):
    model = SharedListMembership
    extra = 0


@admin.register(SharedGiftPublication)
class SharedGiftPublicationAdmin(ModelAdmin):
    list_display = ("gift", "group", "published_by", "created_at")
    list_filter = ("group", ("created_at", RangeDateFilter))
    search_fields = ("gift__title", "group__name", "published_by__nickname", "published_by__email")
    readonly_fields = ("created_at",)


@admin.register(SharedList)
class SharedListAdmin(ModelAdmin):
    list_display = ("name", "member_count", "deleted_at", "is_demo", "created_at")
    list_filter = ("is_demo", ("created_at", RangeDateFilter))
    search_fields = ("name", "members__nickname", "members__email")
    readonly_fields = ("restore_token", "created_at")
    inlines = (SharedListMembershipInline,)

    @admin.display(description=_("Members"))
    def member_count(self, obj):
        return obj.members.count()


@admin.register(GiftComment)
class GiftCommentAdmin(ModelAdmin):
    list_display = ("gift", "author", "group", "is_deleted", "created_at", "edited_at")
    list_filter = ("is_deleted", "group", ("created_at", RangeDateFilter))
    search_fields = ("body", "gift__title", "author__nickname", "author__email", "group__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BalanceSettlement)
class BalanceSettlementAdmin(ModelAdmin):
    list_display = ("group", "payer", "payee", "amount", "created_at")
    list_filter = ("group", ("created_at", RangeDateFilter))
    search_fields = ("payer__nickname", "payee__nickname", "group__name")
    readonly_fields = ("created_at",)


@admin.register(ManagedMember)
class ManagedMemberAdmin(ModelAdmin):
    list_display = ("name", "group", "color_preview", "user", "created_at")
    list_filter = ("group",)
    search_fields = ("name", "group__name")
    readonly_fields = ("created_at",)

    @admin.display(description=_("Color"))
    def color_preview(self, obj):
        return format_html(
            '<span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{};"></span> {}',
            obj.color,
            obj.color,
        )


@admin.register(EventList)
class EventListAdmin(ModelAdmin):
    list_display = ("name", "owner", "event_date", "access_token", "participant_count", "created_at")
    list_filter = (("event_date", RangeDateFilter),)
    search_fields = ("name", "description", "owner__nickname", "owner__email")
    readonly_fields = ("access_token", "created_at")
    filter_horizontal = ("participants",)

    fieldsets = (
        (None, {"fields": ("name", "description", "image", "event_date", "access_token")}),
        (_("Owner & participants"), {"fields": ("owner", "participants")}),
        (_("Dates"), {"fields": ("created_at",)}),
    )

    @admin.display(description=_("Participants"))
    def participant_count(self, obj):
        return obj.participants.count()


@admin.register(GuestReservation)
class GuestReservationAdmin(ModelAdmin):
    list_display = ("gift", "reserver_name", "reserver_user", "exclusivity", "created_at")
    list_filter = ("exclusivity", ("created_at", RangeDateFilter))
    search_fields = ("reserver_name", "reserver_user__nickname", "gift__title")
    readonly_fields = ("created_at", "session_key")
