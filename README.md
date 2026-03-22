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
| `CURRENCY_SYMBOL` | Символ / код валюты | `UZS` |
| `CURRENCY_CODE` | ISO-код валюты | `UZS` |
| `CURRENCY_MIN_WITHDRAWAL` | Минимальная сумма вывода | `65000` |
| `CURRENCY_MIN_DEPOSIT` | Минимальное пополнение баланса | `130000` |

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
│   ├── landing.html        # Лендинг (публичная страница)
│   ├── faq.html            # FAQ / Справка
│   ├── auth/               # Страницы авторизации
│   ├── campaigns/          # Страницы кампаний и откликов
│   ├── catalog/            # Каталог блогеров и форма прямого предложения
│   ├── dashboard/          # Дашборды (advertiser, blogger)
│   ├── deals/              # Страницы сделок
│   ├── profiles/           # Профиль (своя страница, редактирование, публичная)
│   ├── platforms/          # Форма добавления/редактирования площадки
│   ├── billing/            # Кошелёк и транзакции
│   ├── notifications/      # Страница уведомлений
│   ├── admin_panel/        # Панель администратора (/panel/)
│   ├── partials/           # Переиспользуемые блоки
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
| `cleanup_old_notifications` | ежедневно | Удаляет уведомления старше 90 дней |

## Деплой на production (VPS)

### Первый деплой

```bash
ssh user@your-vps

git clone https://github.com/iRatG/mktplace.git
cd mktplace

cp .env.example .env.prod
nano .env.prod  # Заполнить все значения

docker-compose -f docker-compose.vps.yml up -d --build
# Миграции и статика применяются автоматически через entrypoint.sh
```

### Обновление

```bash
git pull
nohup docker-compose -f docker-compose.vps.yml up -d --build > /tmp/deploy.log 2>&1 &
```

## Веб-интерфейс (URL-структура)

| Раздел | URL | Роль |
|---|---|---|
| Лендинг | `/` | Публичный |
| Вход / Регистрация | `/login/`, `/register/` | Публичный |
| Дашборд | `/dashboard/advertiser/`, `/dashboard/blogger/` | По роли |
| Профиль | `/profile/`, `/profile/edit/` | Авторизованные |
| Публичный профиль блогера | `/bloggers/<pk>/` | Авторизованные |
| Площадки | `/platforms/add/`, `/platforms/<pk>/edit/` | Блогер |
| Кампании | `/campaigns/`, `/campaigns/create/`, `/campaigns/<pk>/` | По роли |
| Сделки | `/deals/`, `/deals/<pk>/` | По роли |
| **Отзыв о сделке** | `/deals/<pk>/review/` | Рекламодатель |
| Кошелёк | `/wallet/` | Авторизованные |
| **Уведомления** | `/notifications/` | Авторизованные |
| **Каталог блогеров** | `/bloggers/` | Рекламодатель |
| **Прямое предложение** | `/bloggers/<pk>/offer/` | Рекламодатель |
| **Принять/отклонить оффер** | `/offers/<pk>/accept/`, `/offers/<pk>/reject/` | Блогер |
| Админ-панель (дашборд) | `/panel/` | is_staff |
| Кампании на модерации | `/panel/campaigns/` | is_staff |
| Площадки на проверке | `/panel/platforms/` | is_staff |
| Споры | `/panel/disputes/` | is_staff |
| Заявки на вывод | `/panel/withdrawals/` | is_staff |
| Пользователи (поиск, блок) | `/panel/users/` | is_staff |
| **Категории** | `/panel/categories/` | is_staff |
| **Аналитика** | `/analytics/` | По роли (adv/blogger) |
| FAQ | `/faq/` | Публичный |

### Демо-аккаунты

```bash
# Создать демо-пользователей
docker compose run --rm web python manage.py create_demo_users --reset

# Заполнить демо-данными
docker compose run --rm web python manage.py seed_demo_data --reset
```

| Роль | Email | Пароль |
|---|---|---|
| Рекламодатель | advertiser@demo.com | Demo1234! |
| Блогер | blogger@demo.com | Demo1234! |
| Администратор | admin@demo.com | Demo1234! |

## Реализованные модули

| Модуль | Статус | Описание |
|---|---|---|
| 2. Пользователи / Auth | ✅ | Регистрация, email-подтверждение, вход, восстановление пароля, роли, блокировка |
| 3. Профили | ✅ | BloggerProfile, AdvertiserProfile, автосоздание, is_complete, публичный профиль |
| 4. Площадки | ✅ | CRUD площадок, модерация, статусы PENDING/APPROVED/REJECTED |
| 5. Кампании | ✅ | CRUD, статусы, draft→moderation→active→paused/cancelled/completed |
| 6. Отклики | ✅ | Блогер откликается, рекламодатель принимает/отклоняет, max_bloggers guard |
| 7. Сделки + Отзывы | ✅ | Жизненный цикл, DealStatusLog, submit/confirm/cancel, Celery авто-задачи; Review модель (1–5★, 7 дней окно, пересчёт рейтинга) |
| 8. Биллинг | ✅ | Кошелёк, эскроу, транзакции, вывод средств, BillingService |
| 10. Каталог блогеров | ✅ | Каталог с фильтрами, прямые предложения (DirectOffer), accept/reject |
| 11A. In-app уведомления | ✅ | NotificationService, колокольчик в меню, страница уведомлений, авто-очистка 90 дней |
| 12. Аналитика | ✅ | Дашборд рекламодателя (расходы, конверсия, кампании по статусам), блогера (заработок, рейтинг, отклики), админа (доход платформы, топ пользователей) |
| 13. Админ-панель | ✅ | Модерация кампаний, площадок, споры, выводы; поиск пользователей, блокировка, CRUD категорий |

**Не реализовано (следующие итерации):**
- Модуль 9: CPA-модель (трекинговые ссылки, конверсии)
- Модуль 11B/C: Email-уведомления, Telegram-бот
- Сделки: согласование креатива (ON_APPROVAL), чат

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

### Тесты

```bash
# Все тесты (277 тестов)
docker compose run --rm web python manage.py test apps --noinput

# URL/интеграционные тесты (129 тестов)
docker compose run --rm web python manage.py test apps.web.tests_urls -v 2

# Тесты профилей (43 теста)
docker compose run --rm web python manage.py test apps.web.tests_profiles -v 2

# Тесты каталога и прямых предложений (28 тестов)
docker compose run --rm web python manage.py test apps.web.tests_catalog -v 2

# Тесты уведомлений (28 тестов)
docker compose run --rm web python manage.py test apps.web.tests_notifications -v 2

# Тесты отзывов и доработок админ-панели (25 тестов)
docker compose run --rm web python manage.py test apps.web.tests_reviews -v 2

# Тесты аналитики (24 теста)
docker compose run --rm web python manage.py test apps.web.tests_analytics -v 2
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
