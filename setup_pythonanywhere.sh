#!/bin/bash
echo '' >> ~/.bashrc && echo 'source virtualenvwrapper.sh' >> ~/.bashrc
source virtualenvwrapper.sh

mkvirtualenv mittab
workon mittab
pip install -r requirements.txt
python manage.py changepassword tab
python manage.py collectstatic
