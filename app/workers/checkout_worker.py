from pathlib import Path
import json
import os
import time
import traceback

from dotenv import load_dotenv
import pika
from sqlalchemy import select

from app.db.database import SessionLocal
from app.messaging.rabbitmq import CHECKOUT_QUEUE
from app.models.order import OrderModel
from app.models.product import ProductModel
from app.utils.datetime import now_utc


BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

RABBITMQ_URL = os.getenv("RABBITMQ_URL")

if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL não encontrada no arquivo .env")


ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_FAILED = "FAILED"
ORDER_STATUS_PROCESSING = "PROCESSING"


def log(message: str):
    if isinstance(message, str) and message.startswith("{"):
        print(message, flush=True)
        return

    print(f"[worker] {message}", flush=True)


def process_message(ch, method, properties, body):
    worker_started_at = time.perf_counter()
    payload = json.loads(body)
    order_id = payload.get("order_id")
    published_at_ms = payload.get("published_at_ms")
    worker_received_at_ms = int(time.time() * 1000)
    queue_wait_ms = None

    if published_at_ms is not None:
        queue_wait_ms = worker_received_at_ms - int(published_at_ms)

    db = SessionLocal()

    try:
        result = db.execute(
            select(OrderModel).where(OrderModel.id == order_id)
        )
        order = result.scalar_one_or_none()

        if order is None:
            log(f"Pedido {order_id} não encontrado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if order.status == ORDER_STATUS_COMPLETED:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if order.status == ORDER_STATUS_FAILED:
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        order.status = ORDER_STATUS_PROCESSING
        order.updated_at = now_utc()
        order.failure_reason = None
        db.commit()

        product_result = db.execute(
            select(ProductModel).where(ProductModel.id == order.product_id)
        )
        product = product_result.scalar_one_or_none()

        if product is None:
            order.status = ORDER_STATUS_FAILED
            order.updated_at = now_utc()
            order.processed_at = now_utc()
            order.failure_reason = "Produto não encontrado no momento do processamento"
            db.commit()

            log(f"Pedido {order.id} falhou: produto não encontrado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if order.quantity > product.stock:
            order.status = ORDER_STATUS_FAILED
            order.updated_at = now_utc()
            order.processed_at = now_utc()
            order.failure_reason = "Estoque insuficiente no momento do processamento"
            db.commit()

            log(f"Pedido {order.id} falhou: estoque insuficiente.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        product.stock -= order.quantity
        order.status = ORDER_STATUS_COMPLETED
        order.updated_at = now_utc()
        order.processed_at = now_utc()
        order.failure_reason = None
        db.commit()

        log(f"Pedido {order.id} concluído.")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as exc:
        db.rollback()
        log(f"Erro no processamento do pedido {order_id}: {exc}")
        traceback.print_exc()

        try:
            if "order" in locals() and order is not None:
                order.status = ORDER_STATUS_FAILED
                order.updated_at = now_utc()
                order.processed_at = now_utc()
                order.failure_reason = f"Erro inesperado no worker: {exc}"
                db.add(order)
                db.commit()
        except Exception as internal_exc:
            db.rollback()
            log(f"Erro ao salvar FAILED no pedido {order_id}: {internal_exc}")
            traceback.print_exc()

        ch.basic_ack(delivery_tag=method.delivery_tag)

    finally:
        final_status = None
        if "order" in locals() and order is not None:
            final_status = order.status

        log(
            json.dumps(
                {
                    "event": "worker_process_message",
                    "order_id": order_id,
                    "queue_wait_ms": queue_wait_ms,
                    "worker_total_ms": round(
                        (time.perf_counter() - worker_started_at) * 1000,
                        3,
                    ),
                    "final_status": final_status,
                },
                ensure_ascii=False,
            )
        )
        db.close()


def main():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = None

    try:
        connection = pika.BlockingConnection(params)
        channel = connection.channel()

        channel.queue_declare(queue=CHECKOUT_QUEUE, durable=True)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(
            queue=CHECKOUT_QUEUE,
            on_message_callback=process_message,
            auto_ack=False,
        )

        log(f"Consumindo fila '{CHECKOUT_QUEUE}'...")
        channel.start_consuming()

    except KeyboardInterrupt:
        log("Worker interrompido manualmente.")

    except Exception as exc:
        log(f"Erro fatal no worker: {exc}")
        traceback.print_exc()
        raise

    finally:
        try:
            if connection and connection.is_open:
                connection.close()
        except Exception as exc:
            log(f"Erro ao fechar conexão com RabbitMQ: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
