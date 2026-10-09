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

### Статус сделки — только через `apps/deals/services.py`
Сайт, API, таймеры Celery и панель не меняют `deal.status` сами, а вызывают переходы из `apps/deals/services.py`
(`submit_creative`, `approve_creative`, `reject_creative`, `submit_publication`, `complete`, `open_dispute`,
`resolve_dispute`, `cancel`, таймеры `auto_*`). Внутри каждого перехода: `atomic` + `select_for_update`, проверка стороны и
статуса (`TransitionError` с текстом для пользователя), `DealStatusLog.log()` ДО смены статуса (помощник `_move`),
деньги через `BillingService`, системное сообщение в чат, уведомления. Сделка создаётся только `create_deal`
(через `apps/campaigns/services.accept_response` / `accept_direct_offer`). Новый переход — новая функция там же.

### Кампания: срок и деньги
Срок кампании — `apps/campaigns/services.py` (`expired_error`, таймер `auto_complete_expired_campaigns` раз в час:
ACTIVE/PAUSED после `end_date` → COMPLETED, ожидающие отклики и предложения → EXPIRED). Лимит блогеров, бюджет и срок
при создании сделки — одна функция `deal_acceptance_error`. Деньги двигаются только через `BillingService` (строго,
без `max(0)`; выплата вывода — `complete_withdrawal`, тип PAYOUT); в Django admin денежные и статусные поля — только чтение.

### Креатив сделки — одобрение ведёт в WAITING_PUBLICATION, повторной отправки нет
Одобрение креатива (веб `deal_approve_creative`, DRF `approve-creative`, Celery `auto_approve_creative`)
переводит сделку ON_APPROVAL → WAITING_PUBLICATION. Признак «креатив одобрен» — поле
`creative_approved_at`, а не статус: при заполненном поле отправка креатива отклоняется (так закрыты и
старые сделки, одобренные в IN_PROGRESS). Публикация принимается из IN_PROGRESS (креатив пропущен) и
WAITING_PUBLICATION; рекламодатель отменяет сделку из WAITING_PAYMENT / IN_PROGRESS / WAITING_PUBLICATION,
блогер — только из WAITING_PAYMENT.

### Денежные итоги — только через `apps/billing/metrics.py`
`BillingService` пишет списания (`PAYMENT`, `RESERVE`, `WITHDRAWAL`) с минусом, зачисления — с плюсом; отдельной
транзакции «комиссия» нет (комиссия = |PAYMENT| − EARNING). Доход платформы, оборот, траты, заработок и топы считаются
функциями `metrics` (сумма по модулю), а не своим `Sum("amount")` во view — так 06.10.2026 доход админа показывал
−1.85 × оборот. В тестах деньги проводить через `BillingService`, а не создавать `Transaction` вручную со своим знаком.

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
ошибки. После любого деплоя — проверять, что у web, celery и celery-beat один и тот же
образ: `docker inspect -f '{{.Image}}' mktplace-web-1 mktplace-celery-1 mktplace-celery-beat-1`
(три одинаковых id) и `docker exec mktplace-celery-1 celery -A config inspect registered`
(новые задачи в списке) — см. `docs/DEPLOY.md`, раздел «Проверка работоспособности».
`migrate` и `collectstatic` выполняет только web: у celery/celery-beat в `docker-compose.vps.yml`
задан `SKIP_MIGRATIONS=1` (иначе при выпуске с новой миграцией они гоняются с web и один
падает с `DuplicateTable`).

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
- Уведомления кликабельны: у `Notification` есть поле `url`, `NotificationService.notify(..., url=...)`
  сохраняет цель (заявка юрлица → `/panel/legal-entities/<pk>/`, кампания, кошелёк и т.д.),
  свойство `target_url` даёт запасной адрес для старых записей. Новый тип уведомления —
  передавать `url` через `reverse("web:...")`. Гасить уведомление во view не нужно: `NotificationResolveMiddleware`
  отмечает прочитанными уведомления пользователя, когда он открыл их страницу. Цели: `url`, страница сделки
  (`related_deal`) и страница кампании (`related_campaign`, параметр `campaign=` в `notify`). Отметка ставится до
  отрисовки, чтобы значок на этой же странице был верным, и откатывается, если ответ не 200. Переход, сделанный
  через API, обязан уведомлять так же, как тот же переход на сайте.
- Форма кампании и модерация: параметры кампании (даты, цена ≤ бюджета, макс. блогеров × цена ≤ бюджета)
  проверяет одна функция `apps/campaigns/validation.py` — и в веб-форме, и в API; суммы вводятся с
  пробелами по разрядам (`SpacedDecimalField`, `static/js/number-input.js`, кнопки −/+ в `partials/number_input.html`),
  выводятся фильтром `money` (`{% load money %}`), в Python-текстах — `apps.billing.formatting.format_money`; не
  `floatformat` и не `:,.0f` (тест-страж `tests_display_consistency`). Форма кампании — `novalidate`, ошибки даёт сервер. Карточка кампании для модератора `/panel/campaigns/<pk>/`, причина отклонения
  обязательна, отклонённую кампанию рекламодатель отправляет повторно. CPA-кампания создаётся через веб.
- Вход юрлица по ИНН: поле «Логин» принимает email или ИНН (9 цифр, пробелы игнорируются). Формат ИНН —
  `apps.registration.validators.validate_inn` (на поле модели, в публичной форме, при выдаче доступа). Аккаунт юрлица
  по-прежнему хранится под служебным email `legal.<ИНН>@ddocs.internal` (формат — только через
  `apps.registration.services.legal_entity_login`), старый логин тоже работает; сотруднику логин показывается
  как ИНН (`User.login_display`) — при выдаче доступа, в очереди, карточке, реестре и Excel.
- Решения бизнеса по кампаниям (04.10.2026): кампанию на паузе можно редактировать — сохранение отправляет её
  на повторную модерацию (`EDITABLE_STATUSES` в `apps/campaigns/validation.py`, веб и API); минимальная цена
  за размещение — `CAMPAIGN_MIN_FIXED_PRICE` (10 000); «Макс. блогеров» не ниже числа занятых мест
  (`deals_in_cap()` — одна функция и для принятия отклика, и для правки); 17 категорий загружены миграцией
  `platforms.0004_business_categories` (общий список для кампаний и площадок).
- Дашборды ведут к действию: плашки — ссылки, блок «Требует действия» у блогера и рекламодателя, строки сделок
  кликабельны целиком, `deal_list?status=active|<статус>`; «Мои отклики» блогера `/my/responses/`
  (`web:my_responses`); в откликах у рекламодателя — данные блогера (ник, соцсеть, подписчики, ER, рейтинг,
  `deals_count`) без N+1; цена отклика вводится с разрядами и разбирается на сервере. Русская подпись статуса
  сделки — `templates/deals/_status_label.html`. Подсветка при наведении — только у `a.card-dark`.
- Правки модератора: в карточке кампании на модерации — «Предложить правки» (`/panel/campaigns/<pk>/propose/`,
  форма кампании в режиме `proposal_mode`). Предложение — `CampaignEditProposal` (одно PENDING на кампанию),
  значения хранятся «сырыми» данными `CampaignForm` и при принятии проходят ту же форму и проверку
  (`apps/web/campaign_proposals.py`). Пока ждём ответа — кампания в MODERATION, одобрить/отклонить нельзя.
  Рекламодатель принял → ACTIVE; отклонил → REJECTED с комментарием модератора как причиной.
- Светлая тема и логотип (04.10.2026): весь сайт светлый в тонах эмблемы (оранжевый `#F97316`, серый текст,
  белые карточки), главная свёрстана по макету из `task/logo/`. Логотип — только через `templates/partials/logo.html`
  (эмблема `static/img/logo.svg` + «UBlogers!», `on_orange=True` для оранжевого фона). Имена классов `card-dark` /
  `input-dark` оставлены, но теперь это светлые стили из `base.html`; в новых шаблонах — `text-gray-*`, а не
  `text-white`/`text-slate-*` (белый текст только на цветных кнопках). Шаблоны сохранять без BOM — он рисует
  пустую полосу над навбаром.
- Увеличение бюджета без модерации (BZ-2, решение бизнеса 07.10.2026): владелец кампании `ACTIVE`/`PAUSED` с
  неистёкшим сроком увеличивает бюджет одной функцией `apps.campaigns.services.increase_budget` (веб и API —
  `POST /campaigns/<pk>/increase-budget/` и `POST /api/v1/campaigns/<pk>/increase-budget/`); статус и
  модерация не меняются, баланс не проверяется (бюджет не замораживается). Снимок `approved_snapshot`
  обновляется тем же бюджетом (`campaign_proposals.mark_approved`), чтобы следующая ре-модерация не показала
  увеличение как непроверенную правку. Уменьшение бюджета — по-прежнему только через обычную правку (пауза →
  правка → модерация, не ниже занятого).
- Окно приёма контента (BZ-1, решение бизнеса 07.10.2026) может целиком лежать до старта кампании — это время на
  подготовку материалов. Правила — `campaign_param_errors` (веб и API): «с» ≤ «до», «до» не позже чем за 5 рабочих
  дней (пн–пт) до конца кампании, даты не в прошлом. Правила «до ≥ начало кампании» больше нет.
- Срок ответа на отклик и прямое предложение (BZ-3): 7 дней (`expires_at`, `RESPONSE_TTL` в `apps/campaigns/models.py`),
  идёт и на паузе. Часовая задача `auto_expire_responses_and_offers` (beat): за сутки — одно напоминание тому, кто
  отвечает (`reminder_sent_at`), после срока — EXPIRED и уведомления обеим сторонам. Просроченное нельзя принять
  даже до срабатывания задачи (`is_overdue` в `accept_response` / `accept_direct_offer`).
- «Завершить кампанию» досрочно (BZ-4): одна `apps.campaigns.services.complete_campaign` для таймера и владельца
  (`actor=`), последствия одинаковые — отклики и предложения EXPIRED, сделки идут, блогерам со сделками «ваша сделка
  продолжается». Досрочно — из ACTIVE, PAUSED и MODERATION с `approved_snapshot` (`Campaign.can_finish_early`),
  отметка `completed_early_at`, ожидающее предложение правок → CLOSED. API: `complete`; `cancel` делает то же,
  статус CANCELLED больше не ставится.

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
