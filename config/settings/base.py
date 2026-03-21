from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
_env_file = BASE_DIR / '.env'
if _env_file.exists():
    environ.Env.read_env(_env_file)

SECRET_KEY = env('DJANGO_SECRET_KEY')
DEBUG = env.bool('DJANGO_DEBUG', default=False)
ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=[])

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'django_filters',
    'django_celery_beat',
    'drf_spectacular',
]

LOCAL_APPS = [
    'apps.users',
    'apps.profiles',
    'apps.platforms',
    'apps.campaigns',
    'apps.deals',
    'apps.billing',
    'apps.notifications',
    'apps.analytics',
    'apps.web',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.web.context_processors.currency',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('POSTGRES_DB'),
        'USER': env('POSTGRES_USER'),
        'PASSWORD': env('POSTGRES_PASSWORD'),
        'HOST': env('POSTGRES_HOST', default='db'),
        'PORT': env('POSTGRES_PORT', default='5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTH_USER_MODEL = 'users.User'

LANGUAGE_CODE = env('LANGUAGE_CODE', default='ru-ru')
TIME_ZONE = env('TIME_ZONE', default='Asia/Tashkent')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'

# ── Currency / локализация ─────────────────────────────────────────────────────
# Чтобы переключить страну — поменяйте эти переменные в .env
# Пример для Узбекистана:
#   CURRENCY_SYMBOL=so'm
#   CURRENCY_CODE=UZS
#   CURRENCY_MIN_WITHDRAWAL=65000
#   CURRENCY_MIN_DEPOSIT=130000
#   PLATFORM_COMMISSION_PERCENT=15
CURRENCY_SYMBOL          = env('CURRENCY_SYMBOL',          default='₽')
CURRENCY_CODE            = env('CURRENCY_CODE',            default='RUB')
CURRENCY_MIN_WITHDRAWAL  = env.int('CURRENCY_MIN_WITHDRAWAL',  default=500)
CURRENCY_MIN_DEPOSIT     = env.int('CURRENCY_MIN_DEPOSIT',     default=1000)

# Redis
REDIS_URL = env('REDIS_URL', default='redis://redis:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30  # 30 дней

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://redis:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='redis://redis:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_BEAT_SCHEDULE = {
    'auto-complete-deals': {
        'task': 'apps.deals.tasks.auto_complete_deals',
        'schedule': 3600,  # every hour
    },
    'auto-approve-creative': {
        'task': 'apps.deals.tasks.auto_approve_creative',
        'schedule': 3600,  # every hour
    },
    'auto-cancel-overdue-deals': {
        'task': 'apps.deals.tasks.auto_cancel_overdue_deals',
        'schedule': 3600,  # every hour
    },
}

# DRF
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# JWT
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': True,
}

# CORS
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[
    'http://localhost:3000',
    'http://127.0.0.1:3000',
])

# API docs
SPECTACULAR_SETTINGS = {
    'TITLE': 'Mktplace API',
    'DESCRIPTION': 'Платформа для размещения рекламы у блогеров',
    'VERSION': '1.0.0',
}

# Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@mktplace.com')

# Frontend URL (used in email confirmation and password reset links)
FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:8000')

# Platform business settings
PLATFORM_COMMISSION_PERCENT = env.int('PLATFORM_COMMISSION_PERCENT', default=15)
MIN_WITHDRAWAL_AMOUNT = env.int('MIN_WITHDRAWAL_AMOUNT', default=500)
MIN_DEPOSIT_AMOUNT = env.int('MIN_DEPOSIT_AMOUNT', default=1000)
MAX_LOGIN_ATTEMPTS = 5
LOGIN_BLOCK_DURATION_MINUTES = 15
DEAL_AUTO_COMPLETE_HOURS = 72
CREATIVE_APPROVAL_TIMEOUT_HOURS = 48
MAX_CREATIVE_ITERATIONS = 3
