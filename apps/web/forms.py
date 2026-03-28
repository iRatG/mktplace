from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.campaigns.models import Campaign, DirectOffer
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


# ── Catalog (Module 10) ───────────────────────────────────────────────────────

class CatalogFilterForm(forms.Form):
    """GET-форма фильтрации каталога блогерских площадок (Модуль 10).

    Все поля необязательны. Применяется в blogger_catalog view.

    Фильтры:
        social_type      — тип соцсети (Platform.SocialType choices)
        category         — тематика площадки (Category FK, pk)
        min_subscribers  — подписчики от
        max_subscribers  — подписчики до
        min_price        — цена за пост от (Platform.price_post)
        max_price        — цена за пост до
        min_er           — ER% от (Platform.engagement_rate)
        max_er           — ER% до
        min_rating       — минимальный рейтинг (BloggerProfile.rating, 0–5)
        sort             — порядок сортировки результатов
                           По умолчанию (пусто): рейтинг блогера по убыванию.
    """

    SORT_CHOICES = [
        ("", "По умолчанию"),
        ("-subscribers", "Подписчики (больше)"),
        ("-engagement_rate", "ER% (больше)"),
        ("price_post", "Цена (меньше)"),
        ("-price_post", "Цена (больше)"),
        ("-created_at", "Новые первые"),
    ]

    social_type = forms.ChoiceField(
        choices=[("", "Все соцсети")] + Platform.SocialType.choices,
        required=False,
        label="Соцсеть",
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        empty_label="Все тематики",
        label="Тематика",
    )
    min_subscribers = forms.IntegerField(
        required=False, min_value=0, label="Подписчиков от",
        widget=forms.NumberInput(attrs={"placeholder": "0"}),
    )
    max_subscribers = forms.IntegerField(
        required=False, min_value=0, label="Подписчиков до",
        widget=forms.NumberInput(attrs={"placeholder": "любое"}),
    )
    min_price = forms.DecimalField(
        required=False, min_value=0, label="Цена от",
        widget=forms.NumberInput(attrs={"placeholder": "0"}),
    )
    max_price = forms.DecimalField(
        required=False, min_value=0, label="Цена до",
        widget=forms.NumberInput(attrs={"placeholder": "любая"}),
    )
    min_er = forms.DecimalField(
        required=False, min_value=0, label="ER% от",
        widget=forms.NumberInput(attrs={"placeholder": "0", "step": "0.1"}),
    )
    max_er = forms.DecimalField(
        required=False, min_value=0, label="ER% до",
        widget=forms.NumberInput(attrs={"placeholder": "любой", "step": "0.1"}),
    )
    min_rating = forms.DecimalField(
        required=False, min_value=0, max_value=5, label="Рейтинг от",
        widget=forms.NumberInput(attrs={"placeholder": "0", "step": "0.1"}),
    )
    sort = forms.ChoiceField(choices=SORT_CHOICES, required=False, label="Сортировка")


class DirectOfferForm(forms.Form):
    """Форма создания прямого предложения от рекламодателя блогеру (Модуль 10).

    Инициализируется с обязательным аргументом advertiser (User):
        form = DirectOfferForm(advertiser=request.user, data=request.POST)

    Поля:
        campaign       — одна из ACTIVE кампаний текущего рекламодателя (ModelChoiceField)
        content_type   — тип контента (post / stories / video / review / reels)
        proposed_price — предлагаемая цена за размещение (необязательно;
                         если пусто — используется campaign.fixed_price)
        message        — произвольное сообщение блогеру (необязательно)

    __init__:
        Ограничивает queryset кампаний: только advertiser=advertiser, status=ACTIVE.
    """

    CONTENT_TYPE_CHOICES = [
        ("post", "Пост"),
        ("stories", "Сторис"),
        ("video", "Видео"),
        ("review", "Обзор"),
        ("reels", "Reels"),
    ]

    campaign = forms.ModelChoiceField(
        queryset=Campaign.objects.none(),
        label="Кампания",
        empty_label="Выберите кампанию",
    )
    content_type = forms.ChoiceField(choices=CONTENT_TYPE_CHOICES, label="Тип контента")
    proposed_price = forms.DecimalField(
        required=False, min_value=0, label="Предлагаемая цена",
        widget=forms.NumberInput(attrs={"placeholder": "оставьте пустым — цена по кампании"}),
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Расскажите блогеру о вашем предложении"}),
        required=False,
        label="Сообщение",
    )

    def __init__(self, advertiser, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["campaign"].queryset = Campaign.objects.filter(
            advertiser=advertiser, status=Campaign.Status.ACTIVE
        )


# ── Reviews (Module 7) ────────────────────────────────────────────────────────

class ReviewForm(forms.Form):
    """Форма отзыва рекламодателя о блогере после завершения сделки (Модуль 7).

    Используется в deal_detail (POST) и deal_review_submit view.

    Поля:
        rating — оценка от 1 до 5 звёзд
        text   — текстовый комментарий (необязательно, до 1000 символов)

    Окно: 7 дней после COMPLETED. Один отзыв на сделку. Проверяется в view.
    """

    RATING_CHOICES = [
        (1, "1 ★ — Плохо"),
        (2, "2 ★ — Ниже среднего"),
        (3, "3 ★ — Нормально"),
        (4, "4 ★ — Хорошо"),
        (5, "5 ★ — Отлично"),
    ]

    rating = forms.IntegerField(
        min_value=1,
        max_value=5,
        label="Оценка",
        widget=forms.Select(choices=RATING_CHOICES),
    )
    text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Расскажите о сотрудничестве"}),
        required=False,
        max_length=1000,
        label="Комментарий",
    )


# ── Category form (Module 13) ─────────────────────────────────────────────────

class CategoryForm(forms.Form):
    """Форма создания категории платформы в админ-панели (Модуль 13).

    Используется в admin_categories view.

    Поля:
        name — человекочитаемое название категории (unique)
        slug — URL-slug (unique)
    """

    name = forms.CharField(max_length=100, label="Название")
    slug = forms.SlugField(max_length=100, label="Slug")


class ChatMessageForm(forms.Form):
    """Форма отправки сообщения в чате сделки (Модуль 7 / Sprint 6).

    Используется в deal_send_message view.
    Доступна сторонам сделки (блогер, рекламодатель) и is_staff.
    После завершения/отмены сделки чат переходит в режим только чтения.

    Поля:
        text — текст сообщения (необязательно если есть file)
        file — прикреплённый файл до 10 МБ (необязательно если есть text)
    """

    text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Напишите сообщение..."}),
        max_length=2000,
        required=False,
        label="",
    )
    file = forms.FileField(
        required=False,
        label="",
        help_text="До 10 МБ",
    )

    def clean(self):
        cleaned_data = super().clean()
        text = cleaned_data.get("text", "").strip()
        file = cleaned_data.get("file")
        if not text and not file:
            raise forms.ValidationError("Введите текст или прикрепите файл.")
        return cleaned_data


# ── Creative submit form (Sprint 7) ───────────────────────────────────────────

class CreativeSubmitForm(forms.Form):
    """Форма отправки креатива на согласование (Sprint 7).

    Используется в deal_submit_creative view (только блогер).
    Переводит сделку из IN_PROGRESS → ON_APPROVAL.

    Поля:
        creative_text  — текст креатива (необязательно если есть creative_media)
        creative_media — медиафайл (необязательно если есть creative_text)
    """

    creative_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Текст рекламного поста..."}),
        max_length=5000,
        required=False,
        label="Текст креатива",
    )
    creative_media = forms.FileField(
        required=False,
        label="Медиафайл",
        help_text="Изображение или видео для публикации",
    )

    def clean(self):
        cleaned_data = super().clean()
        text = cleaned_data.get("creative_text", "").strip()
        media = cleaned_data.get("creative_media")
        if not text and not media:
            raise forms.ValidationError("Укажите текст креатива или прикрепите медиафайл.")
        return cleaned_data
