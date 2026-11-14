from django.test.runner import DiscoverRunner


class Runner(DiscoverRunner):
    """Обычный раннер Django + значения по умолчанию тестовых данных (см. apps/profiles/testing.py)."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        from apps.profiles.testing import admit_bloggers_by_default

        admit_bloggers_by_default()
