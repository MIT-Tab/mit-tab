#!/usr/bin/env bash
cd mit-tab
mkvirtualenv mittab
workon mittab
pip install -r requirements.txt
python manage.py changepassword tab
python manage.py collectstatic
