from .base import *
import sentry_sdk

DEBUG = False

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=[])
CSRF_TRUSTED_ORIGINS = env.list(
    'CSRF_TRUSTED_ORIGINS',
    default=['https://ublogers.uz', 'https://www.ublogers.uz'],
)

# Статика с хэшем содержимого в имени: WhiteNoise кэширует файлы на год (WHITENOISE_MAX_AGE), и без
# версии в имени браузер держал бы старый JS после выпуска (QA camp_test_3: не было кнопок −/+).
# Только для production — local и тесты работают без собранного manifest.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Sentry (опционально)
SENTRY_DSN = env('SENTRY_DSN', default='')
if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)
