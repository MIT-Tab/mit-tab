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

```bash
# Clone the repository
git clone https://github.com/MIT-Tab/mit-tab.git
cd mit-tab

# Follow the setup guide for your platform in CONTRIBUTING.md
```

### Technology Stack

- **Backend**: Django (Python)
- **Frontend**: JavaScript with Webpack
- **Database**: MySQL
- **Deployment**: Docker

### Testing

Tests require Chrome's headless driver in your `$PATH`. [Info here](http://chromedriver.chromium.org/getting-started)

## Production Deployment

Production deployment is managed through [benmusch/mittab-deploy](https://github.com/benmusch/mittab-deploy).

The production environment uses the Dockerfiles in this repository, and tournaments automatically pull code from the master branch.

Legacy production setup documentation is available in the [`mittab/production_setup`](mittab/production_setup) directory.

## License

MIT-Tab is open source software. See [LICENSE](LICENSE) for details.
