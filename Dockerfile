# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit
COPY webpack.config.js ./
COPY assets ./assets
RUN npm exec -- webpack --config webpack.config.js --mode production

FROM python:3.10.16 AS collected-static
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && /root/.local/bin/uv export --frozen --no-dev --no-group docs --no-hashes \
       > /tmp/static-requirements.txt \
    && awk '/^(django|asgiref|sqlparse|tzdata)==/' /tmp/static-requirements.txt > /tmp/static-django.txt \
    && /root/.local/bin/uv pip install --system --no-deps -r /tmp/static-django.txt
COPY --from=assets /build/assets ./assets
COPY bin/collect-static.py ./bin/
RUN python bin/collect-static.py assets staticfiles

FROM debian:bookworm-slim AS rust-build
ARG RUST_VERSION=1.100.0
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs \
       | sh -s -- -y --no-modify-path --profile minimal --default-toolchain "$RUST_VERSION"
ENV PATH="/root/.cargo/bin:$PATH"
WORKDIR /build
COPY rust ./rust
COPY mittab/templates ./mittab/templates
COPY settings ./settings
COPY assets/img/favicon.ico ./assets/img/favicon.ico
COPY mittab/apps/tab/config/tournament_todo_steps.yaml ./mittab/apps/tab/config/tournament_todo_steps.yaml
COPY mittab/apps/tab/fixtures/testing_db.json ./mittab/apps/tab/fixtures/testing_db.json
RUN cargo build --release --locked --manifest-path rust/Cargo.toml -p mittab-server

FROM debian:bookworm-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates mariadb-client openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system mittab \
    && useradd --system --gid mittab --home-dir /var/www/tab mittab \
    && install -d -m 0700 -o mittab -g mittab /var/www/tab/tmp /var/www/tab/mittab/backups
WORKDIR /var/www/tab
COPY --from=rust-build /build/rust/target/release/mittab-server /usr/local/bin/mittab-server
COPY --from=collected-static /build/staticfiles ./staticfiles
COPY --from=assets /build/webpack-stats.json ./
COPY rust/server/DJANGO-LICENSE /usr/share/doc/mittab/django-LICENSE
COPY bin/start-server.sh ./bin/start-server.sh
USER mittab
EXPOSE 8000
CMD ["/var/www/tab/bin/start-server.sh"]
