from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.campaigns.models import Campaign
from apps.platforms.models import Category, Platform
from apps.profiles.models import AdvertiserProfile, BloggerProfile
from apps.users.models import User


class CampaignForm(forms.ModelForm):
    CONTENT_TYPE_CHOICES = [
        ("post", "Пост"),
        ("stories", "Сторис"),
        ("video", "Видео"),
        ("review", "Обзор"),
        ("reels", "Reels"),
    ]
    SOCIAL_CHOICES = [
        ("instagram", "Instagram"),
        ("telegram", "Telegram"),
        ("youtube", "YouTube"),
        ("vk", "ВКонтакте"),
        ("tiktok", "TikTok"),
    ]

    content_types = forms.MultipleChoiceField(
        choices=CONTENT_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Форматы контента",
    )
    allowed_socials = forms.MultipleChoiceField(
        choices=SOCIAL_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Площадки",
    )

    class Meta:
        model = Campaign
        fields = [
            "name", "description", "category",
            "payment_type", "fixed_price", "budget",
            "start_date", "end_date", "deadline",
            "min_subscribers", "content_types", "allowed_socials",
            "max_bloggers",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "deadline": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.all()
        self.fields["category"].required = False
        self.fields["description"].required = False
        # Restore saved multi-values from JSON list
        if self.instance.pk:
            self.initial["content_types"] = self.instance.content_types
            self.initial["allowed_socials"] = self.instance.allowed_socials

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment_type") == Campaign.PaymentType.FIXED and not cleaned.get("fixed_price"):
            self.add_error("fixed_price", "Укажите фиксированную цену.")
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput())
    password = forms.CharField(widget=forms.PasswordInput())


class RegisterForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=User.Role.choices)
    password1 = forms.CharField(widget=forms.PasswordInput())
    password2 = forms.CharField(widget=forms.PasswordInput())

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("Пользователь с таким email уже зарегистрирован.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("Пароли не совпадают.")
        if p1:
            try:
                validate_password(p1)
            except ValidationError as e:
                raise ValidationError(list(e.messages))
        return cleaned


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField()


class PasswordResetConfirmForm(forms.Form):
    password1 = forms.CharField(widget=forms.PasswordInput())
    password2 = forms.CharField(widget=forms.PasswordInput())

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("Пароли не совпадают.")
        if p1:
            try:
                validate_password(p1)
            except ValidationError as e:
                raise ValidationError(list(e.messages))
        return cleaned


class BloggerProfileForm(forms.ModelForm):
    class Meta:
        model = BloggerProfile
        fields = ["nickname", "bio"]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 4}),
        }


class AdvertiserProfileForm(forms.ModelForm):
    class Meta:
        model = AdvertiserProfile
        fields = ["company_name", "industry", "contact_name", "phone", "website", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class PlatformForm(forms.ModelForm):
    categories = forms.ModelMultipleChoiceField(
        queryset=Category.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Тематики",
    )

    class Meta:
        model = Platform
        fields = [
            "social_type", "url", "categories",
            "subscribers", "avg_views", "engagement_rate",
            "price_post", "price_stories", "price_video", "price_review",
        ]
