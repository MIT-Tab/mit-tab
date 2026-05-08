FROM python:3.10.16

# install dependenices
ARG DOCTL_VERSION=1.120.0

RUN apt-get update && \
  apt-get install -y vim default-mysql-client openssl curl memcached && \
  rm -rf /var/lib/apt/lists/* && \
  curl -sSL https://github.com/digitalocean/doctl/releases/download/v${DOCTL_VERSION}/doctl-${DOCTL_VERSION}-linux-amd64.tar.gz \
    | tar -xz -C /usr/local/bin doctl

WORKDIR /var/www/tab

COPY pyproject.toml ./
COPY uv.lock ./
COPY package.json ./
COPY package-lock.json ./
COPY manage.py ./
COPY webpack.config.js ./
COPY ./settings ./settings
COPY ./mittab ./mittab
COPY ./bin    ./bin
COPY ./assets ./assets

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT=/usr/local
RUN uv sync --frozen --no-dev --no-group docs

RUN curl -sL https://deb.nodesource.com/setup_20.x | bash
RUN apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

RUN npm ci --no-audit
RUN ./node_modules/.bin/webpack --config webpack.config.js --mode production
RUN python manage.py collectstatic --noinput

RUN mkdir /var/tmp/django_cache

EXPOSE 8000
CMD ["/var/www/tab/bin/start-server.sh"]
