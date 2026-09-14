"""
app.py — AI Logistics Assistant (app "disparadora" de pruebas)
================================================================
Chat simple que llama a Azure API Management (Model Gateway)
y registra cada llamada en los mismos archivos .jsonl que lee tu dashboard
real (azure-ai-foundry-dashboard), usando sus mismos módulos de services/
sin modificarlos.
"""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Carga inicial de variables de entorno (.env)
# ---------------------------------------------------------------------------
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

# Derivación dinámica de rutas de log
_dashboard_dir_raw = os.getenv("DASHBOARD_DATA_DIR", "").strip()
if _dashboard_dir_raw:
    _dashboard_dir = os.path.expanduser(_dashboard_dir_raw)
    os.makedirs(_dashboard_dir, exist_ok=True)
    os.environ.setdefault("USAGE_LOG_PATH", os.path.join(_dashboard_dir, "usage_log.jsonl"))
    os.environ.setdefault("GATEWAY_LOG_PATH", os.path.join(_dashboard_dir, "gateway_log.jsonl"))
    os.environ.setdefault("METRICS_LOG_PATH", os.path.join(_dashboard_dir, "metrics_log.jsonl"))
    os.environ.setdefault("REQUESTS_INDEX_PATH", os.path.join(_dashboard_dir, "requests_index.jsonl"))
    os.environ.setdefault("ERRORS_LOG_PATH", os.path.join(_dashboard_dir, "errors_log.jsonl"))
    os.environ.setdefault("EVENTS_DIR", _dashboard_dir)

# Importaciones de servicios (escritura y lectura para telemetría)
from services.event_logger import EventLogger
from services.gateway_metrics import GATEWAY_LOG_PATH, get_gateway_telemetry, record_gateway_event
from services.metrics_collector import get_aggregated_metrics, record_metric
from services.usage_tracker import USAGE_LOG_PATH, get_consumption_summary, record_usage

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuración global de la aplicación
# ---------------------------------------------------------------------------
APIM_ENDPOINT = os.getenv("APIM_ENDPOINT", "").rstrip("/")
# Antes el path era "inference" fijo en build_inference_url(). El APIM de
# este lab es COMPARTIDO entre varios labs, así que cada uno tiene su propia
# ruta única (ej. "inference-tazvvonn4lhea") para no chocar con las APIs de
# otros labs. Este valor sale del output "aiGatewayApiName"/"aiGatewayUrl"
# del deployment del lab -- ver CONTEXTO_PROYECTO.md.
APIM_INFERENCE_PATH = os.getenv("APIM_INFERENCE_PATH", "inference").strip("/")
APIM_SUBSCRIPTION_KEY = os.getenv("APIM_SUBSCRIPTION_KEY", "")
MODEL_DEPLOYMENT = os.getenv("MODEL_DEPLOYMENT", "gpt-5.4-mini")
AGENT_NAME = os.getenv("AGENT_NAME", "my-test-agent")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))

REQUESTS_INDEX_PATH = os.getenv("REQUESTS_INDEX_PATH", "requests_index.jsonl")
ERRORS_LOG_PATH = os.getenv("ERRORS_LOG_PATH", "errors_log.jsonl")
EVENTS_DIR = os.getenv("EVENTS_DIR", os.path.dirname(REQUESTS_INDEX_PATH) or ".")

_conversation_state = {"conversation_id": None}

# Reutilización de conexiones con requests.Session
http_session = requests.Session()


def build_inference_url() -> str:
    """Construye la URL del endpoint de inferencia de APIM."""
    return f"{APIM_ENDPOINT}/{APIM_INFERENCE_PATH}/openai/deployments/{MODEL_DEPLOYMENT}/chat/completions?api-version=2024-12-01-preview"


def _persist_request_summary(logger: EventLogger, usage: dict, cost: dict | None, breakdown: dict, status: str) -> None:
    """Registra el resumen de cada request en formato JSONL."""
    cost_total = 0.0
    if cost and isinstance(cost, dict):
        cost_total = cost.get("cost", {}).get("total", 0.0)

    entry = {
        "request_id": logger.request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model": usage.get("model", MODEL_DEPLOYMENT),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cost_total": cost_total,
        "gateway_latency_ms": breakdown.get("gateway_latency_ms", 0.0),
        "backend_latency_ms": breakdown.get("backend_latency_ms", 0.0),
        "total_latency_ms": logger.total_duration_ms(),
        "source": "ai-logistics-assistant-web",
    }
    try:
        with open(REQUESTS_INDEX_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        app.logger.error("Error al escribir en REQUESTS_INDEX_PATH")
        traceback.print_exc()


def _persist_error(stage: str, message: str, request_id: str) -> None:
    """Registra un error ocurrido en las fases de ejecución."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "stage": stage,
        "message": message,
        "source": "ai-logistics-assistant-web",
    }
    try:
        with open(ERRORS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        app.logger.error("Error al escribir en ERRORS_LOG_PATH")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Rutas de Flask
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        model_name=MODEL_DEPLOYMENT,
        apim_configured=bool(APIM_ENDPOINT and APIM_SUBSCRIPTION_KEY),
    )


@app.route("/api/health")
def health_check():
    return jsonify(
        {
            "status": "ok",
            "apim_configured": bool(APIM_ENDPOINT and APIM_SUBSCRIPTION_KEY),
            "model": MODEL_DEPLOYMENT,
            "logs_target": {
                "usage_log": os.getenv("USAGE_LOG_PATH", "usage_log.jsonl"),
                "gateway_log": os.getenv("GATEWAY_LOG_PATH", "gateway_log.jsonl"),
                "metrics_log": os.getenv("METRICS_LOG_PATH", "metrics_log.jsonl"),
                "requests_index": REQUESTS_INDEX_PATH,
                "errors_log": ERRORS_LOG_PATH,
            },
        }
    )


@app.route("/api/consumption")
def api_consumption():
    """
    Resumen de consumo (llamadas, tokens, costo) registrado en app2.
    Usa la misma función get_consumption_summary() que ya existe en
    usage_tracker.py -- ninguna lógica nueva, solo se expone por HTTP.
    """
    return jsonify(get_consumption_summary())


@app.route("/api/gateway")
def api_gateway():
    """
    Telemetría de gateway (tasa de éxito, latencia promedio) registrada
    en app2, vía get_gateway_telemetry() de gateway_metrics.py.
    """
    return jsonify(get_gateway_telemetry())


@app.route("/api/metrics")
def api_metrics():
    """
    Métricas agregadas (latencia, costo, tokens) de la última hora por
    defecto, vía get_aggregated_metrics() de metrics_collector.py.
    Acepta ?since_minutes=N opcional, ej: /api/metrics?since_minutes=1440
    para ver el último día.
    """
    since_minutes = request.args.get("since_minutes", default=60, type=int)
    return jsonify(get_aggregated_metrics(since_minutes=since_minutes))


@app.route("/api/usage/records")
def api_usage_records():
    """
    Registros crudos de usage_log.jsonl de app2 (uno por llamada), para
    que app1 pueda construir sus gráficas de costo/tokens en el tiempo
    incluyendo también las llamadas hechas en app2.
    """
    records = []
    if os.path.exists(USAGE_LOG_PATH):
        with open(USAGE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return jsonify({"records": records})


@app.route("/api/gateway/records")
def api_gateway_records():
    """
    Registros crudos de gateway_log.jsonl de app2 (uno por llamada), para
    la gráfica de latencia combinada.
    """
    records = []
    if os.path.exists(GATEWAY_LOG_PATH):
        with open(GATEWAY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return jsonify({"records": records})


@app.route("/api/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    user_message = (payload.get("message") or "").strip()

    if not user_message:
        return jsonify({"error": "El mensaje no puede estar vacío."}), 400

    if not APIM_ENDPOINT or not APIM_SUBSCRIPTION_KEY:
        return jsonify({"error": "Falta APIM_ENDPOINT / APIM_SUBSCRIPTION_KEY en el archivo .env"}), 500

    logger = EventLogger()
    request_id = logger.request_id

    # IMPORTANTE: EventLogger solo expone stage() como context manager
    # (ver services/event_logger.py) -- NO existe un método stage_start().
    # Por eso cada etapa se marca con "with logger.stage(...):" envolviendo
    # el código real de esa etapa (o "pass" si la etapa es solo informativa).
    with logger.stage("received", detail=f"Mensaje recibido ({len(user_message)} caracteres)"):
        pass

    with logger.stage("validating", detail="Verificando el contenido del mensaje"):
        pass

    with logger.stage("preparing", detail="Construyendo el payload de inferencia"):
        body = {"messages": [{"role": "user", "content": user_message}]}
        headers = {
            "Content-Type": "application/json",
            "api-key": APIM_SUBSCRIPTION_KEY,
        }
        url = build_inference_url()

    with logger.stage("connecting", detail="Estableciendo conexión con APIM Gateway"):
        pass

    status_code = None
    reply_text = ""
    input_tokens = 0
    output_tokens = 0
    model_from_response = MODEL_DEPLOYMENT
    usage_raw = {}
    cost = None
    backend_latency_ms = 0.0

    try:
        with logger.stage("responses_api", detail="Enviando solicitud POST a APIM /inference"):
            backend_started = time.perf_counter()
            response = http_session.post(
                url,
                json=body,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            status_code = response.status_code
            backend_latency_ms = round((time.perf_counter() - backend_started) * 1000, 1)

        with logger.stage("waiting_model", detail=f"Backend respondió con status HTTP {status_code}"):
            if status_code >= 400:
                raise RuntimeError(f"APIM respondió HTTP {status_code}: {response.text[:400]}")
            data = response.json()

        with logger.stage("response_received", detail="Procesando respuesta JSON de Azure AI"):
            pass

        with logger.stage("processing_output", detail="Extrayendo texto y metadatos de tokens"):
            reply_text = (
                data.get("choices", [{}])[0].get("message", {}).get("content")
                or "(sin contenido en la respuesta)"
            )
            model_from_response = data.get("model", MODEL_DEPLOYMENT)
            usage_raw = data.get("usage") or {}
            input_tokens = usage_raw.get("prompt_tokens", 0) or 0
            output_tokens = usage_raw.get("completion_tokens", 0) or 0

        with logger.stage("calculating_cost", detail="Registrando uso y calculando costos en el sistema"):
            cost = record_usage(
                agent_id="apim-direct-trigger",
                agent_name=AGENT_NAME,
                model=model_from_response,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                response_id=str(uuid.uuid4()),
                conversation_id=_conversation_state.get("conversation_id"),
            )

        with logger.stage("sent_to_frontend", detail="Respuesta empaquetada exitosamente"):
            pass

        breakdown = logger.latency_breakdown()

        # Registro de métricas de red y métricas globales
        record_gateway_event(
            request_id=request_id,
            status_code=status_code,
            gateway_latency_ms=breakdown.get("gateway_latency_ms", 0.0),
            backend_latency_ms=backend_latency_ms,
            endpoint=url,
            model_used=model_from_response,
        )

        total_cost = cost.get("cost", {}).get("total", 0.0) if cost else 0.0
        record_metric("latency_ms", logger.total_duration_ms(), unit="ms", tags={"request_id": request_id, "source": "web-trigger"})
        record_metric("cost_usd", total_cost, unit="usd", tags={"request_id": request_id, "source": "web-trigger"})
        record_metric("tokens_total", input_tokens + output_tokens, unit="tokens", tags={"request_id": request_id, "source": "web-trigger"})

        usage_dict = {
            "model": model_from_response,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

        _persist_request_summary(logger, usage_dict, cost, breakdown, status="ok")

        # Guardar el árbol completo de eventos
        events_path = os.path.join(EVENTS_DIR, f"events_{request_id}.json")
        try:
            logger.dump_json(events_path)
        except Exception:
            traceback.print_exc()

        return jsonify(
            {
                "reply": reply_text,
                "request_id": request_id,
                "model": model_from_response,
                "usage": usage_dict,
                "cost": total_cost,
                "latency_ms": logger.total_duration_ms(),
                "tokens_were_real": bool(usage_raw),
            }
        )

    except Exception as exc:
        _persist_error(stage="responses_api", message=str(exc), request_id=request_id)
        breakdown = logger.latency_breakdown()

        try:
            record_gateway_event(
                request_id=request_id,
                status_code=status_code or 502,
                gateway_latency_ms=breakdown.get("gateway_latency_ms", 0.0),
                backend_latency_ms=breakdown.get("backend_latency_ms", 0.0),
                endpoint=url,
                model_used=MODEL_DEPLOYMENT,
            )
            usage_dict = {"model": MODEL_DEPLOYMENT, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            _persist_request_summary(logger, usage_dict, None, breakdown, status="error")
        except Exception:
            traceback.print_exc()

        return jsonify({"error": f"Error al procesar la solicitud en el gateway: {str(exc)}"}), 502


@app.route("/api/requests")
def api_requests_list():
    """
    Índice de solicitudes registradas por app2 (mismo formato que el
    /api/requests de app1), para que app1 pueda fusionarlas en Historial.
    """
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
    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return jsonify({"count": len(records), "requests": records[:limit]})


@app.route("/api/requests/<request_id>")
def api_request_detail(request_id):
    """Detalle (resumen + eventos) de una solicitud procesada por app2."""
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

    events_path = os.path.join(EVENTS_DIR, f"events_{request_id}.json")
    events_payload = None
    if os.path.exists(events_path):
        try:
            with open(events_path, "r", encoding="utf-8") as f:
                events_payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            events_payload = None

    if summary is None and events_payload is None:
        return jsonify({"error": f"No se encontró la solicitud '{request_id}'."}), 404

    return jsonify({
        "request_id": request_id,
        "summary": summary,
        "events": (events_payload or {}).get("events", []),
        "total_duration_ms": (events_payload or {}).get("total_duration_ms"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5090))
    app.run(host="0.0.0.0", port=port, debug=False)