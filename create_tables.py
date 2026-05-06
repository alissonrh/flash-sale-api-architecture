from app.db.database import Base, engine
from app.models.order import OrderModel
from app.models.product import ProductModel

print("Criando tabelas...")
Base.metadata.create_all(bind=engine)
print("Tabelas criadas com sucesso.")