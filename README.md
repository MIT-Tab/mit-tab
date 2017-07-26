[![Build Status](https://travis-ci.org/jolynch/mit-tab.svg?branch=master)](https://travis-ci.org/jolynch/mit-tab)

MIT-TAB
=======
A web application that allows APDA american debate tournaments to correctly tab
and manage their tournaments. You add the following information:

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

Installation + Running
----------------------
Currently the installation consists of downloading the code, installing
requirements and then manually running the server. So:
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

At this time it is recommended to run v2.0 (Django 1.6)


Running with Docker
-------------------
If you'd like, you can also run the application with Docker. The docker
configuration is meant to simulate a production server, so it's not ideal for
debugging or general development. You must have docker-compose 1.14.0+ installed to run.

To run on localhost
Since it uses nginx, it doesn't need a port in the url. Just go to http://localhost


```
docker-compose build
docker-compose up
```

With a specified password for the tab user:

```
docker-compose build --build-arg password={password} web
docker-compose build nginx
docker-compose up
```

The `build` command will automatically run migrations and other commands used to
set up a production environment (i.e. `collectstatic`)

Production Setup & Deployment
-----------------------------

Deployment to production is controlled by
[benmusch/mittab-deploy](https://github.com/benmusch/mittab-deploy). The
production environment is built using the dockerfiles in this repo, and the
tournaments automatically pull the code that is currently on the master branch.


Older versions of the production setup are documented [here.](mittab/production_setup)

Why Should I Use This?
----------------------
There are a few other tab programs available, many of them free, many of them
decent, that can tabulate APDA style tournaments. The main differences between
all of the other programs and this one are:
* This is an open source project that anyone can see and change
* The algorithm for tabulation is much better (minimum weight perfect matching),
which means that your tab will be much more fair and as optimal as possible.
* The web interface means that you don't have to install any software locally
* The interface is generally pretty straight forward to use
* MIT-TAB has chosen simplicity over configurability in the general case, unlike
a program like TRPC, MIT-TAB forces you to use standard tabbing practices so that
tournament experiences are more uniform.

Batch Data Import
-----------------
For file import, structure seperate .xlsx spreadsheets with columns as follows:

Teams:

1. Team's Name
2. Team's School. If the team is a hybrid, use the school they're protected from
3. Team Seed - "Full", "Half", "Free", or leave blank for Unseeded
4. Debater 1 Name - If the team is an ironman, place the debater's info here
5. Debater 1 Varsity/Novice - "Novice", "Nov", or "N", to mark debater as novice.
Otherwise they will be marked as varsity
6. Debater 1 Phone Number - can be blank
7. Debater 1 Provider - can be blank
8. Debater 2 Name - if ironman, leave blank
9. Debater 2 Varsity/Novice
10. Debater 2 Phone
11. Debater 2 Provider

Judges:

1. Judge's Name
2. Judge's Rank (Decimial Value from 0-100)
3. Judge's Phone Number (can be blank)
4. Judge's Provider (can be blank)
5. Affiliated schools, one per column, using as many additional columns as needed

Rooms:

1. Room Name
2. Room Rank (Decimal Value from 0-100)


* The first row is assumed to be a header row - it will not be read
