#!/bin/bash
echo '' >> ~/.bashrc && echo 'source virtualenvwrapper.sh' >> ~/.bashrc
source virtualenvwrapper.sh

mkvirtualenv mittab
workon mittab
pip install -r requirements.txt
python manage.py changepassword tab
python manage.py collectstatic

curl -o /var/www/$(echo $USER)_pythonanywhere_com_wsgi.py https://gist.githubusercontent.com/BenMusch/f3e950298001b2717882a39fc5ca3074/raw/f3a618ca1118771d4529ae55ae8dcee9676363a3/gistfile1.py
