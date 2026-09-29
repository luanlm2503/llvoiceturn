from fastapi import FastAPI

from app import __version__
from app.api import health
from app.config import get_settings
from app.errors import install_error_handlers
from app.log import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    # Chỉ mở trang tài liệu API ở môi trường dev
    app = FastAPI(
        title="LLVoiceTool Proxy",
        version=__version__,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_dev else None,
    )
    install_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
