#/usr/bin/env bash
set -e
set +x

sudo curl -sL https://deb.nodesource.com/setup_12.x | bash
apt-get install -y nodejs

npm install
./node_modules/.bin/webpack --config webpack.config.js --mode production
python manage.py collectstatic --noinput
