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

Сайт будет доступен на `http://SERVER_IP:8080`

---

## История серверов

| Дата | IP | Имя | Статус | Причина смены |
|---|---|---|---|---|
| 2026-02-21 | "."."." | — | УДАЛЁН 2026-04-25 | Не оплачен |
| 2026-04-26 | "."."." | Brown Hydrogenium | АКТИВЕН | Текущий |

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

# ВАЖНО: собирать только web — celery/celery-beat используют тот же образ
docker compose -f docker-compose.vps.yml build web

# Запуск всех контейнеров (web, db, redis, celery, celery-beat)
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

---

## Проверка работоспособности

```bash
# Статус контейнеров
docker ps

# Должно быть 5 контейнеров Up:
# mktplace-web-1        (порт 8080)
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
```

Сайт: `http://SERVER_IP:8080`

Демо-логины:
| Роль | Email | Пароль |
|---|---|---|
| Админ | admin@demo.com | ! |
| Рекламодатель | advertiser@demo.com | ! |
| Блогер | blogger@demo.com | ! |

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

# ПРАВИЛЬНО (celery/celery-beat используют тот же образ что web):
docker compose -f docker-compose.vps.yml build web
```

---

## Архитектура на VPS

```
Internet
    |
:8080 (Gunicorn, 1 worker)
    |
Django App (mktplace-web-1)
    |          |           |
PostgreSQL   Redis       Celery worker
(mktplace-   (mktplace-  (mktplace-celery-1)
db-1)        redis-1)    + celery-beat-1
```

Все сервисы в одной Docker-сети. Gunicorn слушает на 0.0.0.0:8000 внутри контейнера, проброшен на хост :8080.

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
