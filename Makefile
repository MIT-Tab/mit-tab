.PHONY: all production web cmd test tests clean

export DJANGO_SETTINGS_MODULE := mittab.settings

all: production

production:
	@true

web:
	python manage.py runserver

tests: test

test:
	./bin/setup testpassword
	python manage.py test

shell:
	python manage.py shell

clean:
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete

dev_server:
	DEBUG=1 python manage.py runserver
