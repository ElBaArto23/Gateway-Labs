"""
foundry_service.py
===================
Cliente hacia Azure AI Foundry (Persistent Agents / Responses API).

Toda la configuración sensible viene de variables de entorno -- nada
hardcodeado. Si algo falla (auth, red, agente inexistente), este módulo
LANZA la excepción en vez de inventar una respuesta -- el manejo de
errores de app.py (EventLogger + /api/issues) depende de que los
fallos reales se propaguen, no de que se enmascaren con texto genérico.

Variables de entorno usadas
----------------------------
    AI_FOUNDRY_PROJECT_ENDPOINT   (obligatoria)
    AGENT_NAME                    (opcional, por defecto "my-test-agent")
                                   -- debe coincidir con la misma variable
                                   que usa app.py, no una distinta.
"""

from __future__ import annotations

import os
from typing import Optional

PROJECT_ENDPOINT = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")
AGENT_NAME = os.environ.get("AGENT_NAME", "my-test-agent")

_project_client = None
_openai_client = None
_agent_cache = None


def get_project_client():
    """
    Conecta contra Azure AI Foundry usando DefaultAzureCredential.
    No hay fallback silencioso: si falla, se propaga la excepción real
    para que se vea en los logs y en /api/issues.
    """
    global _project_client
    if _project_client is None:
        if not PROJECT_ENDPOINT:
            raise RuntimeError(
                "Falta la variable de entorno AI_FOUNDRY_PROJECT_ENDPOINT. "
                "Configúrala en App Service > Environment variables."
            )

        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        _project_client = AIProjectClient(
            endpoint=PROJECT_ENDPOINT,
            credential=credential,
        )

    return _project_client


def get_agent(agent_name: str = AGENT_NAME):
    """Obtiene el Persistent Agent. Si no existe o falla, se propaga el error."""
    global _agent_cache
    if _agent_cache is None:
        client = get_project_client()
        _agent_cache = client.agents.get(agent_name=agent_name)
    return _agent_cache


def get_openai_client():
    global _openai_client
    if _openai_client is None:
        client = get_project_client()
        _openai_client = client.get_openai_client()
    return _openai_client


def create_or_reuse_conversation(conversation_id: Optional[str] = None) -> str:
    """Reutiliza el id de conversación si ya existe; si no, crea uno nuevo."""
    if conversation_id:
        return conversation_id
    openai_client = get_openai_client()
    return openai_client.conversations.create().id


def send_message(message: str, agent, conversation_id: str):
    """Envía el mensaje al agente vía Responses API. Sin fallback simulado."""
    openai_client = get_openai_client()
    return openai_client.responses.create(
        input=[{"role": "user", "content": message}],
        conversation=conversation_id,
        extra_body={
            "agent_reference": {"name": getattr(agent, "name", AGENT_NAME), "type": "agent_reference"}
        },
    )


def ask_agent(message: str, conversation_id: Optional[str] = None, agent_name: str = AGENT_NAME) -> dict:
    agent = get_agent(agent_name)
    conv_id = create_or_reuse_conversation(conversation_id)
    response = send_message(message, agent, conv_id)

    usage = getattr(response, "usage", None)

    return {
        "reply": getattr(response, "output_text", str(response)),
        "agent_id": getattr(agent, "id", None),
        "conversation_id": conv_id,
        "response_id": getattr(response, "id", None),
        "usage": {
            "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
        },
    }