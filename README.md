<div align="center">
<img width="100%" src="https://image.ibb.co/ggYcLw/banner.png" alt="MIT-Tab">

[![CircleCI](https://circleci.com/gh/MIT-Tab/mit-tab/tree/master.svg?style=svg)](https://circleci.com/gh/MIT-Tab/mit-tab/tree/master)
[![codecov](https://codecov.io/gh/MIT-Tab/mit-tab/branch/master/graph/badge.svg)](https://codecov.io/gh/MIT-Tab/mit-tab)
[![Documentation Status](https://readthedocs.org/projects/mit-tab/badge/?version=latest)](https://mit-tab.readthedocs.io/en/latest/?badge=latest)


</div>

MIT-Tab is a web application to manage APDA debate tournaments.

Looking to learn how to use mit-tab? [Check out the docs!](mit-tab.readthedocs.io)
It has articles on everything you need to know to run tournaments efficiently.
This README is for people who intend to develop features for the software,
**refer to the documentation linked about for non-development-related
information**

## Local Installation + Running

Currently the installation consists of downloading the code, installing
requirements and then manually running the server.

```
git clone <mit-tab repo>
cd mit-tab

# make sure to use python3 for the virtualenv
virtualenv venv
source venv/bin/activate
pip install -r requirements.txt

# set-up webpack assets (currently migrating from Django default assets to webpack)
npm install

# load test data. username: tab password: password
python manage.py loaddata testing_db

# Simultaneously runs webpack and the python server
./bin/dev-server
```

**Note**: the `bin/dev-server` script is new and not tested on many set-ups. If you
have any issues, you can accomplish the same thing by running:

```
# build the assets:
/node_modules/.bin/webpack --config webpack.config.js

# ...or, instead, open another terminal tab and watch the
# assets to auto-update with changes
/node_modules/.bin/webpack --config webpack.config.js --watch

# run the Django server:
DEBUG=1 python manage.py runserver
```

### Testing

To run the tests, you will need to have chrome's headless driver installed and
in your `$PATH`. [Info here](http://chromedriver.chromium.org/getting-started)

### Optional: Running with Docker

If you'd like, you can also run the application with Docker. The docker
configuration is meant to simulate a production server, so it's not ideal for
debugging or general development. You must have docker-compose 1.14.0+ installed to run.

To run on localhost
Since it uses nginx, it doesn't need a port in the url. Just go to http://localhost


```
docker-compose build
docker-compose up

# sets up the static files
docker-compose run --rm web ./bin/setup password
```

## Production Setup & Deployment

Deployment to production is controlled by
[benmusch/mittab-deploy](https://github.com/benmusch/mittab-deploy). The
production environment is built using the dockerfiles in this repo, and the
tournaments automatically pull the code that is currently on the master branch.


Older versions of the production setup are documented [here.](mittab/production_setup)
