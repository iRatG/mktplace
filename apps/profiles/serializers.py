from rest_framework import serializers

from .models import AdvertiserProfile, BloggerProfile


class AdvertiserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdvertiserProfile
        fields = (
            "id",
            "company_name",
            "industry",
            "contact_name",
            "phone",
            "website",
            "logo",
            "description",
            "inn",
            "is_complete",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "is_complete", "created_at", "updated_at")

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        instance.check_completeness()
        return instance


class BloggerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BloggerProfile
        fields = (
            "id",
            "nickname",
            "avatar",
            "bio",
            "rating",
            "deals_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "rating", "deals_count", "created_at", "updated_at")


class PublicBloggerProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)

    class Meta:
        model = BloggerProfile
        fields = (
            "user_id",
            "email",
            "nickname",
            "avatar",
            "bio",
            "rating",
            "deals_count",
        )
