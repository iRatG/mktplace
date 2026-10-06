from rest_framework import serializers

from .models import Campaign, Response
from .validation import active_response, budget_committed, campaign_param_errors, deals_in_cap


class CampaignSerializer(serializers.ModelSerializer):
    advertiser_email = serializers.EmailField(source="advertiser.email", read_only=True)
    category_name = serializers.CharField(
        source="category.name", read_only=True, default=None
    )
    responses_count = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = (
            "id",
            "advertiser_email",
            "name",
            "description",
            "category",
            "category_name",
            "subject",
            "image",
            "content_types",
            "required_elements",
            "payment_type",
            "fixed_price",
            "cpa_type",
            "cpa_rate",
            "cpa_tracking_url",
            "budget",
            "start_date",
            "end_date",
            "content_start",
            "deadline",
            "min_subscribers",
            "min_er",
            "allowed_socials",
            "status",
            "rejection_reason",
            "max_bloggers",
            "responses_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "advertiser_email",
            "category_name",
            "status",
            "rejection_reason",
            "responses_count",
            "created_at",
            "updated_at",
        )

    def get_responses_count(self, obj):
        return obj.responses.count()


class CampaignCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campaign
        fields = (
            "id",
            "name",
            "description",
            "category",
            "subject",
            "image",
            "content_types",
            "required_elements",
            "payment_type",
            "fixed_price",
            "cpa_type",
            "cpa_rate",
            "cpa_tracking_url",
            "budget",
            "start_date",
            "end_date",
            "content_start",
            "deadline",
            "min_subscribers",
            "min_er",
            "allowed_socials",
            "max_bloggers",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        payment_type = attrs.get("payment_type")
        if payment_type == Campaign.PaymentType.FIXED and not attrs.get("fixed_price"):
            raise serializers.ValidationError(
                {"fixed_price": "Fixed price is required for fixed payment type."}
            )
        if payment_type == Campaign.PaymentType.CPA:
            if not attrs.get("cpa_type"):
                raise serializers.ValidationError(
                    {"cpa_type": "CPA type is required for CPA payment type."}
                )
            if not attrs.get("cpa_rate"):
                raise serializers.ValidationError(
                    {"cpa_rate": "CPA rate is required for CPA payment type."}
                )

        # При частичном обновлении недостающие значения берём из кампании.
        def value(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        errors = campaign_param_errors(
            payment_type=value("payment_type") or Campaign.PaymentType.FIXED,
            fixed_price=value("fixed_price"),
            budget=value("budget"),
            start_date=value("start_date"),
            end_date=value("end_date"),
            deadline=value("deadline"),
            content_start=value("content_start"),
            max_bloggers=value("max_bloggers"),
            taken_slots=deals_in_cap(self.instance),
            committed_budget=budget_committed(self.instance),
        )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        return Campaign.objects.create(advertiser=request.user, **validated_data)


class ResponseSerializer(serializers.ModelSerializer):
    blogger_email = serializers.EmailField(source="blogger.email", read_only=True)
    campaign_name = serializers.CharField(source="campaign.name", read_only=True)
    platform_url = serializers.URLField(source="platform.url", read_only=True)

    class Meta:
        model = Response
        fields = (
            "id",
            "blogger_email",
            "campaign",
            "campaign_name",
            "platform",
            "platform_url",
            "content_type",
            "proposed_price",
            "message",
            "status",
            "rejection_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "blogger_email",
            "campaign_name",
            "platform_url",
            "status",
            "rejection_reason",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        request = self.context["request"]
        campaign = attrs.get("campaign")
        platform = attrs.get("platform")

        if platform and platform.blogger != request.user:
            raise serializers.ValidationError(
                {"platform": "You can only respond with your own platform."}
            )

        if campaign and campaign.status != Campaign.Status.ACTIVE:
            raise serializers.ValidationError(
                {"campaign": "This campaign is not accepting responses."}
            )

        if campaign and active_response(campaign, request.user) is not None:
            raise serializers.ValidationError(
                {"campaign": "You already have a pending or accepted response to this campaign."}
            )

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        return Response.objects.create(blogger=request.user, **validated_data)
