from app.messaging.rabbitmq import publish_json_message


def main():
    publish_json_message(
        queue_name="checkout_requests",
        payload={
            "order_id": 123,
            "product_id": 1,
            "quantity": 2,
            "status": "PENDING",
        },
    )


if __name__ == "__main__":
    main()