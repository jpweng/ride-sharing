"""GET /api/health -- the health check (the container's, deploy/compose.yaml, and the
monitoring's, infra/monitoring.tf)."""
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}
