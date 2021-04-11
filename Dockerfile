FROM python:3.7

# install dependenices
RUN apt-get update && \
  apt-get upgrade -y && \
  apt-get install -y vim default-mysql-client

# sets up nodejs to install npm
RUN curl -sL https://deb.nodesource.com/setup_12.x | bash
RUN apt-get install -y nodejs

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
COPY ./assets ./assets

RUN pip install pipenv
RUN pipenv install --deploy --system

RUN mkdir /var/tmp/django_cache

RUN npm install
RUN ./node_modules/.bin/webpack --config webpack.config.js --mode production
RUN python manage.py collectstatic --noinput

EXPOSE 8010
STOPSIGNAL SIGTERM
CMD ["/var/www/tab/bin/start-server.sh"]
