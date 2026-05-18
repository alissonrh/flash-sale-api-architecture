from pathlib import Path
import json
import os
import time

from dotenv import load_dotenv
import pika
from sqlalchemy import select

from app.db.database import SessionLocal
from app.messaging.rabbitmq import CHECKOUT_QUEUE
from app.models.order import OrderModel
from app.models.product import ProductModel


BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

RABBITMQ_URL = os.getenv("RABBITMQ_URL")

if not RABBITMQ_URL:
    raise ValueError("RABBITMQ_URL não encontrada no arquivo .env")


def process_message(ch, method, properties, body):
    print("\n[worker] Mensagem recebida!")

    payload = json.loads(body)
    print("[worker] Conteúdo:", payload)

    order_id = payload.get("order_id")

    db = SessionLocal()

    try:
        result = db.execute(
            select(OrderModel).where(OrderModel.id == order_id)
        )
        order = result.scalar_one_or_none()

        if order is None:
            print(f"[worker] Pedido {order_id} não encontrado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        print(f"[worker] Processando pedido {order.id}...")

        order.status = "PROCESSING"
        db.commit()

        # Simula um pequeno tempo de processamento
        time.sleep(10)

        product_result = db.execute(
            select(ProductModel).where(ProductModel.id == order.product_id)
        )
        product = product_result.scalar_one_or_none()

        if product is None:
            order.status = "FAILED"
            db.commit()
            print(f"[worker] Produto do pedido {order.id} não encontrado.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if order.quantity > product.stock:
            order.status = "FAILED"
            db.commit()
            print(f"[worker] Estoque insuficiente para o pedido {order.id}.")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        product.stock -= order.quantity
        order.status = "COMPLETED"
        db.commit()

        print(f"[worker] Pedido {order.id} concluído com sucesso.")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        print("[worker] ACK enviado.")

    except Exception as exc:
        db.rollback()
        print(f"[worker] Erro inesperado: {exc}")

        try:
            if "order" in locals() and order is not None:
                order.status = "FAILED"
                db.add(order)
                db.commit()
        except Exception:
            db.rollback()

        ch.basic_ack(delivery_tag=method.delivery_tag)

    finally:
        db.close()


def main():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)

    try:
        channel = connection.channel()

        channel.queue_declare(queue=CHECKOUT_QUEUE, durable=True)
        channel.basic_qos(prefetch_count=1)

        channel.basic_consume(
            queue=CHECKOUT_QUEUE,
            on_message_callback=process_message,
            auto_ack=False,
        )

        print(f"[worker] Aguardando mensagens na fila '{CHECKOUT_QUEUE}'...")
        print("[worker] Para sair, pressione CTRL+C")

        channel.start_consuming()

    except KeyboardInterrupt:
        print("\n[worker] Encerrando worker...")
    finally:
        connection.close()


if __name__ == "__main__":
    main()