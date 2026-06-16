#!/bin/sh
set -e

echo "Aguardando PostgreSQL ficar disponível..."

until python -c "from app.db.database import engine; conn = engine.connect(); conn.close(); print('PostgreSQL OK')"
do
  echo "PostgreSQL ainda não está pronto. Tentando novamente em 2 segundos..."
  sleep 2
done

echo "Criando tabelas..."
python create_tables.py

echo "Aplicando migração de orders..."
python migrate_orders_add_status_metadata.py

if [ "$DEV_MODE" = "1" ]; then
  echo "Iniciando API em modo dev com reload..."
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app
else
  echo "Iniciando API..."
  exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --no-access-log
fi
