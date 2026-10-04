#!/bin/sh
set -e

# collectstatic и migrate выполняет только web. celery/celery-beat стартуют на том же образе
# одновременно с web; если они тоже делают migrate, при выпуске с новой миграцией второй
# контейнер падает с DuplicateTable (так было 04.10.2026). Им задают SKIP_MIGRATIONS=1.
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    echo "Collecting static files..."
    python manage.py collectstatic --noinput

    echo "Applying migrations..."
    python manage.py migrate --noinput
fi

# If arguments were passed (e.g. `docker compose run --rm web python manage.py ...`),
# execute them directly instead of starting gunicorn.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "Starting gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --worker-class gthread \
    --threads 4 \
    --timeout 120 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile -
