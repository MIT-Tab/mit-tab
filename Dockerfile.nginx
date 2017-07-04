FROM tutum/nginx
RUN apt-get update && apt-get install -y vim
RUN rm /etc/nginx/sites-enabled/default
COPY nginx.conf /etc/nginx/sites-enabled/mittab
