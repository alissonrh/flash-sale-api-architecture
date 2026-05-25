#!/bin/sh
set -e

echo "Aguardando PostgreSQL ficar disponível..."
until python -c "from app.db.database import engine; conn = engine.connect(); conn.close(); print('PostgreSQL OK')"
do
  echo "PostgreSQL ainda não está pronto. Tentando novamente em 2 segundos..."
  sleep 2
done

echo "Aguardando RabbitMQ ficar disponível..."
until python -c "import os, pika; params = pika.URLParameters(os.environ['RABBITMQ_URL']); conn = pika.BlockingConnection(params); conn.close(); print('RabbitMQ OK')"
do
  echo "RabbitMQ ainda não está pronto. Tentando novamente em 2 segundos..."
  sleep 2
done

echo "Iniciando worker..."
exec python -u -m app.workers.checkout_worker