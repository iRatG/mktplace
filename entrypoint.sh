#!/bin/sh
set -e

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Applying migrations..."
python manage.py migrate --noinput

# If arguments were passed (e.g. `docker compose run --rm web python manage.py ...`),
# execute them directly instead of starting gunicorn.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "Starting gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
