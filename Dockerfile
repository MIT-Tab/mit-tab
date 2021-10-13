FROM python:3.7

# install dependenices
RUN apt-get update && \
  apt-get upgrade -y && \
  apt-get install -y vim default-mysql-client

WORKDIR /var/www/tab

COPY Pipfile ./
COPY Pipfile.lock ./
COPY package.json ./
COPY package-lock.json ./
COPY manage.py ./
COPY setup.py ./
COPY webpack.config.js ./
COPY ./mittab ./mittab
COPY ./bin    ./bin

RUN pip install pipenv
RUN pipenv install --deploy --system

RUN mkdir /var/tmp/django_cache

EXPOSE 8010
CMD ["/var/www/tab/bin/start-server.sh"]
