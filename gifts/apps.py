from django.apps import AppConfig


class GiftsConfig(AppConfig):
    name = "gifts"

    def ready(self):
        from . import signals  # noqa: F401
