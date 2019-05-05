<div align="center">
<img width="100%" src="https://image.ibb.co/ggYcLw/banner.png" alt="MIT-Tab">

[![CircleCI](https://circleci.com/gh/MIT-Tab/mit-tab.svg?style=svg)](https://circleci.com/gh/MIT-Tab/mit-tab)
[![Coverage Status](https://coveralls.io/repos/github/MIT-Tab/mit-tab/badge.svg?branch=master)](https://coveralls.io/github/MIT-Tab/mit-tab?branch=master)
[![Documentation Status](https://readthedocs.org/projects/mit-tab/badge/?version=latest)](https://mit-tab.readthedocs.io/en/latest/?badge=latest)


</div>

MIT-Tab is a web application to manage APDA debate tournaments.

Looking to learn how to use mit-tab? [Check out the wiki!](https://github.com/jolynch/mit-tab/wiki) It has articles on everything you need to know to run tournaments efficiently.

MIT-Tab can store the following information:

1. Schools
2. Debaters
3. Teams
4. Rooms
5. Scratches

Then you can use this program to actually run your tournament, which means for
each round you:

1. Pair the round
2. Assign judges to the round
3. Let the debaters debate
4. Enter in ballots

At the very end of the tournament you can view the ranking of all teams and all
debaters such that you can pair your elimination rounds accordingly. Since
elimination rounds as well as judge assignment for elimination rounds are much
more up to particuar tournament directors, this program will not do that for
you.

## Local Installation + Running

Currently the installation consists of downloading the code, installing
requirements and then manually running the server.

```
git clone <mit-tab repo> mit-tab
cd mit-tab
virtualenv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py changepassword tab
# Type in the password you want to use while developing
python manage.py runserver
```

## Running with Docker

If you'd like, you can also run the application with Docker. The docker
configuration is meant to simulate a production server, so it's not ideal for
debugging or general development. You must have docker-compose 1.14.0+ installed to run.

To run on localhost
Since it uses nginx, it doesn't need a port in the url. Just go to http://localhost


```
docker-compose build
docker-compose up
```

## Production Setup & Deployment

Deployment to production is controlled by
[benmusch/mittab-deploy](https://github.com/benmusch/mittab-deploy). The
production environment is built using the dockerfiles in this repo, and the
tournaments automatically pull the code that is currently on the master branch.


Older versions of the production setup are documented [here.](mittab/production_setup)
