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

### Round-robin по Django-группе — всегда с запасным вариантом в очереди
Если что-то назначается автоматически по членству в группе (`assign_reviewer`
и т.п.), пустая группа — не гипотетический случай, а то, что реально
случилось на проде 19.09.2026: `assign_reviewer()` вернул `None`, заявки
остались с `assigned_to=NULL` и не показывались никому и никогда (см.
openspec/changes/archive/2026-09-20-fix-registration-reviewer-and-deploy-gaps).
Очередь для такой сущности обязана иметь запасной вариант — показывать
неназначенные записи участникам той же группы, а не только `assigned_to=user`
без альтернативы (пример: `_legal_entity_queue` в
`apps/web/views/registration.py`). Плюс — заводить Django system check
(`django.core.checks`, см. `apps/registration/checks.py`), который
предупреждает `manage.py check`/`migrate`, если группа пуста, а не полагаться
на то, что кто-то заметит пустую очередь вручную.

### web/celery/celery-beat — один и тот же образ, проверяй после каждого деплоя
Все три сервиса в `docker-compose*.yml` держат общий тег `image:
mktplace-app:latest` — без него `docker compose build web` тегирует только
web, а celery/celery-beat тихо остаются на старом образе. Один раз это уже
стоило продакшну потерянной Celery-задачи (`send_blogger_sms_credentials`,
19.09.2026, см. тот же openspec change) — новые задачи на старом образе
регистрируются брокером как unregistered и отбрасываются без единой видимой
ошибки. После любого деплоя с новыми Celery-задачами — проверять
`docker logs mktplace-celery-1 | grep "Apply all migrations"`: список
приложений должен совпадать с тем, что видит `web` (см. `docs/DEPLOY.md`,
раздел «Проверка работоспособности»).

### Защита публичных форм и API — IP клиента только через `client_ip()`
IP для лимитов и блокировок берётся функцией `apps.users.security.client_ip(request)`
(заголовок `X-Real-IP`, который nginx каждый раз перезаписывает), а не первым элементом
`X-Forwarded-For`: тот клиент подделывает одним заголовком и обходит лимит
(`apps/web/views/cpa.py` пока берёт именно `X-Forwarded-For`). Доверять `X-Real-IP` можно,
только пока порт приложения не открыт наружу — в `docker-compose.vps.yml` он привязан к
`127.0.0.1`. Публичная форма проверяется через `security.check_public_form()` (лимит по IP →
honeypot `hp_note` → капча Turnstile, если заданы `TURNSTILE_*`) и подключает
`templates/partials/bot_protection.html`; лимиты и блокировки полностью отключаются
`RATELIMIT_ENABLED=False` (в тестах выключены по умолчанию). Блокировка IP — модель
`BlockedIP` + `BlockedIPMiddleware`; автоблок включается `AUTOBLOCK_ENABLED=True`. Детали и
состояние на сервере — `docs/DEPLOY.md`, раздел «Защита от ботов и перегрузки».

## Структура приложений
```
apps/users/         — Auth, роли, is_demo; security.py / throttling.py / blocklist.py /
                       middleware.py — защита от ботов и блок по IP (BlockedIP),
                       welcome-письмо после подтверждения email (tasks.py)
apps/profiles/      — BloggerProfile, AdvertiserProfile
apps/platforms/     — Platform, Category, PermitDocument
apps/campaigns/     — Campaign, Response, DirectOffer
apps/deals/         — Deal, DealStatusLog, Review, ChatMessage
apps/billing/       — Wallet, Transaction, WithdrawalRequest, TestBalanceGrant
apps/notifications/ — Notification, NotificationService
apps/analytics/     — (views в apps/web)
apps/business_queries/ — Ticket (внутр. тикеты ИТ), BusinessQuery/Question/Submission
                          (опросники A/B для бизнеса без аккаунта, /bq/<token>/)
apps/registration/  — LegalEntityApplication, IdentityVerification (OneID),
                       IPApplication — регистрация юрлиц + подтверждение
                       статуса ИП блогера. Ддокс/OneID/SMS — заглушки
                       (apps/registration/services.py), реальных интеграций нет.
apps/web/           — Django Templates frontend
  views/auth.py, campaigns.py, deals.py, platforms.py,
  profiles.py, billing.py, catalog.py, admin_panel.py,
  notifications.py, analytics.py, cpa.py, pages.py, permits.py,
  business_queries.py, registration.py
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
- Защита от ботов: лимиты входа/сброса пароля/публичных форм/API, honeypot, Turnstile (по ключам),
  блок IP вручную и автоматически, лимиты nginx, fail2ban, ufw (см. `docs/DEPLOY.md`)
- business_queries: внутренние тикеты ИТ + опросники A/B для бизнеса без аккаунта
- registration: регистрация юрлиц (ИНН, закрепление за сотрудником, ручной Ддокс,
  одноразовая выдача пароля) + подтверждение личности блогера через OneID и статуса
  ИП по документу. Внешние сервисы (Ддокс/OneID/MyID/SMS) — заглушки до реальных
  доступов; известный открытый пробел — нет способа пополнить кошелёк реальными
  деньгами для не-демо пользователей (см. openspec/changes/archive/2026-09-18-*)
- Реестр юрлиц для админа `/panel/legal-entities/all/` (все заявки, фильтры, выгрузка
  в Excel `?export=xlsx`, карточка `/panel/legal-entities/<pk>/`); личная очередь
  `/panel/legal-entities/` осталась отдельно. Excel — через `openpyxl`, поэтому после
  деплоя образ надо пересобирать; строки в ячейки пишутся как текст (защита от формул
  из публичной формы). См. openspec/changes/archive/2026-09-20-add-legal-entity-registry

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
