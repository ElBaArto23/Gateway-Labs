"""
services/dashboard_service.py
==============================
Unifica todas las fuentes de datos (costos, uso, gateway, métricas)
para alimentar paneles de control (Dashboards).
"""

from typing import Any, Dict, Optional
from .usage_tracker import get_consumption_summary
from .gateway_metrics import get_gateway_telemetry
from .metrics_collector import get_aggregated_metrics


def get_dashboard_summary(agent_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Construye la vista de datos consolidados para el dashboard principal.
    """
    consumption = get_consumption_summary(agent_name=agent_name)
    gateway = get_gateway_telemetry()
    metrics = get_aggregated_metrics(since_minutes=60)

    return {
        "overview": {
            "total_cost_usd": consumption.get("total_cost", 0.0),
            "total_tokens": consumption.get("total_tokens", 0),
            "total_calls": consumption.get("call_count", 0),
            "gateway_success_rate": gateway.get("success_rate_percentage", 100.0),
            "avg_latency_ms": gateway.get("avg_total_latency_ms", 0.0),
        },
        "consumption_details": consumption,
        "gateway_health": gateway,
        "system_metrics": metrics,
    }