FROM python:3.12-slim

WORKDIR /app

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    apache2 \
    && rm -rf /var/lib/apt/lists/*

RUN a2enmod proxy proxy_http && \
    sed -i 's/Listen 80/Listen 8080/g' /etc/apache2/ports.conf

COPY requirements.txt /app/
COPY apache2-vhost.conf /app/

RUN pip install --no-cache-dir --root-user-action=ignore --upgrade pip && \
    pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY /react/out /app/html/
COPY /api /app/api/

EXPOSE 8080

ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

CMD ["sh", "-c", "chown www-data:www-data -R /app/html/ && cp /app/apache2-vhost.conf /etc/apache2/sites-available/000-default.conf && service apache2 start && WORKERS=$(( $(nproc) * 2 + 1 )) && exec gunicorn --chdir /app/api --bind 127.0.0.1:8000 --workers $WORKERS --threads 25 --worker-class gthread app:app --timeout 600"]