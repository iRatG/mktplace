# ============================================================
#  Mktplace — Developer Commands
# ============================================================

# Local development
up:
	docker-compose up

upd:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

restart:
	docker-compose restart web

logs:
	docker-compose logs -f web

# Django management
migrate:
	docker-compose exec web python manage.py migrate

makemigrations:
	docker-compose exec web python manage.py makemigrations

superuser:
	docker-compose exec web python manage.py createsuperuser

shell:
	docker-compose exec web python manage.py shell

collectstatic:
	docker-compose exec web python manage.py collectstatic --noinput

# Database
dbshell:
	docker-compose exec db psql -U $${POSTGRES_USER} -d $${POSTGRES_DB}

# Production deploy
deploy:
	git pull
	docker-compose -f docker-compose.prod.yml up -d --build
	docker-compose -f docker-compose.prod.yml exec web python manage.py migrate
	docker-compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput

deploy-logs:
	docker-compose -f docker-compose.prod.yml logs -f web

.PHONY: up upd down build restart logs migrate makemigrations superuser shell collectstatic dbshell deploy deploy-logs
