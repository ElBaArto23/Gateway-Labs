"""
services/mcp_server.py
=======================
Servidor MCP (Model Context Protocol) mínimo, montado como Blueprint de
Flask dentro del mismo proceso de app1. Expone tools de solo lectura
sobre la telemetría ya unificada (app1 + app2) para que el Agente de
Foundry pueda consultarlas con tool-calling nativo.

No usa el SDK oficial `mcp` (que es ASGI/Starlette) para no mezclar dos
modelos de servidor en el mismo proceso -- implementa a mano el
subconjunto de la spec "Streamable HTTP" que Foundry necesita:
    POST /mcp   { "method": "initialize" | "tools/list" | "tools/call", ... }

Referencia de forma de respuesta JSON-RPC 2.0:
https://modelcontextprotocol.io/specification
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, request, jsonify

from .usage_tracker import get_consumption_summary, get_usage_records, USAGE_LOG_PATH
from .app2_client import get_app2_consumption, merge_consumption_summaries, get_app2_requests
from .gateway_metrics import get_gateway_telemetry
from .app2_client import get_app2_gateway_telemetry, get_app2_usage_records

import os

mcp_bp = Blueprint("mcp", __name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "ai-foundry-telemetry-mcp", "version": "1.0.0"}

# Secreto compartido para autenticar al Agente de Foundry contra este
# servidor MCP. Se configura como Application Setting en Azure y se
# guarda del lado de Foundry en una Project Connection (key-based).
# Si no está configurado, el endpoint queda abierto (modo lab/desarrollo).
MCP_SHARED_SECRET = os.environ.get("MCP_SHARED_SECRET", "")


# ---------------------------------------------------------------------------
# Implementación de las tools (funciones puras que reusan services/ ya
# existentes -- ninguna lógica de agregación nueva, solo se expone).
# ---------------------------------------------------------------------------

def _unified_consumption(agent_name=None, since=None) -> Dict[str, Any]:
    app1_summary = get_consumption_summary(agent_name=agent_name, since=since)
    app2_summary = get_app2_consumption(
        agent_name=agent_name,
        since=since.isoformat() if since else None,
    )
    return merge_consumption_summaries(app1_summary, app2_summary)


def tool_top_consumer(args: Dict[str, Any]) -> Dict[str, Any]:
    """¿Qué modelo (o app) consumió más? Ordena by_model por costo o tokens."""
    metric = args.get("metric", "cost")  # "cost" | "tokens" | "calls"
    summary = _unified_consumption()
    by_model = summary.get("by_model") or {}

    if not by_model:
        return {"message": "Aún no hay llamadas registradas.", "by_model": {}}

    key_map = {
        "cost": lambda kv: kv[1].get("total_cost", 0.0),
        "tokens": lambda kv: kv[1].get("total_tokens", 0),
        "calls": lambda kv: kv[1].get("call_count", 0),
    }
    sort_key = key_map.get(metric, key_map["cost"])
    ranked = sorted(by_model.items(), key=sort_key, reverse=True)
    top_model, top_bucket = ranked[0]

    return {
        "metric": metric,
        "top_model": top_model,
        "top_model_stats": top_bucket,
        "ranking": [{"model": m, **b} for m, b in ranked],
        "app1_calls": summary.get("app1_call_count"),
        "app2_calls": summary.get("app2_call_count"),
        "total_cost_usd": summary.get("total_cost"),
        "total_tokens": summary.get("total_tokens"),
    }


def tool_top_call(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    ¿Cuál(es) llamada(s) INDIVIDUAL(es) (no agregadas por modelo) costaron o
    consumieron más -- o menos. Distinto de tool_top_consumer: si todas las
    llamadas son del mismo modelo, esto sigue siendo útil porque compara
    llamada por llamada.

    args:
        metric: "cost" | "tokens" (por defecto "cost")
        order:  "desc" (mayor primero, por defecto) | "asc" (menor primero)
        limit:  cuántas llamadas devolver en el ranking (por defecto 5)
    """
    metric = args.get("metric", "cost")
    order = args.get("order", "desc")
    limit = int(args.get("limit", 5))

    app1_records = [{**r, "source": "app1"} for r in get_usage_records()]
    app2_records = [{**r, "source": "app2"} for r in (get_app2_usage_records() or [])]
    all_records = app1_records + app2_records

    if not all_records:
        return {"message": "Aún no hay llamadas registradas.", "top_call": None, "ranking": []}

    key = "total_cost" if metric == "cost" else "total_tokens"
    ranked = sorted(all_records, key=lambda r: r.get(key, 0), reverse=(order != "asc"))

    return {
        "metric": metric,
        "order": order,
        "top_call": ranked[0],
        "ranking": ranked[:limit],
        "total_calls_considered": len(all_records),
    }


def tool_consumption_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """Resumen total de consumo (costo, tokens, llamadas), opcionalmente desde una fecha."""
    since = None
    since_str = args.get("since")
    if since_str:
        try:
            since = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
        except ValueError:
            since = None
    return _unified_consumption(since=since)


def tool_calls_by_hour(args: Dict[str, Any]) -> Dict[str, Any]:
    """Distribución de llamadas por hora del día (0-23, hora UTC), app1 + app2."""
    limit = int(args.get("limit", 500))

    records = []
    from .usage_tracker import USAGE_LOG_PATH as _p
    if os.path.exists(_p):
        with open(_p, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    app2_requests = get_app2_requests(limit=limit) or []
    # app2_requests trae "timestamp" en el request index; usage records también.
    all_timestamps = [r.get("timestamp", "") for r in records] + [r.get("timestamp", "") for r in app2_requests]

    buckets = [0] * 24
    for ts in all_timestamps:
        try:
            hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
            buckets[hour] += 1
        except (ValueError, AttributeError):
            continue

    peak_hour = max(range(24), key=lambda h: buckets[h]) if any(buckets) else None
    return {
        "buckets_utc": buckets,
        "peak_hour_utc": peak_hour,
        "peak_hour_calls": buckets[peak_hour] if peak_hour is not None else 0,
        "note": "Horas en UTC. call_count por hora, sumando app1 + app2.",
    }


def tool_gateway_health(args: Dict[str, Any]) -> Dict[str, Any]:
    """Salud del gateway: tasa de éxito y latencia promedio, app1 + app2."""
    app1 = get_gateway_telemetry()
    app2 = get_app2_gateway_telemetry() or {}
    return {"app1": app1, "app2": app2}


TOOLS = {
    "get_top_consumer": {
        "description": (
            "Devuelve qué modelo consumió más recursos (costo, tokens o número de "
            "llamadas), combinando datos de App1 y App2. Úsala para preguntas tipo "
            "'¿qué modelo consumió más?' o '¿cuál gastó más dinero?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["cost", "tokens", "calls"],
                    "description": "Criterio de comparación. Por defecto 'cost'.",
                }
            },
        },
        "handler": tool_top_consumer,
    },
    "get_top_call": {
        "description": (
            "Devuelve cuál(es) llamada(s) INDIVIDUAL(es) (no agregadas por modelo) "
            "costaron o consumieron más -- o menos, con order='asc' -- combinando "
            "App1 y App2. Útil cuando todas las llamadas son del mismo modelo y "
            "'qué modelo consumió más' no aporta nada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["cost", "tokens"],
                    "description": "Criterio de comparación. Por defecto 'cost'.",
                },
                "order": {
                    "type": "string",
                    "enum": ["desc", "asc"],
                    "description": "'desc' = mayor primero (por defecto), 'asc' = menor primero.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Cuántas llamadas devolver en el ranking. Por defecto 5.",
                },
            },
        },
        "handler": tool_top_call,
    },
    "get_consumption_summary": {
        "description": (
            "Resumen total de consumo (costo total, tokens totales, número de "
            "llamadas), combinando App1 y App2. Acepta un filtro opcional 'since' "
            "en formato ISO8601 para acotar a partir de una fecha."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "Fecha ISO8601, ej. 2026-08-08T00:00:00Z"}
            },
        },
        "handler": tool_consumption_summary,
    },
    "get_calls_by_hour": {
        "description": (
            "Distribución de llamadas por hora del día (0-23 UTC), combinando "
            "App1 y App2. Útil para preguntas de 'a qué hora hay más tráfico'."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_calls_by_hour,
    },
    "get_gateway_health": {
        "description": "Salud del gateway (tasa de éxito, latencia promedio) de App1 y App2.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_gateway_health,
    },
}


# ---------------------------------------------------------------------------
# Endpoint JSON-RPC
# ---------------------------------------------------------------------------

def _rpc_result(rpc_id, result):
    return jsonify({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id, code, message):
    return jsonify({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})


@mcp_bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    if MCP_SHARED_SECRET:
        # Foundry envía el header tal como lo definas en la Project Connection.
        # Aquí se acepta "Authorization: Bearer <secreto>" (el formato más
        # estándar y el que Foundry sugiere por defecto para key-based auth).
        auth_header = request.headers.get("Authorization", "")
        provided = auth_header.removeprefix("Bearer ").strip()
        if provided != MCP_SHARED_SECRET:
            body = request.get_json(silent=True) or {}
            return _rpc_error(body.get("id"), -32001, "No autorizado: falta o es incorrecto el secreto MCP.")

    body = request.get_json(silent=True) or {}
    method = body.get("method")
    rpc_id = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return _rpc_result(rpc_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "notifications/initialized":
        # Notificación, no requiere respuesta con contenido.
        return ("", 204)

    if method == "tools/list":
        tools_payload = [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["input_schema"],
            }
            for name, spec in TOOLS.items()
        ]
        return _rpc_result(rpc_id, {"tools": tools_payload})

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        spec = TOOLS.get(tool_name)
        if not spec:
            return _rpc_error(rpc_id, -32601, f"Tool desconocida: {tool_name}")
        try:
            output = spec["handler"](tool_args)
        except Exception as exc:  # noqa: BLE001
            return _rpc_result(rpc_id, {
                "content": [{"type": "text", "text": f"Error ejecutando la tool: {exc}"}],
                "isError": True,
            })
        return _rpc_result(rpc_id, {
            "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
            "isError": False,
        })

    return _rpc_error(rpc_id, -32601, f"Método no soportado: {method}")