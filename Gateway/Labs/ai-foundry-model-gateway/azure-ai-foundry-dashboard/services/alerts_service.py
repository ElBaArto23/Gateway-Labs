"""
services/alerts_service.py
===========================
Monitoreo proactivo: compara el consumo y la salud actual del gateway
contra políticas configuradas (presupuesto, tasa de éxito) y devuelve
la lista de alertas activas.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .usage_tracker import get_consumption_summary
from .gateway_metrics import get_gateway_telemetry

# Debe coincidir con el límite usado en check_budget() dentro de app.py.
BUDGET_MAX_USD = float(os.environ.get("BUDGET_MAX_USD", "5.0"))
BUDGET_WARNING_RATIO = 0.8  # dispara WARNING al 80% del presupuesto
GATEWAY_SUCCESS_MIN_PCT = 95.0  # dispara WARNING si baja de este umbral


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_alerts(agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Evalúa el estado actual del sistema y devuelve alertas activas.
    """
    alerts: List[Dict[str, Any]] = []

    # --- Presupuesto ---
    consumption = get_consumption_summary(agent_name=agent_name)
    total_cost = consumption.get("total_cost", 0.0)
    pct = (total_cost / BUDGET_MAX_USD * 100) if BUDGET_MAX_USD else 0.0

    if total_cost >= BUDGET_MAX_USD:
        alerts.append({
            "severity": "CRITICAL",
            "message": (
                f"Presupuesto superado: ${total_cost:.4f} de "
                f"${BUDGET_MAX_USD:.2f} USD ({pct:.1f}%)."
            ),
            "triggered_at": _now(),
        })
    elif pct >= BUDGET_WARNING_RATIO * 100:
        alerts.append({
            "severity": "WARNING",
            "message": (
                f"El consumo alcanzó el {pct:.1f}% del presupuesto "
                f"(${total_cost:.4f} de ${BUDGET_MAX_USD:.2f} USD)."
            ),
            "triggered_at": _now(),
        })

    # --- Salud del Gateway ---
    gateway = get_gateway_telemetry()
    success_rate = gateway.get("success_rate_percentage", 100.0)
    total_requests = gateway.get("total_requests", 0)

    if total_requests > 0 and success_rate < GATEWAY_SUCCESS_MIN_PCT:
        alerts.append({
            "severity": "WARNING",
            "message": (
                f"Tasa de éxito del gateway en {success_rate}% "
                f"(por debajo del {GATEWAY_SUCCESS_MIN_PCT}% esperado)."
            ),
            "triggered_at": _now(),
        })

    return alerts