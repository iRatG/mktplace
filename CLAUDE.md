# CLAUDE.md — Шпаргалка по проекту Mktplace

Это короткая шпаргалка для быстрого старта сессии. Подробные бизнес-правила
и сценарии по каждому модулю — в `openspec/specs/<app>/spec.md` (источник
истины после этой шпаргалки). История решений — в `openspec/changes/archive/`.

## Стек
Django 5.0 + DRF + PostgreSQL + Redis + Celery + Tailwind CSS (CDN)

## Запуск локально
```bash
docker compose up                        # запустить
docker compose run --rm web python manage.py test apps --noinput   # тесты
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py create_demo_users --reset
docker compose run --rm web python manage.py seed_demo_data
```

## Роли пользователей
- `ADVERTISER` — рекламодатель (создаёт кампании)
- `BLOGGER` — блогер (площадки, отклики)
- `is_staff=True` — администратор (видит всё, `/panel/`)
- Группа Django `"IT Team"` (поверх `is_staff`) — доступ к внутренним тикетам `/tickets/`

## Демо-аккаунты
| Роль | Email | Пароль |
|---|---|---|
| Advertiser | advertiser@demo.com | Demo1234! |
| Blogger | blogger@demo.com | Demo1234! |
| Admin | admin@demo.com | Demo1234! |

## Критические паттерны

### Staff в views — ВСЕГДА ПЕРВЫМ
```python
if user.is_staff:
    obj = get_object_or_404(Model, pk=pk)   # видит всё
elif user.role == User.Role.ADVERTISER:
    obj = get_object_or_404(Model, pk=pk, owner=user)
```

### DealStatusLog — log() ДО изменения статуса
```python
DealStatusLog.log(deal, Deal.Status.CHECKING, ...)  # сначала лог
deal.status = Deal.Status.CHECKING                   # потом статус
deal.save(...)
```

### Изменение статуса сделки — atomic + select_for_update
```python
with db_transaction.atomic():
    deal = Deal.objects.select_for_update().filter(pk=pk).first()
```

### Кампания для блогера — только ACTIVE
```python
campaign = get_object_or_404(Campaign, pk=pk, status=Campaign.Status.ACTIVE)
```

## Структура приложений
```
apps/users/         — Auth, роли, is_demo
apps/profiles/      — BloggerProfile, AdvertiserProfile
apps/platforms/     — Platform, Category, PermitDocument
apps/campaigns/     — Campaign, Response, DirectOffer
apps/deals/         — Deal, DealStatusLog, Review, ChatMessage
apps/billing/       — Wallet, Transaction, WithdrawalRequest, TestBalanceGrant
apps/notifications/ — Notification, NotificationService
apps/analytics/     — (views в apps/web)
apps/business_queries/ — Ticket (внутр. тикеты ИТ), BusinessQuery/Question/Submission
                          (опросники A/B для бизнеса без аккаунта, /bq/<token>/)
apps/web/           — Django Templates frontend
  views/auth.py, campaigns.py, deals.py, platforms.py,
  profiles.py, billing.py, catalog.py, admin_panel.py,
  notifications.py, analytics.py, cpa.py, pages.py, permits.py,
  business_queries.py
```

## URL namespace: `web:`
Все URL в шаблонах: `{% url 'web:landing' %}` (НЕ `web:home`)

## Что реализовано (Sprint 11 — последний)
- Auth, профили, площадки, кампании, отклики, сделки
- Биллинг (эскроу, вывод, test balance grant)
- Каталог блогеров + DirectOffer
- In-app уведомления
- Отзывы + аналитика + чат + согласование креатива
- CPA-модель (TrackingLink, ClickLog, Conversion)
- Quality: Celery VPS, rate limiting, пагинация, views refactor
- Legal: PermitDocument (ЗРУ-701), retention fields, terms/oferta страницы
- Smoke-тесты по ролям (520 тестов)
- business_queries: внутренние тикеты ИТ + опросники A/B для бизнеса без аккаунта

## VPS
IP, пароли, логины и всё остальное про текущий сервер — **только** в `key_param.txt`
(в репозиторий не попадает, см. `.gitignore`). Это правило без исключений: ни IP, ни
пароли, ни логины не должны попадать ни в один файл, который коммитится в git — включая
CLAUDE.md, `docs/DEPLOY.md` и любые другие доки. В `docs/DEPLOY.md` — только сама процедура
деплоя (шаги, команды, известные проблемы), с плейсхолдерами вида `SERVER_IP` вместо
реальных значений.

## Деплой (обновление текущего сервера)
Процедура — в `docs/DEPLOY.md` (раздел «Деплой обновлений»); реальные IP и пароль — из
`key_param.txt`, подставлять только локально при запуске команды, не в файлы репозитория.
