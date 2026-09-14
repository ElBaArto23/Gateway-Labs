"""
services/metrics_collector.py
==============================
Colector de métricas de rendimiento y estado del sistema.
guarda eventos métricos en disco/memoria para agregación posterior.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

METRICS_LOG_PATH = os.environ.get("METRICS_LOG_PATH", "metrics_log.jsonl")
_lock = threading.Lock()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_metric(
    metric_name: str,
    value: float,
    unit: str = "ms",
    tags: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Registra un punto métrico genérico (ej. latencia, tokens/seg, errores).
    """
    metric_entry = {
        "timestamp": _now_utc(),
        "metric": metric_name,
        "value": value,
        "unit": unit,
        "tags": tags or {},
    }

    with _lock:
        with open(METRICS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(metric_entry, ensure_ascii=False) + "\n")

    return metric_entry


def get_aggregated_metrics(since_minutes: int = 60) -> Dict[str, Any]:
    """
    Agrupa métricas registradas en la última ventana de tiempo.
    """
    if not os.path.exists(METRICS_LOG_PATH):
        return {"total_records": 0, "metrics": {}}

    now = datetime.now(timezone.utc)
    metrics_data: Dict[str, List[float]] = {}
    total_records = 0

    with _lock:
        with open(METRICS_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    rec_ts = datetime.fromisoformat(rec["timestamp"])
                    if (now - rec_ts).total_seconds() > (since_minutes * 60):
                        continue

                    m_name = rec["metric"]
                    metrics_data.setdefault(m_name, []).append(float(rec["value"]))
                    total_records += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    summary = {}
    for name, values in metrics_data.items():
        summary[name] = {
            "count": len(values),
            "avg": round(sum(values) / len(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }

    return {"total_records": total_records, "time_window_minutes": since_minutes, "metrics": summary}