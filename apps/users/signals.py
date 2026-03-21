from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


@receiver(post_save, sender=User)
def create_user_related_objects(sender, instance, created, **kwargs):
    """
    When a new user is created:
    - Create AdvertiserProfile or BloggerProfile depending on role
    - Create Wallet
    """
    if not created:
        return

    # Create role-specific profile
    if instance.role == User.Role.ADVERTISER:
        from apps.profiles.models import AdvertiserProfile
        AdvertiserProfile.objects.get_or_create(user=instance)

    elif instance.role == User.Role.BLOGGER:
        from apps.profiles.models import BloggerProfile
        BloggerProfile.objects.get_or_create(user=instance)

    # Create wallet for every user
    from apps.billing.models import Wallet
    Wallet.objects.get_or_create(user=instance)
