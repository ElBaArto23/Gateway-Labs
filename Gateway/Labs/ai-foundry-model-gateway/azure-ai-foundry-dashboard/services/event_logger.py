"""
event_logger.py
================
Mecanismo de registro de eventos (Event Logger) descrito en el prompt de
contexto. NO contiene lógica del laboratorio: solo mide tiempos y guarda
el estado de cada etapa para que el frontend pueda representarla.

Uso típico dentro de app.py:

    logger = EventLogger()
    with logger.stage("connecting"):
        client = get_ai_project_client()   # tu código ya validado

    events = logger.as_list()              # para responder al frontend
    logger.dump_json("events.json")        # persistencia simple en disco
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Debe coincidir 1:1 con el arreglo STAGES definido en script.js
STAGE_LABELS = {
    "received": "Solicitud recibida",
    "validating": "Validando parámetros",
    "preparing": "Preparando solicitud",
    "connecting": "Conectando con Azure AI Foundry",
    "agent_lookup": "Recuperando Persistent Agent",
    "conversation": "Creando o reutilizando Conversation",
    "responses_api": "Enviando solicitud mediante Responses API",
    "waiting_model": "Esperando respuesta del modelo",
    "response_received": "Respuesta recibida",
    "processing_output": "Procesando salida",
    "calculating_cost": "Calculando costo estimado",
    "sent_to_frontend": "Respuesta enviada al frontend",
}

# Clasificación de etapas para separar "tiempo del gateway/Flask" de
# "tiempo esperando al modelo en Azure AI Foundry". Debe coincidir 1:1
# con INTERNAL_STAGE_NAMES / MODEL_STAGE_NAMES en script.js.
INTERNAL_STAGES = {
    "received", "validating", "preparing", "connecting",
    "agent_lookup", "conversation", "sent_to_frontend", "calculating_cost",
}
MODEL_STAGES = {
    "responses_api", "waiting_model", "response_received", "processing_output",
}


@dataclass
class Event:
    stage: str
    label: str
    status: str  # "active" | "done" | "error"
    started_at: str
    detail: str = ""
    duration_ms: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EventLogger:
    """Acumula eventos de una sola ejecución (una pregunta al agente)."""

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    events: list[Event] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    @contextmanager
    def stage(self, stage_key: str, detail: str = ""):
        """
        Context manager para medir e instrumentar una etapa sin tocar
        la lógica interna. Ejemplo:

            with logger.stage("agent_lookup", "Buscando agente por nombre"):
                agent = project_client.agents.get(agent_name)
        """
        label = STAGE_LABELS.get(stage_key, stage_key)
        t0 = time.perf_counter()
        started_at = self._now()
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier error del laboratorio
            duration_ms = (time.perf_counter() - t0) * 1000
            ev = Event(stage_key, label, "error", started_at, str(exc), duration_ms)
            self.events.append(ev)
            self.errors.append({"stage": stage_key, "message": str(exc), "time": self._now()})
            raise
        else:
            duration_ms = (time.perf_counter() - t0) * 1000
            ev = Event(stage_key, label, "done", started_at, detail, duration_ms)
            self.events.append(ev)

    def log_instant(self, stage_key: str, detail: str = "", status: str = "done") -> None:
        """Para eventos que no envuelven un bloque de código (p. ej. checkpoints simples)."""
        label = STAGE_LABELS.get(stage_key, stage_key)
        self.events.append(Event(stage_key, label, status, self._now(), detail, 0.0))

    def as_list(self) -> list[dict]:
        return [e.to_dict() for e in self.events]

    def total_duration_ms(self) -> float:
        return sum(e.duration_ms or 0 for e in self.events)

    def latency_breakdown(self) -> dict:
        """
        Separa la duración total en tiempo interno (Flask/gateway) vs.
        tiempo esperando al modelo (Azure AI Foundry), usando la misma
        clasificación de etapas que el frontend.
        """
        internal_ms = 0.0
        model_ms = 0.0
        for e in self.events:
            duration = e.duration_ms or 0
            if e.stage in MODEL_STAGES:
                model_ms += duration
            elif e.stage in INTERNAL_STAGES:
                internal_ms += duration
        return {
            "gateway_latency_ms": round(internal_ms, 2),
            "backend_latency_ms": round(model_ms, 2),
        }

    def dump_json(self, path: str | Path = "events.json") -> None:
        """Persistencia simple en disco (paso previo a WebSocket en tiempo real)."""
        payload = {
            "request_id": self.request_id,
            "generated_at": self._now(),
            "events": self.as_list(),
            "total_duration_ms": self.total_duration_ms(),
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")