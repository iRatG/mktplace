from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS += ['debug_toolbar']

MIDDLEWARE = ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE

INTERNAL_IPS = ['127.0.0.1']

CORS_ALLOW_ALL_ORIGINS = True

# В тестах — быстрый хешер паролей: PBKDF2 (870 000 итераций) на каждом create_user/login
# замедлял полный прогон в разы. Тесты проверяют логику, а не стойкость хеша; на проде
# (config.settings.production) не действует.
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
