from django.apps import AppConfig


class RegistrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.registration"

    def ready(self):
        from . import checks  # noqa
