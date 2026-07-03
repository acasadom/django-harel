from articles.api import router as articles_router
from ninja import NinjaAPI

api = NinjaAPI(title="django-harel example", version="1.0")
api.add_router("/articles", articles_router)
