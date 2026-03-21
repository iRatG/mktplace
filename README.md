# Mktplace — Платформа для рекламы у блогеров

Двусторонний маркетплейс, соединяющий рекламодателей и блогеров. Рекламодатели создают кампании, блогеры откликаются, платформа обеспечивает безопасные эскроу-расчёты.

## Стек технологий

| Слой | Технология |
|---|---|
| Backend | Python 3.12 + Django 5.0 + Django REST Framework |
| База данных | PostgreSQL 16 |
| Кеш / Брокер | Redis 7 |
| Очередь задач | Celery 5 + django-celery-beat |
| Аутентификация | JWT (djangorestframework-simplejwt) |
| API документация | drf-spectacular (Swagger UI) |
| Фронтенд | Django Templates + Tailwind CSS (CDN) |
| Dev-среда | Docker + docker-compose |
| Production | Nginx + Gunicorn |

## Быстрый старт (локальная разработка)

### Требования

- Docker Desktop
- Git

### Установка

```bash
git clone https://github.com/iRatG/mktplace.git
cd mktplace

# Создать .env из примера
cp .env.example .env
# Отредактировать .env — минимально нужны только значения по умолчанию

# Собрать и запустить
docker-compose up --build
```

После запуска:

| Сервис | URL |
|---|---|
| Сайт (лендинг) | http://localhost:8000/ |
| Вход | http://localhost:8000/login/ |
| Django Admin | http://localhost:8000/admin/ |
| Swagger UI | http://localhost:8000/api/docs/ |

### Первые шаги после запуска

```bash
# Применить миграции (если не применились автоматически)
make migrate

# Создать суперпользователя
make superuser

# Посмотреть логи
make logs
```

## Переменные окружения

Все переменные хранятся в `.env`. Пример — `.env.example`.

| Переменная | Описание | По умолчанию |
|---|---|---|
| `DJANGO_SECRET_KEY` | Секретный ключ Django | — (обязательна) |
| `DJANGO_DEBUG` | Режим отладки | `False` |
| `DJANGO_ALLOWED_HOSTS` | Разрешённые хосты | `[]` |
| `POSTGRES_DB` | Имя базы данных | — |
| `POSTGRES_USER` | Пользователь БД | — |
| `POSTGRES_PASSWORD` | Пароль БД | — |
| `POSTGRES_HOST` | Хост БД | `db` |
| `REDIS_URL` | URL Redis | `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | Брокер Celery | `redis://redis:6379/0` |
| `EMAIL_HOST` | SMTP сервер | `smtp.gmail.com` |
| `EMAIL_HOST_USER` | Email отправителя | — |
| `EMAIL_HOST_PASSWORD` | Пароль SMTP | — |
| `FRONTEND_URL` | Базовый URL сайта (в письмах) | `http://localhost:8000` |
| `PLATFORM_COMMISSION_PERCENT` | Комиссия платформы % | `15` |
| `MIN_WITHDRAWAL_AMOUNT` | Минимальная сумма вывода ₽ | `500` |

## Структура проекта

```
mktplace/
├── apps/
│   ├── users/          # Пользователи, аутентификация, токены
│   ├── profiles/       # Профили рекламодателей и блогеров
│   ├── platforms/      # Площадки блогеров (соцсети)
│   ├── campaigns/      # Рекламные кампании и отклики
│   ├── deals/          # Сделки, чат, статус-логи
│   ├── billing/        # Кошельки, транзакции, вывод средств
│   ├── notifications/  # Уведомления
│   ├── analytics/      # Аналитика
│   └── web/            # Web-интерфейс (views, forms, urls)
├── config/
│   ├── settings/
│   │   ├── base.py     # Базовые настройки
│   │   ├── local.py    # Локальная разработка
│   │   └── production.py  # Продакшн
│   ├── urls.py
│   └── celery.py
├── templates/
│   ├── base.html           # Базовый шаблон сайта
│   ├── base_email.html     # Базовый шаблон писем
│   ├── auth/               # Страницы авторизации
│   ├── campaigns/          # Страницы кампаний
│   ├── dashboard/          # Дашборды пользователей
│   └── emails/             # HTML письма
├── nginx/                  # Конфиг Nginx (production)
├── requirements/
│   ├── base.txt
│   ├── local.txt
│   └── production.txt
├── docker-compose.yml      # Локальная разработка
├── docker-compose.prod.yml # Production
├── Dockerfile
├── Dockerfile.local
└── Makefile
```

## Ключевые команды (Makefile)

```bash
make up          # Запустить все сервисы
make upd         # Запустить в фоне
make down        # Остановить
make build       # Пересобрать образы
make migrate     # Применить миграции
make makemigrations  # Создать миграции
make superuser   # Создать суперпользователя
make shell       # Django shell
make logs        # Логи web-сервиса
make deploy      # Деплой на production
```

## Архитектура бизнес-логики

### Поток денег (эскроу)

```
Рекламодатель пополняет баланс
        ↓
    [available_balance]
        ↓ при принятии отклика
    [reserved_balance]  ← деньги заморожены
        ↓ при завершении сделки
    Блогер получает 85% (15% — комиссия платформы)
        ↓ при запросе на вывод
    [on_withdrawal] → выплата администратором
```

### Жизненный цикл сделки

```
Отклик принят → WAITING_PAYMENT → IN_PROGRESS
→ ON_APPROVAL (если требуется согласование)
→ WAITING_PUBLICATION → PUBLISHED → CHECKING
→ COMPLETED (деньги блогеру) / DISPUTED (спор)
```

### Celery задачи (периодические)

| Задача | Расписание | Описание |
|---|---|---|
| `auto_complete_deals` | каждый час | Завершает сделки в CHECKING > 72ч |
| `auto_approve_creative` | каждый час | Авто-одобряет креатив через 48ч |
| `auto_cancel_overdue_deals` | каждый час | Отменяет WAITING_PAYMENT > 24ч |

## Деплой на production (VPS)

### Первый деплой

```bash
ssh user@your-vps

git clone https://github.com/iRatG/mktplace.git
cd mktplace

cp .env.example .env.prod
nano .env.prod  # Заполнить все значения

docker-compose -f docker-compose.prod.yml up -d --build
docker-compose -f docker-compose.prod.yml exec web python manage.py migrate
docker-compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
docker-compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput
```

### Обновление

```bash
make deploy
# или вручную:
git pull
docker-compose -f docker-compose.prod.yml up -d --build
docker-compose -f docker-compose.prod.yml exec web python manage.py migrate
```

## API

REST API доступен по адресу `/api/v1/`. Swagger UI: `/api/docs/`.

Аутентификация: JWT Bearer Token.

```bash
# Получить токен
POST /api/v1/auth/login/
{"email": "user@example.com", "password": "..."}

# Использовать
Authorization: Bearer <access_token>
```

## Разработка

### Создание миграций после изменения моделей

```bash
make makemigrations
make migrate
```

### Запуск без Docker (для отладки отдельного сервиса)

```bash
python manage.py runserver --settings=config.settings.local
celery -A config worker -l info
celery -A config beat -l info
```

---

**Demo VPS:** 89.111.152.228
**GitHub:** https://github.com/iRatG/mktplace
