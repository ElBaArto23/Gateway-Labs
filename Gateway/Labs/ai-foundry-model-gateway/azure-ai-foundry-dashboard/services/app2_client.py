"""
services/app2_client.py
========================
Cliente HTTP hacia app2 (AI Logistics Assistant / gateway-ai-app-v2).

app2 corre en su propio App Service, con su propio disco -- no puede
compartir los .jsonl locales con app1. En vez de eso, app1 le pide sus
datos por HTTP a los endpoints de solo lectura que ya expone app2:
    /api/consumption      resumen agregado (para tarjetas de totales)
    /api/gateway          telemetría agregada de gateway
    /api/metrics          métricas agregadas por nombre
    /api/usage/records     registros CRUDOS de uso (para gráficas)
    /api/gateway/records   registros CRUDOS de gateway (para gráficas)
    /api/requests         índice de solicitudes
    /api/requests/<id>    detalle de una solicitud

Ninguna función de este módulo lanza excepción hacia arriba: si app2 no
responde (caída, red, timeout), devuelven None, y el caller decide si
sigue mostrando solo los datos de app1 o marca "app2 no disponible".
"""

import os
from typing import Any, Dict, List, Optional

import requests

# URL base de app2, configurable por variable de entorno.
APP2_BASE_URL = os.environ.get("APP2_BASE_URL", "").rstrip("/")
APP2_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("APP2_REQUEST_TIMEOUT_SECONDS", "5"))


def _fetch(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    GET a un endpoint de app2. Devuelve None (no {}) si algo falla, para
    poder distinguir "app2 respondió con datos vacíos" de "app2 no respondió".
    """
    if not APP2_BASE_URL:
        return None

    try:
        resp = requests.get(
            f"{APP2_BASE_URL}{path}",
            params=params,
            timeout=APP2_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


# ---------------------------------------------------------------------------
# Resúmenes agregados (para tarjetas de totales -- /api/consumption, /api/status)
# ---------------------------------------------------------------------------

def get_app2_consumption(agent_name: Optional[str] = None, since: Optional[str] = None) -> Optional[Dict[str, Any]]:
    params = {}
    if agent_name:
        params["agent"] = agent_name
    if since:
        params["since"] = since
    return _fetch("/api/consumption", params=params or None)


def get_app2_gateway_telemetry() -> Optional[Dict[str, Any]]:
    return _fetch("/api/gateway")


def merge_consumption_summaries(app1_summary: Dict[str, Any], app2_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Suma los totales de app1 + app2 en un solo resumen (mismo shape que get_consumption_summary())."""
    if not app2_summary:
        merged = dict(app1_summary)
        merged["app2_available"] = False
        return merged

    merged_by_model: Dict[str, Dict[str, Any]] = {}
    for source in (app1_summary.get("by_model") or {}, app2_summary.get("by_model") or {}):
        for model, bucket in source.items():
            dest = merged_by_model.setdefault(
                model, {"call_count": 0, "total_tokens": 0, "total_cost": 0.0}
            )
            dest["call_count"] += bucket.get("call_count", 0)
            dest["total_tokens"] += bucket.get("total_tokens", 0)
            dest["total_cost"] += bucket.get("total_cost", 0.0)

    for bucket in merged_by_model.values():
        bucket["total_cost"] = round(bucket["total_cost"], 8)
        bucket["average_cost_per_call"] = (
            round(bucket["total_cost"] / bucket["call_count"], 8) if bucket["call_count"] else 0.0
        )

    call_count = app1_summary.get("call_count", 0) + app2_summary.get("call_count", 0)
    total_tokens = app1_summary.get("total_tokens", 0) + app2_summary.get("total_tokens", 0)
    total_cost = round(app1_summary.get("total_cost", 0.0) + app2_summary.get("total_cost", 0.0), 8)

    top_model = None
    if merged_by_model:
        top_model = max(merged_by_model.items(), key=lambda kv: kv[1].get("call_count", 0))[0]

    return {
        "agent_name": app1_summary.get("agent_name"),
        "since": app1_summary.get("since"),
        "call_count": call_count,
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "average_cost_per_call": round(total_cost / call_count, 8) if call_count else 0.0,
        "average_tokens_per_call": round(total_tokens / call_count, 2) if call_count else 0.0,
        "by_model": merged_by_model,
        "top_model": top_model,
        "app2_available": True,
        "app1_call_count": app1_summary.get("call_count", 0),
        "app2_call_count": app2_summary.get("call_count", 0),
    }


# ---------------------------------------------------------------------------
# Registros crudos (para gráficas -- /api/metrics)
# ---------------------------------------------------------------------------

def get_app2_usage_records() -> Optional[List[Dict[str, Any]]]:
    """Registros crudos de usage_log.jsonl de app2, uno por llamada."""
    data = _fetch("/api/usage/records")
    if data is None:
        return None
    return data.get("records", [])


def get_app2_gateway_records() -> Optional[List[Dict[str, Any]]]:
    """Registros crudos de gateway_log.jsonl de app2, uno por llamada."""
    data = _fetch("/api/gateway/records")
    if data is None:
        return None
    return data.get("records", [])


def merge_chart_data(
    local_charts: Dict[str, Any],
    app2_usage_records: Optional[List[Dict[str, Any]]],
    app2_gateway_records: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Fusiona las series de tiempo que ya arma charts_service.generate_chart_data()
    (solo con datos locales de app1) con los registros crudos de app2, para
    que las gráficas del dashboard incluyan ambas apps.
    """
    result = {
        "time_series": {k: list(v) for k, v in local_charts.get("time_series", {}).items()},
        "cost_by_model_pie": {k: list(v) for k, v in local_charts.get("cost_by_model_pie", {}).items()},
        "latency_series": {k: list(v) for k, v in local_charts.get("latency_series", {}).items()},
    }

    # --- Fusionar time_series (costo/tokens por llamada) ---
    ts = result["time_series"]
    points = list(zip(
        ts.get("labels", []),
        ts.get("cost_series", []),
        ts.get("token_series", []),
        ts.get("input_tokens", []),
        ts.get("output_tokens", []),
    ))

    if app2_usage_records:
        for rec in app2_usage_records:
            label = (rec.get("timestamp") or "")[:19].replace("T", " ")
            points.append((
                label,
                rec.get("total_cost", 0.0),
                rec.get("total_tokens", 0),
                rec.get("input_tokens", 0),
                rec.get("output_tokens", 0),
            ))

    points.sort(key=lambda p: p[0])

    if points:
        labels, costs, tokens, input_toks, output_toks = map(list, zip(*points))
    else:
        labels, costs, tokens, input_toks, output_toks = [], [], [], [], []

    result["time_series"] = {
        "labels": labels,
        "cost_series": costs,
        "token_series": tokens,
        "input_tokens": input_toks,
        "output_tokens": output_toks,
    }

    # --- Fusionar cost_by_model_pie ---
    pie = dict(zip(
        local_charts.get("cost_by_model_pie", {}).get("labels", []),
        local_charts.get("cost_by_model_pie", {}).get("series", []),
    ))
    if app2_usage_records:
        for rec in app2_usage_records:
            model = rec.get("model_normalized", rec.get("model", "desconocido"))
            pie[model] = pie.get(model, 0.0) + rec.get("total_cost", 0.0)
    result["cost_by_model_pie"] = {
        "labels": list(pie.keys()),
        "series": [round(v, 6) for v in pie.values()],
    }

    # --- Fusionar latency_series ---
    lat = result["latency_series"]
    lat_points = list(zip(
        lat.get("labels", []),
        lat.get("gateway_latency", []),
        lat.get("backend_latency", []),
    ))
    if app2_gateway_records:
        for rec in app2_gateway_records:
            label = (rec.get("timestamp") or "")[:19].replace("T", " ")
            lat_points.append((
                label,
                rec.get("gateway_latency_ms", 0.0),
                rec.get("backend_latency_ms", 0.0),
            ))
    lat_points.sort(key=lambda p: p[0])
    if lat_points:
        lat_labels, gw_lat, be_lat = map(list, zip(*lat_points))
    else:
        lat_labels, gw_lat, be_lat = [], [], []
    result["latency_series"] = {
        "labels": lat_labels,
        "gateway_latency": gw_lat,
        "backend_latency": be_lat,
    }

    return result


# ---------------------------------------------------------------------------
# Solicitudes e Historial (para /api/requests)
# ---------------------------------------------------------------------------

def get_app2_requests(limit: int = 100) -> Optional[List[Dict[str, Any]]]:
    """Índice de solicitudes de app2 (mismo shape que REQUESTS_INDEX_PATH de app1)."""
    data = _fetch("/api/requests", params={"limit": limit})
    if data is None:
        return None
    return data.get("requests", [])


def get_app2_request_detail(request_id: str) -> Optional[Dict[str, Any]]:
    """Detalle (resumen + eventos) de una solicitud puntual de app2."""
    return _fetch(f"/api/requests/{request_id}")


def merge_requests(
    app1_requests: List[Dict[str, Any]],
    app2_requests: Optional[List[Dict[str, Any]]],
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Combina los índices de solicitudes de ambas apps, ordenados por fecha desc."""
    combined = list(app1_requests) + list(app2_requests or [])
    combined.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return combined[:limit]