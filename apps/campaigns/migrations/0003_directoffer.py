import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0002_initial"),
        ("deals", "0001_initial"),
        ("platforms", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DirectOffer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content_type", models.CharField(max_length=50)),
                (
                    "proposed_price",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=12,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
                ("message", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "advertiser",
                    models.ForeignKey(
                        limit_choices_to={"role": "advertiser"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="direct_offers_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "blogger",
                    models.ForeignKey(
                        limit_choices_to={"role": "blogger"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="direct_offers_received",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "campaign",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="direct_offers",
                        to="campaigns.campaign",
                    ),
                ),
                (
                    "deal",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="direct_offer",
                        to="deals.deal",
                    ),
                ),
                (
                    "platform",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="direct_offers",
                        to="platforms.platform",
                    ),
                ),
            ],
            options={
                "verbose_name": "Direct Offer",
                "verbose_name_plural": "Direct Offers",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="directoffer",
            constraint=models.UniqueConstraint(
                fields=["advertiser", "campaign", "platform"],
                name="unique_direct_offer_per_platform",
            ),
        ),
    ]
