#!/usr/bin/env bash
set -e
set +x

python manage.py migrate --no-input
python manage.py initialize_tourney --tab-password $TAB_PASSWORD
