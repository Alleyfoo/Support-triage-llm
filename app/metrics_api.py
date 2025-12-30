from fastapi import APIRouter

from . import metrics

router = APIRouter()


@router.get("/metrics/snapshot")
def metrics_snapshot() -> dict:
    return metrics.snapshot()
