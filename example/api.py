from articles.api import router as articles_router
from ninja import NinjaAPI
from orders.api import router as orders_router

api = NinjaAPI(title="django-harel example", version="1.0")
api.add_router("/articles", articles_router)
api.add_router("/orders", orders_router)
