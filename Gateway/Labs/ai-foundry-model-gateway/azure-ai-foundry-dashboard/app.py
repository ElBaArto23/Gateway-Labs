"""
app.py
======
Backend del "AI Foundry Model Gateway" (APP1). Punto de entrada de Flask:
recibe las solicitudes del frontend, llama a foundry_service.py y usa
EventLogger para reportar cada etapa al panel de procesos del frontend.
Además, consulta vía HTTP a APP2 para fusionar métricas de telemetría y consumo.

Variables de entorno requeridas
--------------------------------
    AI_FOUNDRY_PROJECT_ENDPOINT   (obligatoria)
    AGENT_NAME                    (opcional, por defecto "my-test-agent")

Ejecutar
--------
    pip install flask flask-cors azure-ai-projects azure-identity
    python app.py
"""

from __future__ import annotations

import json
import os
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Carga .env (mismo patrón que ai-gateway-app/app.py, App 2). Las
# variables ya exportadas en el sistema/terminal siguen teniendo
# prioridad si existen -- load_dotenv() no las pisa por defecto.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# Importación de servicios desde la carpeta services/
from services import (
    record_usage,
    get_consumption_summary,
    check_budget,
    BudgetExceededError,
    EventLogger,
    get_dashboard_summary,
    generate_chart_data,
    record_gateway_event,
    get_gateway_telemetry,
    record_metric,
    get_aggregated_metrics,
    check_alerts,
)
from services.event_logger import STAGE_LABELS
from services.gateway_metrics import GATEWAY_LOG_PATH
from services.usage_tracker import USAGE_LOG_PATH
import services.foundry_service as foundry_service

# 1) Import del cliente para APP2
from services.app2_client import (
    get_app2_consumption,
    get_app2_gateway_telemetry,
    merge_consumption_summaries,
    get_app2_usage_records,
    get_app2_gateway_records,
    merge_chart_data,
    get_app2_requests,        # NUEVO — hay que agregarlo a app2_client.py
    get_app2_request_detail,  # NUEVO — ídem
    merge_requests,           # NUEVO — ídem
)

STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

# Marcador de versión -- se actualiza cada vez que se corrige algo en el
# atajo local de telemetría, para poder confirmar desde /api/version que
# el código realmente activo en el servidor es el último que se subió,
# sin depender de interpretar las respuestas del chat.
APP_CODE_VERSION = "telemetry-v5-min-max-plural-2026-08-09"

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
CORS(app)  # Permite peticiones fetch desde cualquier origen/puerto local


@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_CODE_VERSION})


# Nota: el endpoint HTTP /mcp (Blueprint mcp_bp) YA NO se registra aquí.
# Se dejó de usar porque el Agente de Foundry no lograba completar el
# handshake de inicialización contra él (timeout en /mcp desde el agente).
# Las funciones de telemetría que vivían detrás de ese endpoint se siguen
# usando -- pero ahora se llaman directo desde Flask (ver más abajo), sin
# pasar por Foundry ni por el protocolo MCP en absoluto.

# 2) Atajo local de telemetría para el chat
# ------------------------------------------
# Mientras el agente de Foundry no logre enumerar la tool MCP `telemetry2`
# de forma confiable, respondemos las preguntas de consumo/costos/tráfico
# directamente en Flask, reusando las mismas funciones que ya expone
# services/mcp_server.py (ya probadas y funcionando). Esto no reemplaza
# al agente para el resto de preguntas -- solo intercepta las de telemetría
# ANTES de llamar a Foundry, así el resto del chat sigue igual.
from services.mcp_server import (
    tool_top_consumer,
    tool_consumption_summary,
    tool_calls_by_hour,
    tool_gateway_health,
    tool_top_call,
)


def try_answer_telemetry_locally(message: str):
    """
    Si el mensaje es una pregunta de telemetría (consumo, costos, tráfico,
    salud del gateway), la responde localmente y devuelve el texto ya
    redactado. Si no reconoce el patrón, devuelve None y el flujo normal
    sigue hacia el agente de Foundry.
    """
    text = (message or "").lower()

    METRIC_WORDS = ("gast", "cost", "cuest", "consum")
    TOKEN_WORDS = ("token",)
    CALL_WORDS = ("llamada", "llamadas", "peticion", "petición")
    HOUR_WORDS = ("hora", "horas", "pico", "tráfico", "trafico")
    HEALTH_WORDS = ("salud", "gateway", "disponibilidad", "errores", "latencia")
    SUMMARY_WORDS = ("resumen", "total", "cuánto hemos", "cuanto hemos", "consumo")

    def has_any(words):
        return any(w in text for w in words)

    # ¿Cuál/cuáles LLAMADA(S) individual(es) tuvo(vieron) más o menos consumo?
    # (distinto de "qué modelo consumió más" -- esto compara llamada por
    # llamada, no agregado por modelo). Se revisa ANTES que la pregunta por
    # modelo para que tenga prioridad cuando se menciona "llamada"/"llamadas"
    # explícitamente. Soporta:
    #   - "más" / "top" / "mayor"        -> orden descendente
    #   - "menos" / "menor" / "mínimo"    -> orden ascendente
    #   - singular ("cuál")               -> devuelve 1 resultado
    #   - plural ("cuáles", "cuales son") -> devuelve un ranking (hasta 5)
    HIGH_WORDS = ("más", "mas", "top", "mayor")
    LOW_WORDS = ("menos", "menor", "mínimo", "minimo", "más bajo", "mas bajo")
    PLURAL_WORDS = ("cuáles", "cuales", "varias", "algunas", "listame", "lístame", "dame las", "top 3", "top 5")

    if has_any(CALL_WORDS) and (has_any(HIGH_WORDS) or has_any(LOW_WORDS)) and (
        has_any(METRIC_WORDS) or has_any(TOKEN_WORDS)
    ):
        order = "asc" if has_any(LOW_WORDS) and not has_any(HIGH_WORDS) else "desc"
        metric = "tokens" if has_any(TOKEN_WORDS) else "cost"
        is_plural = has_any(PLURAL_WORDS)
        limit = 5 if is_plural else 1

        data = tool_top_call({"metric": metric, "order": order, "limit": limit})
        ranking = data.get("ranking") or []
        if not ranking:
            return "Todavía no hay llamadas registradas para calcular esto."

        metric_label = "en tokens" if metric == "tokens" else "en costo"
        direccion = "mayor" if order == "desc" else "menor"

        def _describe(call):
            ts = call.get("timestamp", "N/D")
            modelo = call.get("model_normalized", call.get("model", "N/D"))
            origen = "App1" if call.get("source") == "app1" else "App2"
            return (
                f"{ts} ({origen}, modelo {modelo}): "
                f"{call.get('total_tokens', 0)} tokens totales "
                f"({call.get('input_tokens', 0)} entrada / {call.get('output_tokens', 0)} salida), "
                f"costo de {call.get('total_cost', 0):.6f} USD"
            )

        if not is_plural:
            return (
                f"La llamada individual con {direccion} consumo {metric_label} fue el "
                f"{_describe(ranking[0])}.\n\n"
                f"(Comparado contra {data.get('total_calls_considered', 0)} llamadas en total, App1 + App2.)"
            )

        lines = [f"{i}. {_describe(call)}" for i, call in enumerate(ranking, start=1)]
        return (
            f"Las llamadas con {direccion} consumo {metric_label} son:\n\n"
            + "\n".join(lines)
            + f"\n\n(De un total de {data.get('total_calls_considered', 0)} llamadas, App1 + App2.)"
        )

    # ¿Qué modelo/app consumió más? (costo / tokens / llamadas)
    if ("más" in text or "mas" in text or "top" in text or "mayor" in text) and (
        has_any(METRIC_WORDS) or has_any(TOKEN_WORDS) or has_any(CALL_WORDS)
    ):
        if has_any(TOKEN_WORDS):
            metric = "tokens"
        elif has_any(CALL_WORDS):
            metric = "calls"
        else:
            metric = "cost"

        data = tool_top_consumer({"metric": metric})
        if not data.get("ranking"):
            return "Todavía no hay llamadas registradas para calcular esto."

        metric_label = {"cost": "en costo", "tokens": "en tokens", "calls": "en número de llamadas"}[metric]
        top_model = data["top_model"]
        stats = data["top_model_stats"]
        return (
            f"El modelo que más consumió {metric_label} es **{top_model}**, con "
            f"{stats.get('total_cost', 0):.4f} USD, {stats.get('total_tokens', 0)} tokens y "
            f"{stats.get('call_count', 0)} llamadas registradas.\n\n"
            f"En total (App1 + App2): {data.get('total_cost_usd', 0):.4f} USD, "
            f"{data.get('total_tokens', 0)} tokens, "
            f"{data.get('app1_calls', 0)} llamadas de App1 y {data.get('app2_calls', 0)} de App2."
        )

    # Salud del gateway
    if has_any(HEALTH_WORDS):
        data = tool_gateway_health({})
        app1 = data.get("app1", {}) or {}
        app2 = data.get("app2", {}) or {}
        return (
            f"Salud del gateway:\n"
            f"- App1 → tasa de éxito: {app1.get('success_rate', 'N/D')}, "
            f"latencia promedio: {app1.get('avg_latency_ms', 'N/D')} ms\n"
            f"- App2 → tasa de éxito: {app2.get('success_rate', 'N/D')}, "
            f"latencia promedio: {app2.get('avg_latency_ms', 'N/D')} ms"
        )

    # Tráfico por hora
    if has_any(HOUR_WORDS) and (has_any(CALL_WORDS) or "tráfico" in text or "trafico" in text or True):
        data = tool_calls_by_hour({})
        peak = data.get("peak_hour_utc")
        if peak is None:
            return "Todavía no hay suficientes llamadas registradas para ver un patrón por hora."
        return (
            f"La hora con más tráfico (UTC) es la **{peak}:00**, con "
            f"{data.get('peak_hour_calls', 0)} llamadas registradas en esa franja."
        )

    # Resumen general de consumo
    if has_any(SUMMARY_WORDS):
        data = tool_consumption_summary({})
        return (
            f"Resumen de consumo (App1 + App2):\n"
            f"- Costo total: {data.get('total_cost', 0):.4f} USD\n"
            f"- Tokens totales: {data.get('total_tokens', 0)}\n"
            f"- Llamadas App1: {data.get('app1_call_count', 0)}, "
            f"App2: {data.get('app2_call_count', 0)}"
        )

    return None

AGENT_NAME = os.environ.get("AGENT_NAME", "my-test-agent")

# Debe coincidir con el límite usado en check_budget() más abajo y en
# alerts_service.py, para no tener el mismo número hardcodeado en 3 lugares.
BUDGET_MAX_USD = float(os.environ.get("BUDGET_MAX_USD", "5.0"))

ERRORS_LOG_PATH = os.environ.get("ERRORS_LOG_PATH", "errors_log.jsonl")

# Índice de solicitudes: un registro resumido por cada /api/chat procesado
REQUESTS_INDEX_PATH = os.environ.get("REQUESTS_INDEX_PATH", "requests_index.jsonl")

# Historial de conversación en memoria por sesión de proceso
_conversation_state = {"conversation_id": None, "agent_id": None}

# Últimos eventos de EventLogger, para que el módulo "Flujo del Sistema"
# pueda consultarlos vía /api/events/latest
_last_events = {
    "request_id": None,
    "generated_at": None,
    "status": "idle",
    "events": [],
    "total_duration_ms": 0.0,
}

_APP_START_TIME = time.time()


def _requests_per_minute(window_minutes: int = 15) -> dict:
    """
    Agrupa gateway_log.jsonl por minuto de reloj (YYYY-MM-DDTHH:MM).
    Útil para la prueba de carga: "¿cuántas solicitudes por minuto?".
    """
    buckets = Counter()
    if os.path.exists(GATEWAY_LOG_PATH):
        with open(GATEWAY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    minute_key = rec.get("timestamp", "")[:16]  # YYYY-MM-DDTHH:MM
                    if minute_key:
                        buckets[minute_key] += 1
                except json.JSONDecodeError:
                    continue

    ordered = sorted(buckets.items())[-window_minutes:]
    values = [v for _, v in ordered]
    return {
        "labels": [k[-5:] for k, _ in ordered],  # solo HH:MM
        "values": values,
        "peak_per_minute": max(values) if values else 0,
        "avg_per_minute": round(sum(values) / len(values), 2) if values else 0,
    }


def _persist_request_summary(logger: EventLogger, usage: dict | None, cost: dict | None, breakdown: dict, status: str) -> None:
    """
    Guarda un registro resumido de la solicitud en REQUESTS_INDEX_PATH.
    """
    entry = {
        "request_id": logger.request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,  # "ok" | "error" | "blocked"
        "model": (usage or {}).get("model"),
        "input_tokens": (usage or {}).get("input_tokens", 0),
        "output_tokens": (usage or {}).get("output_tokens", 0),
        "total_tokens": (usage or {}).get("total_tokens", 0),
        "cost_total": ((cost or {}).get("cost") or {}).get("total", 0.0),
        "gateway_latency_ms": breakdown.get("gateway_latency_ms", 0.0),
        "backend_latency_ms": breakdown.get("backend_latency_ms", 0.0),
        "total_latency_ms": logger.total_duration_ms(),
    }
    try:
        with open(REQUESTS_INDEX_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        traceback.print_exc()


def _persist_error(stage: str, message: str, request_id: str) -> None:
    """Guarda un error en ERRORS_LOG_PATH para que /api/issues tenga historial."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "stage": stage,
        "message": message,
    }
    try:
        with open(ERRORS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        traceback.print_exc()


def run_agent_query(message: str, logger: EventLogger) -> dict:
    """
    Ejecuta el flujo completo de una consulta al laboratorio, instrumentado
    con el Event Logger.
    """

    with logger.stage("received", detail=f"Mensaje recibido ({len(message)} caracteres)"):
        pass

    with logger.stage("validating", detail="Verificando que el mensaje no esté vacío"):
        if not message or not message.strip():
            raise ValueError("El mensaje no puede estar vacío")

    with logger.stage("preparing", detail="Construyendo el payload de la solicitud"):
        payload = {"role": "user", "content": message}

    with logger.stage("telemetry_shortcut", detail="Revisando si es pregunta de telemetría"):
        local_reply = try_answer_telemetry_locally(message)

    if local_reply is not None:
        # Respondida localmente, sin pasar por Foundry -- evita por completo
        # el problema de enumeración de tools del agente.
        return {
            "reply": local_reply,
            "agent_id": _conversation_state.get("agent_id") or "telemetry-local",
            "conversation_id": _conversation_state.get("conversation_id") or "local",
            "response_id": None,
            "usage": {
                "model": "telemetry-local",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            },
            "cost": 0.0,
        }

    with logger.stage("connecting", detail="Autenticando contra Azure AI Foundry"):
        foundry_service.get_project_client()

    with logger.stage("agent_lookup", detail="Recuperando el Persistent Agent existente"):
        agent = foundry_service.get_agent()
        _conversation_state["agent_id"] = agent.id

    with logger.stage("conversation", detail="Reutilizando conversación si ya existe"):
        conversation_id = foundry_service.create_or_reuse_conversation(
            _conversation_state["conversation_id"]
        )
        _conversation_state["conversation_id"] = conversation_id

    with logger.stage("responses_api", detail="Enviando la solicitud mediante Responses API"):
        response = foundry_service.send_message(message, agent, conversation_id)

    with logger.stage("waiting_model", detail="Esperando la respuesta del modelo en Azure AI"):
        model_reply = getattr(response, "output_text", str(response))
        response_id = getattr(response, "id", None)

    with logger.stage("response_received", detail="Respuesta recibida desde el modelo"):
        pass

    with logger.stage("processing_output", detail="Extrayendo el texto final de la respuesta"):
        final_text = model_reply

    with logger.stage("sent_to_frontend", detail="Respuesta lista para enviar al cliente"):
        pass

    usage = getattr(response, "usage", None)
    model = getattr(response, "model", "gpt-5.4-mini")

    input_details = getattr(usage, "input_tokens_details", None) if usage else None
    output_details = getattr(usage, "output_tokens_details", None) if usage else None

    usage_dict = {
        "model": model,
        "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
        "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
        "cached_tokens": getattr(input_details, "cached_tokens", 0) if input_details else 0,
        "reasoning_tokens": getattr(output_details, "reasoning_tokens", 0) if output_details else 0,
    }

    cost = None
    try:
        with logger.stage("calculating_cost", detail="Calculando costo estimado de la llamada"):
            cost = record_usage(
                agent_id=agent.id,
                agent_name=AGENT_NAME,
                model=usage_dict["model"],
                input_tokens=usage_dict["input_tokens"],
                output_tokens=usage_dict["output_tokens"],
                cached_tokens=usage_dict["cached_tokens"],
                reasoning_tokens=usage_dict["reasoning_tokens"],
                response_id=response_id,
                conversation_id=conversation_id,
            )
    except Exception:
        traceback.print_exc()

    return {
        "reply": final_text,
        "agent_id": getattr(agent, "id", "default-agent"),
        "conversation_id": conversation_id,
        "response_id": response_id,
        "usage": usage_dict,
        "cost": cost,
    }


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()

    logger = EventLogger()

    # 1. Control de Presupuesto
    try:
        check_budget(AGENT_NAME, max_cost_usd=BUDGET_MAX_USD, window_minutes=60)
    except BudgetExceededError as exc:
        _persist_error("budget_check", str(exc), logger.request_id)
        _persist_request_summary(logger, None, None, {"gateway_latency_ms": 0.0, "backend_latency_ms": 0.0}, "blocked")
        return jsonify({"error": str(exc)}), 429

    # 2. Ejecución del Agente
    try:
        result = run_agent_query(message, logger)
        breakdown = logger.latency_breakdown()

        record_gateway_event(
            request_id=logger.request_id,
            status_code=200,
            gateway_latency_ms=breakdown["gateway_latency_ms"],
            backend_latency_ms=breakdown["backend_latency_ms"],
            endpoint="/api/chat",
            model_used=result.get("usage", {}).get("model"),
        )
        record_metric(
            "request_total_latency_ms",
            logger.total_duration_ms(),
            unit="ms",
            tags={"endpoint": "/api/chat", "status": "ok"},
        )
        _persist_request_summary(logger, result.get("usage"), result.get("cost"), breakdown, "ok")

        return jsonify(
            {
                "reply": result["reply"],
                "agent_id": result["agent_id"],
                "conversation_id": result["conversation_id"],
                "response_id": result["response_id"],
                "usage": result.get("usage"),
                "cost": result.get("cost"),
                "events": logger.as_list(),
                "total_duration_ms": logger.total_duration_ms(),
                "request_id": logger.request_id,
            }
        )

    except Exception as exc:
        print("\n==============================================")
        print("!!! ERROR DENTRO DE /api/chat !!!")
        traceback.print_exc()
        print("==============================================\n")

        if logger.errors:
            for err in logger.errors:
                _persist_error(err.get("stage", "unknown"), err.get("message", str(exc)), logger.request_id)
        else:
            _persist_error("api_chat", str(exc), logger.request_id)

        breakdown = logger.latency_breakdown()
        record_gateway_event(
            request_id=logger.request_id,
            status_code=500,
            gateway_latency_ms=breakdown["gateway_latency_ms"],
            backend_latency_ms=breakdown["backend_latency_ms"],
            endpoint="/api/chat",
            model_used=None,
        )
        _persist_request_summary(logger, None, None, breakdown, "error")

        return jsonify({
            "error": str(exc),
            "events": logger.as_list()
        }), 500

    finally:
        logger.dump_json(f"events_{logger.request_id}.json")
        _last_events.update({
            "request_id": logger.request_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "error" if logger.errors else "ok",
            "events": logger.as_list(),
            "total_duration_ms": logger.total_duration_ms(),
        })


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# 2) REEMPLAZO DE api_status CON app2_gateway INCLUIDO
@app.route("/api/status")
def api_status():
    gateway = get_gateway_telemetry()
    foundry_connected = foundry_service._project_client is not None
    agent_cached = foundry_service._agent_cache is not None

    # NUEVO: telemetría de gateway reportada por app2.
    app2_gateway = get_app2_gateway_telemetry()

    overall = "ok"
    if gateway.get("total_requests", 0) > 0 and gateway.get("success_rate_percentage", 100.0) < 95.0:
        overall = "degraded"

    return jsonify({
        "status": overall,
        "gateway": gateway,
        "app2_gateway": app2_gateway,  # None si app2 no está configurada o no respondió
        "requests_per_minute": _requests_per_minute(),
        "foundry_connection": {
            "connected": foundry_connected,
            "agent_cached": agent_cached,
            "agent_name": AGENT_NAME,
            "conversation_active": _conversation_state.get("conversation_id") is not None,
        },
        "budget": {
            "max_usd": BUDGET_MAX_USD,
        },
        "uptime_seconds": round(time.time() - _APP_START_TIME, 1),
    })


# 3) REEMPLAZO DE api_consumption FUSIONANDO APP1 Y APP2
@app.route("/api/consumption")
def api_consumption():
    agent_name = request.args.get("agent")

    since = None
    since_param = request.args.get("since")
    if since_param:
        try:
            since = datetime.fromisoformat(since_param.replace("Z", "+00:00"))
        except ValueError:
            since = None

    summary = get_consumption_summary(agent_name=agent_name, since=since)

    # NUEVO: fusiona el consumo reportado por app2 (vía HTTP) con el de
    # app1 (local). Si app2 no está configurada o no responde, esto no
    # rompe nada -- simplemente summary queda igual que antes.
    app2_summary = get_app2_consumption(
        agent_name=agent_name,
        since=since.isoformat() if since else None,
    )
    summary = merge_consumption_summaries(summary, app2_summary)

    # "top_model" ya viene calculado por merge_consumption_summaries()
    # cuando hay datos de app2; si no, lo calculamos aquí como antes.
    if "top_model" not in summary:
        by_model = summary.get("by_model") or {}
        top_model = None
        if by_model:
            top_model = max(by_model.items(), key=lambda kv: kv[1].get("call_count", 0))[0]
        summary["top_model"] = top_model

    return jsonify(summary)


@app.route("/api/consumption/calls")
def api_consumption_calls():
    """
    Detalle de solicitudes individuales (no agregado).
    """
    agent_name = request.args.get("agent")
    sort_by = request.args.get("sort", "cost")
    limit = int(request.args.get("limit", 100))

    records = []
    if os.path.exists(USAGE_LOG_PATH):
        with open(USAGE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if agent_name and rec.get("agent_name") != agent_name:
                    continue
                records.append(rec)

    if sort_by == "tokens":
        records.sort(key=lambda r: r.get("total_tokens", 0), reverse=True)
    elif sort_by == "time":
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    else:
        records.sort(key=lambda r: r.get("total_cost", 0.0), reverse=True)

    return jsonify({
        "agent_name": agent_name,
        "sort": sort_by,
        "count": len(records),
        "calls": records[:limit],
    })


@app.route("/api/requests")
def api_requests_list():
    limit = int(request.args.get("limit", 100))

    records = []
    if os.path.exists(REQUESTS_INDEX_PATH):
        with open(REQUESTS_INDEX_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    app2_records = get_app2_requests(limit=limit)
    merged = merge_requests(records, app2_records, limit=limit)

    return jsonify({
        "count": len(merged),
        "requests": merged,
        "app2_available": app2_records is not None,
    })


@app.route("/api/requests/<request_id>")
def api_request_detail(request_id):
    summary = None
    if os.path.exists(REQUESTS_INDEX_PATH):
        with open(REQUESTS_INDEX_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("request_id") == request_id:
                    summary = rec

    events_path = f"events_{request_id}.json"
    events_payload = None
    if os.path.exists(events_path):
        try:
            with open(events_path, "r", encoding="utf-8") as f:
                events_payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            events_payload = None

    if summary is None and events_payload is None:
        app2_detail = get_app2_request_detail(request_id)
        if app2_detail is None:
            return jsonify({"error": f"No se encontró la solicitud '{request_id}'."}), 404
        return jsonify(app2_detail)

    return jsonify({
        "request_id": request_id,
        "summary": summary,
        "events": (events_payload or {}).get("events", []),
        "total_duration_ms": (events_payload or {}).get("total_duration_ms"),
    })


@app.route("/api/pipeline/stages")
def api_pipeline_stages():
    """
    Etapas canónicas del pipeline de procesamiento.
    """
    stages = [
        {"key": key, "label": label, "order": i}
        for i, (key, label) in enumerate(STAGE_LABELS.items(), start=1)
    ]
    return jsonify({"stages": stages})


@app.route("/api/events/latest")
def api_events_latest():
    """
    Eventos de la última solicitud /api/chat procesada.
    """
    return jsonify(_last_events)


@app.route("/api/metrics")
def api_metrics():
    charts = generate_chart_data()
    app2_usage_records = get_app2_usage_records()
    app2_gateway_records = get_app2_gateway_records()
    charts = merge_chart_data(charts, app2_usage_records, app2_gateway_records)
    aggregated = get_aggregated_metrics(since_minutes=60)
    return jsonify({**charts, "aggregated": aggregated})


@app.route("/api/issues")
def api_issues():
    """
    Errores de ejecución, errores del gateway y alertas activas.
    """
    limit = int(request.args.get("limit", 50))

    errors = []
    if os.path.exists(ERRORS_LOG_PATH):
        with open(ERRORS_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    errors.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    gateway_errors = []
    if os.path.exists(GATEWAY_LOG_PATH):
        with open(GATEWAY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("status_code", 200) >= 400:
                        gateway_errors.append(rec)
                except json.JSONDecodeError:
                    continue

    return jsonify({
        "errors": errors[-limit:],
        "gateway_errors": gateway_errors[-limit:],
        "alerts": check_alerts(AGENT_NAME),
    })


@app.route("/api/architecture")
def api_architecture():
    """
    Recursos/arquitectura de Azure en uso.
    """
    connected = foundry_service._project_client is not None
    agent_cached = foundry_service._agent_cache is not None
    conversation_active = _conversation_state.get("conversation_id") is not None

    nodes = [
        {"id": "client", "type": "client", "label": "Cliente (navegador)", "status": "active"},
        {"id": "flask", "type": "backend", "label": "Flask Gateway (app.py)", "status": "active"},
        {"id": "foundry_project", "type": "azure", "label": "AI Foundry Project", "status": "active" if connected else "idle"},
        {"id": "agent", "type": "azure", "label": f"Persistent Agent ({AGENT_NAME})", "status": "active" if agent_cached else "idle"},
        {"id": "conversation", "type": "azure", "label": "Conversation (Responses API)", "status": "active" if conversation_active else "idle"},
        {"id": "usage_log", "type": "storage", "label": "usage_log.jsonl", "status": "active"},
        {"id": "gateway_log", "type": "storage", "label": "gateway_log.jsonl", "status": "active"},
    ]
    edges = [
        {"from": "client", "to": "flask"},
        {"from": "flask", "to": "foundry_project"},
        {"from": "foundry_project", "to": "agent"},
        {"from": "agent", "to": "conversation"},
        {"from": "flask", "to": "usage_log"},
        {"from": "flask", "to": "gateway_log"},
    ]

    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "endpoint": foundry_service.PROJECT_ENDPOINT,
        "agent_name": AGENT_NAME,
    })


@app.route("/api/debug/telemetry-local")
def api_debug_telemetry_local():
    """
    ENDPOINT TEMPORAL DE DIAGNÓSTICO -- borrar cuando se resuelva el bug
    de telemetría local. Expone en crudo, vía GET (solo abrir la URL en
    el navegador, sin terminal), exactamente lo que ve internamente el
    atajo de telemetría del chat, para compararlo con lo que muestra
    el dashboard.
    """
    from services.usage_tracker import get_consumption_summary as _gcs

    app1_summary = _gcs()
    app2_summary = get_app2_consumption()
    merged = merge_consumption_summaries(app1_summary, app2_summary)
    top_tokens = tool_top_consumer({"metric": "tokens"})

    raw_lines = []
    if os.path.exists(USAGE_LOG_PATH):
        with open(USAGE_LOG_PATH, "r", encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f if line.strip()]

    return jsonify({
        "1_usage_log_path_en_uso": USAGE_LOG_PATH,
        "2_ese_archivo_existe": os.path.exists(USAGE_LOG_PATH),
        "3_cantidad_de_lineas_crudas_en_el_archivo": len(raw_lines),
        "4_ultimas_3_lineas_crudas": raw_lines[-3:],
        "5_app1_summary_sin_fusionar": app1_summary,
        "6_APP2_BASE_URL_configurada": os.environ.get("APP2_BASE_URL", "(vacía)"),
        "7_app2_summary_o_None_si_app2_no_respondio": app2_summary,
        "8_resultado_fusionado_merge_consumption_summaries": merged,
        "9_lo_que_devuelve_tool_top_consumer_tokens": top_tokens,
    })


# ---------------------------------------------------------------------
# Endpoint temporal de prueba: valida conectividad end-to-end contra el
# AI Foundry Model Gateway (APIM compartido), vía chat.completions,
# SIN pasar por el flujo de agentes de Foundry existente (foundry_service).
# Es un endpoint aislado, solo para confirmar que AI_GATEWAY_URL y
# AI_GATEWAY_KEY funcionan y que APIM está enrutando correctamente.
# Se puede borrar una vez validado.
# ---------------------------------------------------------------------
@app.route("/api/test-gateway")
def api_test_gateway():
    from openai import AzureOpenAI

    gateway_url = os.environ.get("AI_GATEWAY_URL")
    gateway_key = os.environ.get("AI_GATEWAY_KEY")

    if not gateway_url or not gateway_key:
        return jsonify({
            "ok": False,
            "error": "Faltan AI_GATEWAY_URL o AI_GATEWAY_KEY en las variables de entorno."
        }), 500

    model = request.args.get("model", "gpt-5.4-mini")

    try:
        client = AzureOpenAI(
            azure_endpoint=gateway_url,
            api_key=gateway_key,
            api_version="2024-12-01-preview",
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hola, ¿estás funcionando a través del gateway?"}],
        )
        return jsonify({
            "ok": True,
            "model": model,
            "reply": response.choices[0].message.content,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "model": model,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)