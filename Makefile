.PHONY: all production web cmd test tests clean

export DJANGO_SETTINGS_MODULE := mittab.settings

all: production

production:
	@true

web:
	python manage.py runserver

tests: test

test:
	python manage.py initialize_tourney test .
	python -m pytest mittab

shell:
	python manage.py shell

clean:
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete
