FROM python:2.7

# install dependenices
RUN apt-get update && apt-get upgrade -y && \
  apt-get install sqlite3 && apt-get install nginx -y

WORKDIR /home/mittab
COPY requirements.txt ./
RUN pip install -r requirements.txt

# setup nginx
COPY nginx.conf ./
RUN ln -s /home/mittab/nginx.conf /etc/nginx/sites-enabled/mittab.org

# setup django
COPY manage.py ./
COPY setup.py ./
COPY ./mittab ./mittab
COPY ./bin ./bin

RUN python manage.py migrate
RUN python manage.py collectstatic --noinput
RUN python manage.py initialize_tourney tournament .


EXPOSE 8000
CMD ["./bin/start"]
