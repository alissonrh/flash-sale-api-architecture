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
PROCESSING_DELAY_SECONDS = int(os.getenv("WORKER_PROCESSING_DELAY_SECONDS", "2"))

if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL não encontrada no arquivo .env")


ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_PROCESSING = "PROCESSING"
ORDER_STATUS_COMPLETED = "COMPLETED"
ORDER_STATUS_FAILED = "FAILED"


def log(message: str):
    print(f"[worker] {message}", flush=True)


def process_message(ch, method, properties, body):
    payload = json.loads(body)
    order_id = payload.get("order_id")

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
            log(f"Pedido {order.id} já estava COMPLETED.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if order.status == ORDER_STATUS_FAILED:
            log(f"Pedido {order.id} já estava FAILED.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        log(f"Processando pedido {order.id}...")

        order.status = ORDER_STATUS_PROCESSING
        order.updated_at = now_utc()
        order.failure_reason = None
        db.commit()

        time.sleep(PROCESSING_DELAY_SECONDS)

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

            log(f"Produto do pedido {order.id} não encontrado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if order.quantity > product.stock:
            order.status = ORDER_STATUS_FAILED
            order.updated_at = now_utc()
            order.processed_at = now_utc()
            order.failure_reason = "Estoque insuficiente no momento do processamento"
            db.commit()

            log(f"Estoque insuficiente para o pedido {order.id}.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        product.stock -= order.quantity
        order.status = ORDER_STATUS_COMPLETED
        order.updated_at = now_utc()
        order.processed_at = now_utc()
        order.failure_reason = None
        db.commit()

        log(f"Pedido {order.id} concluído com sucesso.")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as exc:
        db.rollback()
        log(f"Erro inesperado no worker durante processamento: {exc}")
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
            log(f"Erro ao salvar FAILED no pedido: {internal_exc}")
            traceback.print_exc()

        ch.basic_ack(delivery_tag=method.delivery_tag)

    finally:
        db.close()


def main():
    log("Entrou no main() do worker")

    params = pika.URLParameters(RABBITMQ_URL)
    connection = None

    try:
        log("Abrindo conexão com RabbitMQ...")
        connection = pika.BlockingConnection(params)

        log("Criando channel...")
        channel = connection.channel()

        log(f"Declarando fila '{CHECKOUT_QUEUE}'...")
        channel.queue_declare(queue=CHECKOUT_QUEUE, durable=True)

        log("Configurando basic_qos(prefetch_count=1)...")
        channel.basic_qos(prefetch_count=1)

        log("Registrando consumer...")
        channel.basic_consume(
            queue=CHECKOUT_QUEUE,
            on_message_callback=process_message,
            auto_ack=False,
        )

        log(f"Aguardando mensagens na fila '{CHECKOUT_QUEUE}'...")
        log("Para sair, pressione CTRL+C")

        channel.start_consuming()

        log("channel.start_consuming() retornou inesperadamente.")

    except KeyboardInterrupt:
        log("Worker interrompido manualmente.")

    except Exception as exc:
        log(f"Erro fatal no main() do worker: {exc}")
        traceback.print_exc()
        raise

    finally:
        try:
            if connection and connection.is_open:
                connection.close()
                log("Conexão com RabbitMQ encerrada.")
        except Exception as exc:
            log(f"Erro ao fechar conexão com RabbitMQ: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()