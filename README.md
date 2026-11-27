<div align="center">
<img width="100%" src="https://image.ibb.co/ggYcLw/banner.png" alt="MIT-Tab">

[![CircleCI](https://circleci.com/gh/MIT-Tab/mit-tab/tree/master.svg?style=svg)](https://circleci.com/gh/MIT-Tab/mit-tab/tree/master)
[![codecov](https://codecov.io/gh/MIT-Tab/mit-tab/branch/master/graph/badge.svg)](https://codecov.io/gh/MIT-Tab/mit-tab)
[![Documentation Status](https://readthedocs.org/projects/mit-tab/badge/?version=latest)](https://mit-tab.readthedocs.io/en/latest/?badge=latest)


</div>

[![Deploy to DO](https://www.deploytodo.com/do-btn-blue.svg)](https://cloud.digitalocean.com/apps/new?repo=https://github.com/mit-tab/mit-tab/tree/do-apps)

MIT-Tab is a web application to manage APDA debate tournaments.

## For Tournament Directors & Tab Staff

Looking to learn how to use MIT-Tab to run a tournament? **[Check out the documentation!](https://mit-tab.readthedocs.io/en/latest/)**

The documentation has everything you need to know to run tournaments efficiently, including:
- Setting up your server
- Adding teams, judges, and rooms
- Running preliminary rounds
- Managing outrounds
- And much more

## For Developers

Want to contribute to MIT-Tab? Great! Check out **[CONTRIBUTING.md](CONTRIBUTING.md)** for:
- Development environment setup (macOS, Windows, Linux)
- Code quality guidelines
- Development best practices
- How to submit pull requests

### Quick Start for Developers

Install Rust using rustup, MySQL 8, and Node.js with npm. Create an empty development database and configure the connection before starting MIT-Tab.

```bash
npm ci
export MYSQL_DATABASE=mittab_dev
export MYSQL_HOST=127.0.0.1
export MYSQL_USER=mittab_dev
# Set MYSQL_PASSWORD and TAB_PASSWORD in your local environment.
./bin/dev-server
```

MIT-Tab opens at [http://127.0.0.1:8001](http://127.0.0.1:8001). Sign in as `tab` with the password you configured. Set `TOURNAMENT_NAME=local-test` before initialization to populate the development fixture.

### Technology Stack

- **Backend** uses Rust, Axum, SQLx, and MiniJinja.
- **Frontend** uses HTML, Sass, and JavaScript with Webpack.
- **Storage** uses MySQL and local or S3-compatible database backups.
- **Deployment** uses the repository’s multi-stage Dockerfile.

### Testing

```bash
cargo test --locked --workspace --all-targets --manifest-path rust/Cargo.toml
cargo build --locked --manifest-path rust/Cargo.toml -p mittab-server
uv sync --all-groups
uv run --frozen pytest rust/server/test_django_parity.py
npm run lint
```

The parity suite requires a disposable local MySQL test database and `mysqldump`. It checks the running Rust server and compares selected results with the Django reference. The reference browser suite also requires Chrome and ChromeDriver.

## Production Deployment

The Docker image initializes an empty MySQL database or adopts an existing MIT-Tab schema, then starts `mittab-server`. Supply the existing `MYSQL_*` connection settings and set `TAB_PASSWORD` for new databases. For a remote database, supply a trusted CA as `MYSQL_CA_CERT` or provide a readable PEM file through `MYSQL_SSL_CA`. The startup script also supports the existing DigitalOcean managed-database certificate bootstrap. The Docker image listens on `PORT` (8000 by default) and serves `/healthz` for readiness checks.

`MITTAB_DJANGO_SECRET_KEY` can validate existing Django sessions and staff invitation links during a cutover. Existing accounts and password hashes work after fresh sign-in. The AWS backup and email settings and the Black Rod settings retain their existing environment variable names. Set `SENTRY_DSN` to receive runtime events and traces.

`mittab-server` implements `init`, `migrate`, `serve`, and `export-stats`. The `simulate-rounds` and loopback `load-test` commands run in development and test deployments.

## License

MIT-Tab is open source software. See [LICENSE](LICENSE) for details.
