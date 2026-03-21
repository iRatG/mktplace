from django.apps import AppConfig


class ProfilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.profiles"

    def ready(self):
        from django.db.models.signals import post_save
        from django.apps import apps
        from .models import BloggerProfile, AdvertiserProfile

        def create_user_profile(sender, instance, created, **kwargs):
            if not created:
                return
            if instance.role == "blogger":
                BloggerProfile.objects.get_or_create(user=instance)
            elif instance.role == "advertiser":
                AdvertiserProfile.objects.get_or_create(user=instance)

        User = apps.get_model("users", "User")
        post_save.connect(create_user_profile, sender=User)
