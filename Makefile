.PHONY: all production web cmd test tests clean

all: production

production:
	@true

web:
	python mittab/manage.py runserver

tests: test

test:
	python -m pytest tests

clean:
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete
