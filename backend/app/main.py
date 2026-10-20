from fastapi import FastAPI

from .config import Settings


settings = Settings.from_env()
app = FastAPI(title="Private Project Workspace")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
