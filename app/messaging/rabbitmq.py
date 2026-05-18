from pathlib import Path
import json
import os

from dotenv import load_dotenv
import pika


BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

RABBITMQ_URL = os.getenv("RABBITMQ_URL")

if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL não encontrada no arquivo .env")


def publish_json_message(queue_name: str, payload: dict):
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)

    try:
        channel = connection.channel()

        channel.queue_declare(queue=queue_name, durable=True)

        message_body = json.dumps(payload)

        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=message_body,
            properties=pika.BasicProperties(
                delivery_mode=2
            ),
        )

        print(f"Mensagem publicada na fila '{queue_name}': {message_body}")

    finally:
        connection.close()