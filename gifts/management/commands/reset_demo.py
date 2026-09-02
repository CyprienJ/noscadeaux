from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from gifts.demo import DEMO_EMAIL, demo_reset_due
from gifts.models import (
    EventList,
    Gift,
    Group,
    Reservation,
    SecretSantaExclusion,
    SharedGiftPublication,
    SharedList,
    SharedListMembership,
    User,
)
from gifts.onboarding import CURRENT_ONBOARDING_VERSION


class Command(BaseCommand):
    help = "Reset the public demo account and its isolated sample data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lazy",
            action="store_true",
            help="Only reset when the current demo account is older than the configured interval.",
        )

    def handle(self, *args, **options):
        demo_user = User.objects.filter(email=DEMO_EMAIL, is_demo=True).first()
        if options["lazy"] and not demo_reset_due(demo_user):
            self.stdout.write(self.style.SUCCESS("Demo data is still fresh."))
            return

        with transaction.atomic():
            self._delete_demo_data()
            demo_user = self._create_demo_data()

        self.stdout.write(self.style.SUCCESS(f"Demo account reset: {demo_user.email}"))

    def _delete_demo_data(self):
        EventList.objects.filter(is_demo=True).delete()
        SharedList.objects.filter(is_demo=True).delete()
        Group.objects.filter(is_demo=True).delete()
        User.objects.filter(is_demo=True).delete()

    def _create_user(self, email, nickname, *, active=False):
        user = User.objects.create(
            email=email,
            username=email,
            nickname=nickname,
            is_demo=True,
            is_verified=True,
            is_active=active,
            onboarding_version=CURRENT_ONBOARDING_VERSION,
            onboarding_completed_at=timezone.now(),
            profile_completed_at=timezone.now(),
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
        return user

    def _create_demo_data(self):
        demo = self._create_user(DEMO_EMAIL, "Camille", active=True)
        lea = self._create_user("lea.demo@noscadeaux.internal", "Lea")
        sam = self._create_user("sam.demo@noscadeaux.internal", "Sam")
        nina = self._create_user("nina.demo@noscadeaux.internal", "Nina")
        alex = self._create_user("alex.demo@noscadeaux.internal", "Alex")

        family = Group.objects.create(
            name="Famille Martin",
            created_by=demo,
            description="Un groupe fictif pour tester les listes, reservations et depenses partagees.",
            show_history=True,
            show_balance=True,
            is_demo=True,
        )
        family.members.add(demo, lea, sam, nina)

        friends = Group.objects.create(
            name="Anniversaire de bureau",
            created_by=demo,
            description="Un deuxieme cercle de demonstration avec des collegues fictifs.",
            show_history=True,
            show_balance=True,
            is_demo=True,
        )
        friends.members.add(demo, sam, alex)

        shared_list = SharedList.objects.create(name="Maison & week-ends", is_demo=True)
        SharedListMembership.objects.bulk_create(
            [
                SharedListMembership(shared_list=shared_list, user=demo),
                SharedListMembership(shared_list=shared_list, user=lea),
                SharedListMembership(shared_list=shared_list, user=sam),
            ]
        )
        raclette = Gift.objects.create(
            owner=demo,
            shared_list=shared_list,
            created_by=demo,
            title="Appareil à raclette",
            description="Pour les repas de famille et les week-ends entre amis.",
            price=Decimal("69.90"),
        )
        speaker = Gift.objects.create(
            owner=sam,
            shared_list=shared_list,
            created_by=sam,
            title="Enceinte portable",
            description="Une enceinte résistante pour les sorties et les vacances.",
            price=Decimal("59.00"),
        )
        SharedGiftPublication.objects.bulk_create(
            [
                SharedGiftPublication(gift=raclette, group=family, published_by=demo),
                SharedGiftPublication(gift=raclette, group=friends, published_by=sam),
                SharedGiftPublication(gift=speaker, group=family, published_by=demo),
                SharedGiftPublication(gift=speaker, group=family, published_by=lea),
                SharedGiftPublication(gift=speaker, group=friends, published_by=sam),
            ]
        )

        camera = Gift.objects.create(
            owner=demo,
            created_by=demo,
            title="Appareil photo instantane",
            description="Pour garder des souvenirs des fetes de famille.",
            url="https://example.com/appareil-photo",
            price=Decimal("89.90"),
        )
        camera.visible_in.add(family)

        Gift.objects.create(
            owner=demo,
            created_by=demo,
            title="Cours de cuisine italienne",
            description="Un atelier a faire a deux.",
            price=Decimal("120.00"),
        ).visible_in.add(family, friends)

        backpack = Gift.objects.create(
            owner=demo,
            created_by=demo,
            title="Sac a dos de voyage",
            description="Format cabine, avec poche ordinateur.",
            url="https://example.com/sac-voyage",
            price=Decimal("74.50"),
        )
        backpack.visible_in.add(friends)

        surprise = Gift.objects.create(
            owner=demo,
            created_by=lea,
            title="Album photo surprise",
            description="Une idee ajoutee par Lea, invisible pour Camille dans sa propre liste.",
            price=Decimal("35.00"),
        )
        surprise.visible_in.add(family)
        Reservation.objects.create(gift=surprise, reserver=sam, amount_paid=Decimal("20.00"))
        surprise.group_reserved_on = family
        surprise.actual_cost = Decimal("35.00")
        surprise.offered = True
        surprise.offered_at = timezone.now()
        surprise.save()
        surprise.expense_split.add(lea, sam)

        headphones = Gift.objects.create(
            owner=lea,
            created_by=lea,
            title="Casque audio sans fil",
            description="Modele confortable pour le train.",
            price=Decimal("149.00"),
        )
        headphones.visible_in.add(family)
        Reservation.objects.create(gift=headphones, reserver=demo, exclusivity=True)
        headphones.group_reserved_on = family
        headphones.save()

        board_game = Gift.objects.create(
            owner=sam,
            created_by=sam,
            title="Jeu de societe cooperatif",
            description="Pour les soirees du vendredi.",
            price=Decimal("42.00"),
        )
        board_game.visible_in.add(family, friends)
        Reservation.objects.create(gift=board_game, reserver=demo)
        Reservation.objects.create(gift=board_game, reserver=nina)
        board_game.group_reserved_on = family
        board_game.save()

        event = EventList.objects.create(
            name="Pendaison de cremaillere",
            owner=demo,
            description="Liste publique fictive avec reservations invite.",
            event_date=timezone.localdate() + timedelta(days=21),
            is_demo=True,
        )
        Gift.objects.create(
            owner=demo,
            created_by=demo,
            title="Plante d'interieur",
            description="Facile a entretenir.",
            price=Decimal("29.00"),
            event_list=event,
        )
        Gift.objects.create(
            owner=demo,
            created_by=demo,
            title="Coffret cafe",
            description="Grains et tasse assortie.",
            price=Decimal("39.90"),
            event_list=event,
        )

        santa = EventList.objects.create(
            name="Secret Santa demo",
            owner=demo,
            description="Un tirage fictif pour explorer le mode Noel.",
            event_date=timezone.localdate() + timedelta(days=45),
            mode=EventList.MODE_SECRET_SANTA,
            budget_max=Decimal("30.00"),
            is_demo=True,
        )
        santa.participants.add(lea, sam, nina)
        SecretSantaExclusion.objects.create(event=santa, giver=demo, receiver=lea)
        Gift.objects.create(
            owner=lea,
            created_by=lea,
            title="Roman graphique",
            description="Edition grand format.",
            price=Decimal("24.00"),
        ).visible_in.add(family)

        return demo
