"""
services/gateway_metrics.py
============================
Telemetría específica del Gateway (APIM + AI Services).
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

GATEWAY_LOG_PATH = os.environ.get("GATEWAY_LOG_PATH", "gateway_log.jsonl")
_lock = threading.Lock()


def record_gateway_event(
    request_id: str,
    status_code: int,
    gateway_latency_ms: float,
    backend_latency_ms: float,
    endpoint: str,
    model_used: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Registra el comportamiento de tráfico a nivel de pasarela.
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "status_code": status_code,
        "gateway_latency_ms": gateway_latency_ms,
        "backend_latency_ms": backend_latency_ms,
        "total_latency_ms": gateway_latency_ms + backend_latency_ms,
        "endpoint": endpoint,
        "model": model_used,
    }

    with _lock:
        with open(GATEWAY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    return event


def get_gateway_telemetry() -> Dict[str, Any]:
    """
    Resumen de salud y performance del API Gateway.
    """
    if not os.path.exists(GATEWAY_LOG_PATH):
        return {"total_requests": 0, "success_rate": 100.0, "avg_latency_ms": 0.0}

    total = 0
    errors = 0
    total_latency = 0.0

    with _lock:
        with open(GATEWAY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    if rec.get("status_code", 200) >= 400:
                        errors += 1
                    total_latency += rec.get("total_latency_ms", 0.0)
                except json.JSONDecodeError:
                    continue

    success_rate = round(((total - errors) / total) * 100, 2) if total > 0 else 100.0
    avg_latency = round(total_latency / total, 2) if total > 0 else 0.0

    return {
        "total_requests": total,
        "error_count": errors,
        "success_rate_percentage": success_rate,
        "avg_total_latency_ms": avg_latency,
    }
