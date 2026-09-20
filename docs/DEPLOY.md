# DEPLOY.md — Руководство по развёртыванию Mktplace на новом VPS

> Актуально на 2026-04-26. Обновлять при каждой смене сервера.

---

## Быстрый старт (кратко)

```bash
# 1. Клонировать репо
git clone https://github.com/iRatG/mktplace.git /opt/mktplace

# 2. Установить Docker
curl -fsSL https://get.docker.com | bash
systemctl enable docker && systemctl start docker

# 3. Настроить MTU (ОБЯЗАТЕЛЬНО, иначе apt/pip не работают)
echo '{"mtu": 1450}' > /etc/docker/daemon.json
systemctl restart docker

# 4. Настроить git для HTTP/1.1 (иначе git pull падает)
git config --global http.version HTTP/1.1

# 5. Скопировать .env.prod
# С Windows: pscp /.env.prod root@NEW_IP:/opt/mktplace/.env.prod

# 6. Собрать и запустить
cd /opt/mktplace
docker compose -f docker-compose.vps.yml build web
docker compose -f docker-compose.vps.yml up -d

# 7. Инициализация данных
docker compose -f docker-compose.vps.yml run --rm web python manage.py migrate
docker compose -f docker-compose.vps.yml run --rm web python manage.py create_demo_users --reset
docker compose -f docker-compose.vps.yml run --rm web python manage.py seed_demo_data

# 8. Добавить swap (если RAM <= 2GB)
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Приложение слушает `127.0.0.1:8080` (порт наружу не опубликован); в интернет сайт отдаёт хостовой
nginx (TLS, домен). Проверка на самом сервере:
`curl -H 'Host: ublogers.uz' http://127.0.0.1:8080/login/`

---

## История серверов

IP, имена и пароли серверов — только в `key_param.txt` (не в git). Здесь — только даты и
причины смены, для понимания истории:

| Дата | Статус | Причина смены |
|---|---|---|
| 2026-02-21 | УДАЛЁН 2026-04-25 | Не оплачен |
| 2026-04-26 | УДАЛЁН 2026-06-26 | Не оплачен |
| 2026-06-26 | АКТИВЕН (текущий) | — |

---

## Подробная инструкция

### Шаг 1 — Получить новый сервер

Рекомендуемые параметры:
- **ОС:** Ubuntu 22.04 или 24.04 LTS
- **RAM:** минимум 1 GB (рекомендуется 2 GB)
- **CPU:** 1–2 vCPU
- **Диск:** 10–20 GB

Провайдер: reg.ru (раздел "Облачные серверы")

Сохранить в `key_param` файл:
```
IP: XX.XX.XX.XX
Login: root
Password: XXXX
```

### Шаг 2 — Первое подключение по SSH

**С Windows через plink:**
```bash
# Первый раз — plink попросит подтвердить host key
# Лучше использовать paramiko (Python 3.7):
/c/Python/Python37/python.exe -c "
import paramiko, warnings
warnings.filterwarnings('ignore')
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('IP', username='root', password='PASS', timeout=15)
stdin, stdout, stderr = c.exec_command('echo connected && uname -a')
print(stdout.read().decode())
c.close()
"
```

**ВАЖНО: особенности SSH с Windows в этом проекте:**
- `plink` версии 0.83 НЕ имеет флага `-acceptnew`
- `echo "y" | plink` не работает в фоновом режиме
- `sshpass` не установлен на этой машине
- `python3` = Windows Store Python 3.14.3 (без пакетов!) — НЕ ИСПОЛЬЗОВАТЬ
- Правильный Python: `/c/Python/Python37/python.exe` (там есть paramiko)
- Paramiko установлен: `/c/Python/Python37/Scripts/pip install paramiko`

После первого подключения через paramiko — plink запомнит ключ в реестре Windows. Тогда можно использовать plink с флагом `-hostkey`.

**Получить fingerprint хоста:**
```bash
ssh-keyscan -t ed25519 IP 2>/dev/null | ssh-keygen -lf -
```

### Шаг 3 — Установка Docker

```bash
curl -fsSL https://get.docker.com | bash
systemctl enable docker
systemctl start docker
docker --version  # должен быть 24+
```

### Шаг 4 — MTU fix (КРИТИЧНО)

Без этого шага Docker-контейнеры не могут скачать пакеты:

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "mtu": 1450
}
EOF
systemctl restart docker
```

### Шаг 5 — Клонировать репо

```bash
git config --global http.version HTTP/1.1  # без этого git pull падает
git clone https://github.com/iRatG/mktplace.git /opt/mktplace
cd /opt/mktplace
```

### Шаг 6 — Загрузить .env.prod

`.env.prod` НЕ находится в git (секреты). Копировать с рабочей машины:

```bash
# С Windows:
pscp c:/andr/.env.prod root@IP:/opt/mktplace/.env.prod

# Или через paramiko:
/c/Python/Python37/python.exe -c "
import paramiko
transport = paramiko.Transport(('IP', 22))
transport.connect(username='root', password='PASS')
sftp = paramiko.SFTPClient.from_transport(transport)
sftp.put('c:/andr/.env.prod', '/opt/mktplace/.env.prod')
sftp.close(); transport.close()
print('done')
"
```

### Шаг 7 — Собрать и запустить

```bash
cd /opt/mktplace

# Собрать только web: все три Python-сервиса (web/celery/celery-beat) в
# docker-compose.vps.yml делят один тег image: mktplace-app:latest, поэтому
# сборка web тегирует образ и для celery/celery-beat тоже (см. "Частые ошибки"
# ниже — без этого тега это было бы неверно и однажды сломало продакшн).
docker compose -f docker-compose.vps.yml build web

# up -d пересоздаёт все контейнеры, чей образ изменился — то есть все три
docker compose -f docker-compose.vps.yml up -d

# Проверить что всё поднялось
docker ps
```

### Шаг 8 — Инициализация данных

```bash
# Миграции (всегда после первого запуска или git pull с новыми миграциями)
docker compose -f docker-compose.vps.yml run --rm web python manage.py migrate

# Создать demo-пользователей
docker compose -f docker-compose.vps.yml run --rm web python manage.py create_demo_users --reset

# Заполнить демо-данными
docker compose -f docker-compose.vps.yml run --rm web python manage.py seed_demo_data
```

### Шаг 9 — Добавить swap (ОБЯЗАТЕЛЬНО для 1 GB RAM)

Без swap при нехватке RAM Linux убивает контейнеры (OOM killer):

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Проверка:
free -h
# Swap: 2.0Gi должен появиться
```

---

## Деплой обновлений (после git push)

```bash
# С Windows через paramiko:
cmd = "cd /opt/mktplace && git pull && docker compose -f docker-compose.vps.yml build web && docker compose -f docker-compose.vps.yml up -d && docker ps"

# Или через plink (после принятия host key):
plink -ssh -pw "PASS" -hostkey "SHA256:FINGERPRINT" root@IP "cd /opt/mktplace && git pull && docker compose -f docker-compose.vps.yml build web && docker compose -f docker-compose.vps.yml up -d"
```

**Когда `build web` обязателен, а не «на всякий случай»:** любое изменение
`requirements/base.txt` (новая зависимость) применяется только пересборкой образа —
`git pull` + `restart` её не подтянет. Пример: `openpyxl` для выгрузки реестра юрлиц в Excel
(`/panel/legal-entities/all/?export=xlsx`) — без пересборки страница реестра работает, а
выгрузка падает с `ModuleNotFoundError`. Проверка после деплоя:

```bash
docker exec mktplace-web-1 python -c "import openpyxl; print(openpyxl.__version__)"
```

---

## Проверка работоспособности

```bash
# Статус контейнеров
docker ps

# Должно быть 5 контейнеров Up:
# mktplace-web-1        (127.0.0.1:8080)
# mktplace-db-1         (postgres, healthy)
# mktplace-redis-1      (redis, healthy)
# mktplace-celery-1
# mktplace-celery-beat-1

# Ресурсы
free -h
df -h /
docker stats --no-stream

# Логи web
docker logs mktplace-web-1 --tail 50

# Логи celery
docker logs mktplace-celery-1 --tail 20

# ВАЖНО: убедиться, что celery не отстал от web по коду. При старте celery
# печатает список установленных приложений при миграции — он должен включать
# ВСЕ текущие Django-приложения (в частности registration, business_queries).
# Если каких-то новых приложений в этом списке нет — образ celery устарел,
# несмотря на общий тег image: (см. "Частые ошибки" ниже). Один раз это уже
# стоило продакшну молча потерянной Celery-задачи (send_blogger_sms_credentials,
# 19.09.2026) — задачи новых приложений на старом образе не регистрируются и
# отбрасываются брокером без единой видимой пользователю ошибки.
docker logs mktplace-celery-1 2>&1 | grep -A3 "Apply all migrations"
```

Сайт: `https://ublogers.uz` (хостовой nginx → `127.0.0.1:8080`)

Демо-логины: см. `key_param` файл (не в git).

---

## Защита от ботов и перегрузки

Защита состоит из слоёв; каждый работает и без остальных.

| Слой | Где | Что делает |
|---|---|---|
| Лимиты в приложении | `apps/users/security.py`, `apps/users/throttling.py` | попытки входа, сброс пароля, заявки юрлиц и блогеров, API `/api/v1/` |
| Honeypot | `templates/partials/bot_protection.html` | скрытое поле; заполнил — значит бот |
| Капча Turnstile | тот же partial + `TURNSTILE_*` в `.env.prod` | действует только когда заданы ключи |
| Лимиты в nginx | `deploy/nginx/` | отсекают поток запросов до того, как он дойдёт до Django |
| fail2ban | `deploy/fail2ban/` | банит IP, которые упорно упираются в лимиты nginx |
| Блок по IP | `apps/users/blocklist.py`, Django admin → «Заблокированные IP» | 403 на любой запрос с заблокированного адреса: вручную и автоматически (флаг `AUTOBLOCK_ENABLED`) |
| Файрвол | `ufw` на хосте | снаружи открыты только 22, 80, 443 |

Состояние на текущем сервере (20.09.2026): лимиты nginx, джейл fail2ban, `ufw`, привязка порта
приложения к `127.0.0.1` и автоблок IP включены; ключи Turnstile не заданы, капча не активна.

### Лимиты в nginx на VPS

Хостовой nginx (не контейнер) стоит перед приложением и проксирует на `127.0.0.1:8080`.

```bash
cd /opt/mktplace && git pull
cp deploy/nginx/ublogers-ratelimit.conf /etc/nginx/conf.d/
mkdir -p /etc/nginx/snippets
cp deploy/nginx/ublogers-limits.conf /etc/nginx/snippets/
# Сделать резервную копию /etc/nginx/sites-available/ublogers.uz. Затем в
# /etc/nginx/sites-enabled/ublogers.uz внутри server { ... } на 443 добавить строку
# (после server_name):
#     include /etc/nginx/snippets/ublogers-limits.conf;
# Существующий location / оставить как есть: он обслуживает все остальные пути.
# Сниппет добавляет общий лимит на сервер и отдельный location для входа/регистрации.
nginx -t && systemctl reload nginx
```

Порядок важен: `nginx -t` до `reload`. Если проверка не прошла — reload не делать, файл сайта
восстановить из резервной копии.

### Джейл fail2ban

```bash
cp deploy/fail2ban/ublogers-nginx.local /etc/fail2ban/jail.d/
systemctl reload fail2ban
fail2ban-client status nginx-limit-req
```

### Порт приложения и файрвол

Лимиты по IP работают, только если до приложения нельзя достучаться в обход nginx:
`X-Real-IP` приложение берёт на веру. Поэтому в `docker-compose.vps.yml` порт `web`
опубликован только на localhost: `"127.0.0.1:8080:8000"`. Docker публикует порты в обход
`ufw`, так что закрывает порт именно эта привязка, а не правило файрвола.

`ufw` пропускает только SSH, HTTP и HTTPS. Порядок: **сначала** разрешить SSH, потом включать.
Чтобы не потерять доступ, перед `ufw --force enable` запускается страховочный таймер, который
сам выключит файрвол, если не отменить его после проверки входа по SSH новым соединением:

```bash
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
nohup sh -c 'sleep 300; ufw --force disable' >/dev/null 2>&1 & echo $! > /root/ufw_safety.pid
ufw --force enable
# новым SSH-соединением проверить доступ, сайт и связь контейнеров, затем отменить таймер:
kill $(cat /root/ufw_safety.pid) && rm /root/ufw_safety.pid
```

Проверка снаружи: порт 8080 не отвечает приложением, наружу слушают только 22, 80, 443.
Простая проверка «порт принимает соединение» может обманывать (провайдер, антивирус, прокси
принимают любое TCP-соединение), поэтому смотреть нужно на ответ по протоколу.

### Капча Cloudflare Turnstile

Ключи не заданы, капча не активна. Чтобы включить:

1. Cloudflare → Turnstile → Add site (домен `ublogers.uz`), взять Site key и Secret key.
2. Записать в `.env.prod` на сервере `TURNSTILE_SITE_KEY` и `TURNSTILE_SECRET_KEY`
   (в репозиторий не попадают), затем `docker compose -f docker-compose.vps.yml up -d`.
3. Проверить регистрацию юрлица и блогера, сброс пароля: виджет должен появиться,
   отправка без прохождения капчи — отклоняться.

Капча не должна ломать регистрацию: если Cloudflare недоступен из сети сервера или ключи
неверны, проверка пропускается, а в логе `web` появляется запись `Turnstile ... captcha
skipped` (остальная защита продолжает работать). Блокируется только отправка без токена
или с токеном, который Cloudflare отклонил. Вход (`/login/`) капчей не закрыт вовсе.
Откат: очистить `TURNSTILE_*` в `.env.prod` и перезапустить `web`.

### Блок по IP (вручную и автоматически)

**Вручную.** Django admin (`/admin/`) → «Заблокированные IP» → добавить адрес, причину и,
если нужно, срок («Заблокирован до»; пусто — бессрочно). Снять блокировку — удалить запись.
Изменение действует сразу. Залогиненный сотрудник (`is_staff`) не блокируется никогда.
Если админ заблокировал сам себя и не может войти:

```bash
docker compose -f docker-compose.vps.yml run --rm web python manage.py unblock_ip           # показать действующие
docker compose -f docker-compose.vps.yml run --rm web python manage.py unblock_ip 1.2.3.4   # снять
```

**Автоматически (по правилу).** Каждое срабатывание защиты — превышен лимит, сработал
honeypot (весит 5), не пройдена капча — даёт IP «штраф». 10 штрафов за час — блокировка на
24 часа; в админке такая запись помечена «Автоматически», причина содержит последний
сработавший механизм. Приватные и локальные адреса (10.x, 172.16-31.x, 192.168.x, 127.x)
не блокируются никогда, ручную блокировку автоблок не перезаписывает.

Автоблок включается флагом `AUTOBLOCK_ENABLED=True` в `.env.prod` (по умолчанию выключен) и
на текущем сервере включён. Включать его можно только при закрытом порте приложения:
адрес клиента берётся из заголовка `X-Real-IP`, и пока порт открыт наружу, любой может
подставить в заголовок чужой IP и заблокировать его. Проверено: через nginx подделанные
`X-Real-IP` и `X-Forwarded-For` игнорируются. После смены флага:
`docker compose -f docker-compose.vps.yml up -d --force-recreate web`.

### Приветственное письмо

Отправляется один раз, после подтверждения email (`templates/emails/welcome.html` и
`welcome.txt`, задача `send_welcome_email`). В теле нет ссылок; вкладка «FAQ» выделена
жирным. Получают его только пользователи с настоящим email: регистрация юрлица (нет email)
и блогера по SMS (адрес `@sms.internal`) письма не получают. Сбой очереди (Redis) не мешает
подтверждению email — пишется в лог `web`.

---

## Частые ошибки и решения

### manage.py: No such file or directory
**Причина:** собрали через дефолтный `docker compose build` вместо VPS-версии.
```bash
# НЕПРАВИЛЬНО:
docker compose build web

# ПРАВИЛЬНО:
docker compose -f docker-compose.vps.yml build web
```

### git pull зависает
```bash
git config --global http.version HTTP/1.1
```

### pip/apt не работают в контейнере
MTU не настроен. Выполнить Шаг 4.

### plink зависает на host key
plink 0.83 не имеет `-acceptnew`. Использовать paramiko (см. Шаг 2).

### OOM: контейнеры падают без ошибок
Нет swap. Выполнить Шаг 9.

### docker compose зависает при build
Длинные операции через plink зависают. Использовать nohup:
```bash
nohup docker compose -f docker-compose.vps.yml build web > /tmp/build.log 2>&1 &
# Следить за логом:
tail -f /tmp/build.log
```

### Не пересобирать все сервисы сразу
```bash
# МЕДЛЕННО и неправильно (3 полных пересборки):
docker compose -f docker-compose.vps.yml build --no-cache web celery celery-beat

# ПРАВИЛЬНО — все три сервиса указывают на один тег image: mktplace-app:latest
# в docker-compose.vps.yml, поэтому сборка web обновляет образ и для
# celery/celery-beat тоже, а up -d пересоздаёт все три контейнера:
docker compose -f docker-compose.vps.yml build web
docker compose -f docker-compose.vps.yml up -d
```

### celery отстал от web (задачи "unregistered", молча теряются)
**Причина (была найдена и исправлена 19.09.2026):** до этой даты у web,
celery и celery-beat не было общего `image:` тега в `docker-compose.vps.yml` —
`docker compose build web` тегировал только образ web, а celery/celery-beat
годами продолжали работать на старом образе. Симптом в логах celery:
```
ERROR: Received unregistered task of type 'apps.some_app.tasks.some_task'.
The message has been ignored and discarded.
```
Теперь у всех трёх сервисов общий тег `image: mktplace-app:latest` — одной
сборки `web` достаточно (см. проверку в разделе «Проверка работоспособности»
выше). Если ошибка появилась снова — проверить, что тег `image:` не потерялся
при правках compose-файла.

---

## Архитектура на VPS

```
Internet
    |
:443 (nginx хоста: TLS, лимиты запросов)
    |
127.0.0.1:8080 (Gunicorn, 1 worker)
    |
Django App (mktplace-web-1)
    |          |           |
PostgreSQL   Redis       Celery worker
(mktplace-   (mktplace-  (mktplace-celery-1)
db-1)        redis-1)    + celery-beat-1
```

Все сервисы в одной Docker-сети. Gunicorn слушает на 0.0.0.0:8000 внутри контейнера, проброшен на хост только как `127.0.0.1:8080`.

---

## Файлы конфигурации

| Файл | Назначение |
|---|---|
| `docker-compose.vps.yml` | Production compose (все 5 сервисов) |
| `docker-compose.yml` | Локальная разработка |
| `.env.prod` | Секреты продакшна (НЕ в git) |
| `.env` | Секреты локальной разработки (НЕ в git) |
| `config/settings/production.py` | Django settings для VPS |
| `entrypoint.sh` | Entrypoint: collectstatic + migrate + gunicorn |

---

## Рекомендуемые характеристики сервера

| Нагрузка | RAM | CPU | Диск |
|---|---|---|---|
| Demo / разработка | 1 GB + 2 GB swap | 1 vCPU | 10 GB |
| Малый продакшн (до 50 пользователей) | 2 GB | 2 vCPU | 20 GB |
| Средний продакшн (50-500 пользователей) | 4 GB | 2-4 vCPU | 40 GB |
