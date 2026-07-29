from pathlib import Path
import json
import os
import time
import traceback

from dotenv import load_dotenv
import pika
from sqlalchemy import select
from opentelemetry import trace

from app.db.database import SessionLocal, engine
from app.messaging.rabbitmq import CHECKOUT_QUEUE
from app.models.order import OrderModel
from app.models.product import ProductModel
from app.utils.diagnostics import log_event
from app.utils.datetime import now_utc
from app.observability.tracing import (
    configure_tracing,
    instrument_pika,
    instrument_sqlalchemy,
)


BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

RABBITMQ_URL = os.getenv("RABBITMQ_URL")

if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL não encontrada no arquivo .env")

configure_tracing()
instrument_sqlalchemy(engine)
instrument_pika()

tracer = trace.get_tracer(__name__)

ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_FAILED = "FAILED"
ORDER_STATUS_PROCESSING = "PROCESSING"


def log(message: str):
    print(f"[worker] {message}", flush=True)

@tracer.start_as_current_span("worker.process_checkout")
def process_message(ch, method, properties, body):
    worker_started_at = time.perf_counter()
    payload = json.loads(body)
    order_id = payload.get("order_id")
    correlation_id = payload.get("correlation_id")
    published_at_ms = payload.get("published_at_ms")
    worker_received_at_ms = int(time.time() * 1000)
    queue_wait_ms = None
    final_status = None

    if published_at_ms is not None:
        queue_wait_ms = worker_received_at_ms - int(published_at_ms)

    current_span = trace.get_current_span()

    if order_id is not None:
        current_span.set_attribute("order.id", order_id)

    if correlation_id is not None:
        current_span.set_attribute("order.correlation_id", correlation_id)

    if queue_wait_ms is not None:
        current_span.set_attribute("messaging.queue_wait_ms", queue_wait_ms)

    log_event(
        component="worker",
        event="worker_message_consumed",
        message="Checkout message consumed from RabbitMQ",
        correlation_id=correlation_id,
        order_id=order_id,
        queue_wait_ms=queue_wait_ms,
    )

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
        failure_reason = None

        if "order" in locals() and order is not None:
            final_status = order.status
            failure_reason = order.failure_reason

        if final_status is not None:
            current_span.set_attribute("order.final_status", final_status)

        if failure_reason:
            current_span.set_attribute("order.failure_reason", failure_reason)

        log_event(
            component="worker",
            event="worker_process_message",
            message="Checkout message processing finished",
            level=(
                "ERROR"
                if final_status in {None, ORDER_STATUS_FAILED}
                else "INFO"
            ),
            correlation_id=correlation_id,
            order_id=order_id,
            queue_wait_ms=queue_wait_ms,
            worker_total_ms=round(
                (time.perf_counter() - worker_started_at) * 1000,
                3,
            ),
            final_status=final_status,
            failure_reason=failure_reason,
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
