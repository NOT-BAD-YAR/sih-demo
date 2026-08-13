"""FastAPI application — Phase 6 API layer.

Wires the Phase 6 routers, CORS for the Phase 7 dashboard, and exposes the
OpenAPI schema at `/docs`.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import admin, alerts, auth, entities, incidents, overview

app = FastAPI(
    title="Insider-Threat UEBA API",
    version="0.6.0",
    description=(
        "Behavioral analytics combining statistical baselines, ML anomaly "
        "detection, contextual rules, and event correlation. Response actions "
        "are recommended / simulated."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only — Phase 7 dashboard origin is locked down
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, overview, entities, alerts, incidents, admin):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness probe for the Docker healthcheck."""
    return {"status": "ok"}