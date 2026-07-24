from pathlib import Path
import json
import os

from dotenv import load_dotenv
import pika

from app.utils.diagnostics import log_event


BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

RABBITMQ_URL = os.getenv("RABBITMQ_URL")

if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL não encontrada no arquivo .env")


CHECKOUT_QUEUE = "checkout_requests"


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

        log_event(
            component="api",
            event="rabbitmq_message_published",
            message="Message published to RabbitMQ",
            correlation_id=payload.get("correlation_id"),
            order_id=payload.get("order_id"),
            queue_name=queue_name,
            published_at_ms=payload.get("published_at_ms"),
    )

    finally:
        connection.close()
