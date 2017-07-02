FROM python:2.7

RUN apt-get update && apt-get install sqlite3

WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY manage.py ./
COPY setup.py ./
COPY ./mittab ./mittab

RUN python manage.py migrate
RUN python manage.py collectstatic --noinput
RUN python manage.py initialize_tourney tournament .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
