from rest_framework import serializers

from .models import Notification, NotificationSettings


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "type",
            "title",
            "body",
            "is_read",
            "related_deal",
            "created_at",
        )
        read_only_fields = ("id", "type", "title", "body", "related_deal", "created_at")


class NotificationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationSettings
        fields = ("id", "preferences", "updated_at")
        read_only_fields = ("id", "updated_at")

    def update(self, instance, validated_data):
        # Merge preferences rather than replace
        new_preferences = validated_data.get("preferences", {})
        instance.preferences.update(new_preferences)
        instance.save(update_fields=["preferences", "updated_at"])
        return instance
