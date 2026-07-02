from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import Base, engine
from .api import interviews, voice, upload, debug
from . import models  # noqa: F401 — register models


def create_app() -> FastAPI:
    app = FastAPI(title="Offer Master API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # DB init (MVP: create_all; migrate to Alembic when schema stabilizes)
    Base.metadata.create_all(bind=engine)

    # Storage
    storage = Path(settings.STORAGE_DIR)
    storage.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(storage)), name="files")

    app.include_router(interviews.router)
    app.include_router(voice.router)
    app.include_router(upload.router)
    app.include_router(debug.router)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()
