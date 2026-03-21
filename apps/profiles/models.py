from django.conf import settings
from django.db import models


class AdvertiserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="advertiser_profile",
    )
    company_name = models.CharField(max_length=255, blank=True)
    industry = models.CharField(max_length=100, blank=True)
    contact_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    logo = models.ImageField(upload_to="advertiser_logos/", null=True, blank=True)
    description = models.TextField(blank=True)
    inn = models.CharField(max_length=20, blank=True, verbose_name="INN")
    is_complete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Advertiser Profile"
        verbose_name_plural = "Advertiser Profiles"

    def __str__(self):
        return f"AdvertiserProfile({self.user.email})"

    def check_completeness(self):
        required = [
            self.company_name,
            self.industry,
            self.contact_name,
            self.phone,
        ]
        self.is_complete = all(required)
        self.save(update_fields=["is_complete"])
        return self.is_complete


class BloggerProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blogger_profile",
    )
    nickname = models.CharField(max_length=100, blank=True)
    avatar = models.ImageField(upload_to="blogger_avatars/", null=True, blank=True)
    bio = models.TextField(blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    deals_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Blogger Profile"
        verbose_name_plural = "Blogger Profiles"

    def __str__(self):
        return f"BloggerProfile({self.user.email})"
