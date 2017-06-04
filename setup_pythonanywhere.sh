#!/bin/bash
# sets up a server on PythonAnywhere

echo $1
if [ -z $1 ]; then
  WSGI="/var/www/${USER}_pythonanywhere_com_wsgi.py"
else
  BASE=$1
  WSGI="/var/www/${BASE//./_}_wsgi.py"
fi

BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${BLUE}STEP 1: Installing dependencies${NC}"

echo '' >> ~/.bashrc && echo 'source virtualenvwrapper.sh' >> ~/.bashrc
source virtualenvwrapper.sh

mkvirtualenv mittab
workon mittab
pip install -r requirements.txt

echo -e "${BLUE}STEP 2: Set your password${NC}"
python manage.py changepassword tab

echo -e "${BLUE}STEP 3: Collecting HTML, CSS and JS files${NC}"
python manage.py collectstatic

# Sets up WSGI with this gist:
# https://gist.github.com/BenMusch/f3e950298001b2717882a39fc5ca3074
# If you wish to change the WSGI, change the Gist then update the link
echo -e "${BLUE}STEP 4: Configuring server${NC}"
curl -o $WSGI https://gist.githubusercontent.com/BenMusch/f3e950298001b2717882a39fc5ca3074/raw/f97ba6cb20946b7cf21916be3f90b43d333874ca/gistfile1.py
echo -e "${GREEN}Success! Read this document to see how to finish the process:${NC}"
echo -e "${BLUE}https://docs.google.com/document/d/1j4r8UwtJqeQBz2rhNAZo1ITt40DaDe0cz6G2iiTz414/edit${NC}"
