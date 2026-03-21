from rest_framework import serializers

from .models import Category, Platform


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "description")
        read_only_fields = ("id",)


class PlatformSerializer(serializers.ModelSerializer):
    categories = CategorySerializer(many=True, read_only=True)
    blogger_email = serializers.EmailField(source="blogger.email", read_only=True)

    class Meta:
        model = Platform
        fields = (
            "id",
            "blogger_email",
            "social_type",
            "url",
            "categories",
            "subscribers",
            "avg_views",
            "engagement_rate",
            "price_post",
            "price_stories",
            "price_video",
            "price_review",
            "status",
            "rejection_reason",
            "metrics_updated_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "blogger_email",
            "status",
            "rejection_reason",
            "metrics_updated_at",
            "created_at",
            "updated_at",
        )


class PlatformCreateSerializer(serializers.ModelSerializer):
    category_ids = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        many=True,
        write_only=True,
        required=False,
        source="categories",
    )

    class Meta:
        model = Platform
        fields = (
            "id",
            "social_type",
            "url",
            "category_ids",
            "subscribers",
            "avg_views",
            "engagement_rate",
            "price_post",
            "price_stories",
            "price_video",
            "price_review",
        )
        read_only_fields = ("id",)

    def create(self, validated_data):
        categories = validated_data.pop("categories", [])
        request = self.context["request"]
        platform = Platform.objects.create(blogger=request.user, **validated_data)
        if categories:
            platform.categories.set(categories)
        return platform

    def update(self, instance, validated_data):
        categories = validated_data.pop("categories", None)
        instance = super().update(instance, validated_data)
        if categories is not None:
            instance.categories.set(categories)
        return instance
