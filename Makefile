.PHONY: all production web cmd test tests clean

export DJANGO_SETTINGS_MODULE := mittab.settings

all: production

production:
	@true

web:
	uv run python manage.py runserver

tests: test

test:
	uv run ./bin/setup password
	uv run pytest --reuse-db mittab/

shell:
	uv run python manage.py shell

clean:
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete
