from rest_framework import serializers

from .models import Campaign, Response


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
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "blogger_email",
            "campaign_name",
            "platform_url",
            "status",
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

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        return Response.objects.create(blogger=request.user, **validated_data)
