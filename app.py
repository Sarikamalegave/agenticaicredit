# app.py
# --- OTel/Langfuse MUST configure before any agent/workflow import ---
from observe.observability import SPANS  # noqa: F401  (side effect: configures exporters)

# Defensive: server still boots even if health() isn't defined
try:
    from observe.observability import health
except ImportError:
    def health() -> dict:
        return {}

import truststore
truststore.inject_into_ssl()

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes import authin, evaluate

app = FastAPI(title="Mentora QA Engine", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(authin.router, prefix="/api")
app.include_router(evaluate.router, prefix="/api")


@app.get("/health")
def health_check():
    # Reports server status PLUS Langfuse/OTel observability state.
    return {"status": "server running", **health()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)