from sqlalchemy import text

from app.db.database import engine


SQL_STATEMENTS = [
    """
    ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
    """,
    """
    ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
    """,
    """
    ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
    """,
    """
    ALTER TABLE public.orders
    ADD COLUMN IF NOT EXISTS failure_reason TEXT;
    """,
    """
    UPDATE public.orders
    SET created_at = NOW()
    WHERE created_at IS NULL;
    """,
    """
    UPDATE public.orders
    SET updated_at = NOW()
    WHERE updated_at IS NULL;
    """,
]


def main():
    with engine.begin() as connection:
        for statement in SQL_STATEMENTS:
            connection.execute(text(statement))

    print("Migração concluída com sucesso.")


if __name__ == "__main__":
    main()