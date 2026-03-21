from rest_framework import serializers

from .models import ChatMessage, Deal, DealStatusLog


class DealSerializer(serializers.ModelSerializer):
    blogger_email = serializers.EmailField(source="blogger.email", read_only=True)
    advertiser_email = serializers.EmailField(source="advertiser.email", read_only=True)
    campaign_name = serializers.CharField(source="campaign.name", read_only=True)
    platform_url = serializers.URLField(source="platform.url", read_only=True)

    class Meta:
        model = Deal
        fields = (
            "id",
            "campaign",
            "campaign_name",
            "blogger",
            "blogger_email",
            "advertiser",
            "advertiser_email",
            "platform",
            "platform_url",
            "response",
            "amount",
            "status",
            "creative_text",
            "creative_media",
            "creative_submitted_at",
            "creative_approved_at",
            "creative_rejection_reason",
            "publication_url",
            "publication_at",
            "dispute_reason",
            "dispute_opened_at",
            "dispute_resolved_at",
            "dispute_resolution",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "blogger_email",
            "advertiser_email",
            "campaign_name",
            "platform_url",
            "status",
            "creative_submitted_at",
            "creative_approved_at",
            "creative_rejection_reason",
            "publication_at",
            "dispute_opened_at",
            "dispute_resolved_at",
            "created_at",
            "updated_at",
        )


class ChatMessageSerializer(serializers.ModelSerializer):
    sender_email = serializers.EmailField(
        source="sender.email", read_only=True, default=None
    )

    class Meta:
        model = ChatMessage
        fields = ("id", "deal", "sender", "sender_email", "text", "file", "is_system", "created_at")
        read_only_fields = ("id", "sender", "sender_email", "is_system", "created_at")

    def create(self, validated_data):
        request = self.context["request"]
        return ChatMessage.objects.create(sender=request.user, **validated_data)

    def validate_deal(self, value):
        request = self.context["request"]
        user = request.user
        if value.blogger != user and value.advertiser != user:
            raise serializers.ValidationError(
                "You are not a participant in this deal."
            )
        return value


class DealStatusLogSerializer(serializers.ModelSerializer):
    changed_by_email = serializers.EmailField(
        source="changed_by.email", read_only=True, default=None
    )

    class Meta:
        model = DealStatusLog
        fields = (
            "id",
            "deal",
            "old_status",
            "new_status",
            "changed_by",
            "changed_by_email",
            "comment",
            "created_at",
        )
        read_only_fields = fields
