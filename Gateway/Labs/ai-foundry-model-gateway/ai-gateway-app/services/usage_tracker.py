"""
usage_tracker.py
=================

Persiste el consumo de cada llamada al agente y ofrece agregaciones
(consumo total, por modelo, por agente, promedios) y un control de
presupuesto a nivel de aplicación.

A diferencia de cost_calculator.py (cálculo puro, sin I/O), este
módulo SÍ hace I/O: escribe/lee un archivo JSON Lines en disco.

Pensado para migrar después a Table Storage / Cosmos DB sin cambiar
la interfaz pública (record_usage, get_consumption_summary, check_budget).
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .cost_calculator import calculate_cost

USAGE_LOG_PATH = os.environ.get("USAGE_LOG_PATH", "usage_log.jsonl")

# Lock para mitigar condición de carrera al escribir el archivo bajo
# concurrencia (varias requests de Flask al mismo tiempo). No resuelve
# el problema de fondo -- un JSONL local no es apto para multi-proceso/
# multi-instancia -- pero evita líneas corruptas por escritura simultánea
# dentro del mismo proceso.
_write_lock = threading.Lock()


class BudgetExceededError(Exception):
    """Se lanza cuando check_budget() detecta que se superó el límite configurado."""
    pass


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def record_usage(
    agent_id: str,
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    response_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> Dict[str, Any]:
    """
    Calcula el costo de una llamada (vía cost_calculator) y persiste
    el registro en USAGE_LOG_PATH.

    Returns:
        El dict de costo devuelto por calculate_cost(), tal cual,
        para que quien llama pueda usarlo directamente en la respuesta
        de la API sin tener que volver a leer el archivo.
    """
    cost = calculate_cost(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )

    record = {
        "timestamp": _now_utc().isoformat(),
        "agent_id": agent_id,
        "agent_name": agent_name,
        "response_id": response_id,
        "conversation_id": conversation_id,
        "model": cost["model"],
        "model_normalized": cost["model_normalized"],
        "input_tokens": cost["usage"]["input_tokens"],
        "output_tokens": cost["usage"]["output_tokens"],
        "cached_tokens": cost["usage"]["cached_tokens"],
        "reasoning_tokens": cost["usage"]["reasoning_tokens"],
        "total_tokens": cost["usage"]["total_tokens"],
        "input_cost": cost["cost"]["input"],
        "output_cost": cost["cost"]["output"],
        "total_cost": cost["cost"]["total"],
    }

    with _write_lock:
        with open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return cost


def _read_records():
    """Generador que lee usage_log.jsonl línea por línea, ignorando líneas corruptas."""
    if not os.path.exists(USAGE_LOG_PATH):
        return

    with open(USAGE_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Línea corrupta (ej. escritura interrumpida) -- se ignora
                # en vez de tumbar toda la agregación.
                continue


def get_consumption_summary(
    agent_name: Optional[str] = None,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Agrega el consumo registrado en USAGE_LOG_PATH.
    """
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    call_count = 0
    total_tokens = 0
    total_cost = 0.0
    by_model: Dict[str, Dict[str, Any]] = {}

    for rec in _read_records():
        if agent_name is not None and rec.get("agent_name") != agent_name:
            continue

        if since is not None:
            try:
                rec_ts = datetime.fromisoformat(rec["timestamp"])
            except (KeyError, ValueError):
                continue
            if rec_ts < since:
                continue

        call_count += 1
        total_tokens += rec.get("total_tokens", 0)
        total_cost += rec.get("total_cost", 0.0)

        model_key = rec.get("model_normalized", rec.get("model", "unknown"))
        bucket = by_model.setdefault(
            model_key,
            {"call_count": 0, "total_tokens": 0, "total_cost": 0.0},
        )
        bucket["call_count"] += 1
        bucket["total_tokens"] += rec.get("total_tokens", 0)
        bucket["total_cost"] += rec.get("total_cost", 0.0)

    for bucket in by_model.values():
        bucket["total_cost"] = round(bucket["total_cost"], 8)
        bucket["average_cost_per_call"] = (
            round(bucket["total_cost"] / bucket["call_count"], 8)
            if bucket["call_count"] else 0.0
        )

    return {
        "agent_name": agent_name,
        "since": since.isoformat() if since else None,
        "call_count": call_count,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 8),
        "average_cost_per_call": round(total_cost / call_count, 8) if call_count else 0.0,
        "average_tokens_per_call": round(total_tokens / call_count, 2) if call_count else 0.0,
        "by_model": by_model,
    }


def check_budget(
    agent_name: str,
    max_cost_usd: Optional[float] = None,
    max_tokens: Optional[int] = None,
    window_minutes: int = 60,
) -> None:
    """
    Verifica si el consumo de un agente en la última ventana de tiempo
    supera el límite configurado. No hace nada si no lo supera.
    """
    since = _now_utc() - timedelta(minutes=window_minutes)
    summary = get_consumption_summary(agent_name=agent_name, since=since)

    if max_cost_usd is not None and summary["total_cost"] >= max_cost_usd:
        raise BudgetExceededError(
            f"Agente '{agent_name}' superó el presupuesto de "
            f"${max_cost_usd:.4f} USD en los últimos {window_minutes} min "
            f"(consumido: ${summary['total_cost']:.4f} USD, "
            f"{summary['call_count']} llamadas)."
        )

    if max_tokens is not None and summary["total_tokens"] >= max_tokens:
        raise BudgetExceededError(
            f"Agente '{agent_name}' superó el límite de {max_tokens} tokens "
            f"en los últimos {window_minutes} min "
            f"(consumido: {summary['total_tokens']} tokens, "
            f"{summary['call_count']} llamadas)."
        )
