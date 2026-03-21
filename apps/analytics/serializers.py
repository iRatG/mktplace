from rest_framework import serializers


class AdvertiserDashboardSerializer(serializers.Serializer):
    total_campaigns = serializers.IntegerField()
    active_campaigns = serializers.IntegerField()
    total_deals = serializers.IntegerField()
    completed_deals = serializers.IntegerField()
    total_spent = serializers.DecimalField(max_digits=14, decimal_places=2)
    active_deals = serializers.IntegerField()
    pending_responses = serializers.IntegerField()
    campaigns_by_status = serializers.DictField(child=serializers.IntegerField())
    deals_by_status = serializers.DictField(child=serializers.IntegerField())


class BloggerDashboardSerializer(serializers.Serializer):
    total_deals = serializers.IntegerField()
    completed_deals = serializers.IntegerField()
    active_deals = serializers.IntegerField()
    total_earned = serializers.DecimalField(max_digits=14, decimal_places=2)
    available_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    total_platforms = serializers.IntegerField()
    approved_platforms = serializers.IntegerField()
    pending_responses = serializers.IntegerField()
    deals_by_status = serializers.DictField(child=serializers.IntegerField())


class AdminDashboardSerializer(serializers.Serializer):
    total_users = serializers.IntegerField()
    total_advertisers = serializers.IntegerField()
    total_bloggers = serializers.IntegerField()
    new_users_today = serializers.IntegerField()
    total_campaigns = serializers.IntegerField()
    active_campaigns = serializers.IntegerField()
    campaigns_pending_moderation = serializers.IntegerField()
    total_deals = serializers.IntegerField()
    active_deals = serializers.IntegerField()
    disputed_deals = serializers.IntegerField()
    total_platforms = serializers.IntegerField()
    platforms_pending_moderation = serializers.IntegerField()
    total_volume = serializers.DecimalField(max_digits=14, decimal_places=2)
    pending_withdrawals = serializers.IntegerField()
    pending_withdrawals_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
