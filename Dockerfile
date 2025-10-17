FROM python:3.10

# install dependenices
RUN apt-get update && \
  apt-get upgrade -y && \
  apt-get install -y vim default-mysql-client openssl

WORKDIR /var/www/tab

COPY Pipfile ./
COPY Pipfile.lock ./
COPY package.json ./
COPY package-lock.json ./
COPY manage.py ./
COPY setup.py ./
COPY webpack.config.js ./
COPY ./settings ./settings
COPY ./mittab ./mittab
COPY ./bin    ./bin
COPY ./assets ./assets

RUN pip install pipenv
RUN pipenv install --deploy --system

RUN curl -sL https://deb.nodesource.com/setup_18.x | bash
RUN apt-get install -y nodejs aptitude
RUN aptitude install -y npm

RUN npm install
RUN ./node_modules/.bin/webpack --config webpack.config.js --mode production
RUN python manage.py collectstatic --noinput

RUN mkdir /var/tmp/django_cache

EXPOSE 8000
CMD ["/var/www/tab/bin/start-server.sh"]
