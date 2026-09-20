from django.apps import AppConfig


class BusinessQueriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.business_queries"

    def ready(self):
        from . import checks  # noqa
