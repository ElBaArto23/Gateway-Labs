"""
cost_calculator.py
==================

Calcula el costo estimado de una llamada realizada a Azure AI Foundry
utilizando los tokens consumidos y el modelo utilizado.

Responsabilidades:
- Mantener las tarifas por modelo.
- Calcular costo de input.
- Calcular costo de output.
- Calcular costo total.
No realiza llamadas a Azure.
No depende de Flask.
No interactúa con el frontend.
"""

from typing import Any, Dict


# Precio por 1 millón de tokens (USD)
MODEL_PRICING = {
    "gpt-5.4": {
        "input": 2.50,      
        "output": 15.00,
    },
    "gpt-5.4-mini": {
        "input": 0.75,
        "output": 4.50,
    },
}


def normalize_model_name(model: str) -> str:
    """
    Normaliza el nombre de un modelo/deployment al identificador base
    usado en MODEL_PRICING.
    """
    if not model:
        raise ValueError("Model name is empty or None")

    # Elimina el prefijo de conexión si existe.
    if "/" in model:
        model = model.split("/")[-1]

    # Match exacto primero.
    if model in MODEL_PRICING:
        return model

    # Match por prefijo, probando las claves más largas primero.
    for key in sorted(MODEL_PRICING.keys(), key=len, reverse=True):
        if model.startswith(key):
            return key

    raise ValueError(f"Unsupported model: {model}")


def get_model_pricing(model: str) -> Dict[str, float]:
    """
    Obtiene la configuración de precios para un modelo.

    Acepta tanto el nombre base ("gpt-5.4") como el nombre completo
    de deployment con fecha de versión ("gpt-5.4-2026-03-05").

    Raises:
        ValueError: Si el modelo no existe ni matchea por prefijo.
    """
    normalized = normalize_model_name(model)
    return MODEL_PRICING[normalized]


def calculate_input_cost(tokens: int, price_per_million: float) -> float:
    tokens = tokens or 0
    return (tokens / 1_000_000) * price_per_million


def calculate_output_cost(tokens: int, price_per_million: float) -> float:
    tokens = tokens or 0
    return (tokens / 1_000_000) * price_per_million


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> Dict[str, Any]:
    """
    Calcula el costo estimado de una llamada al modelo.
    """

    normalized_model = normalize_model_name(model)
    pricing = MODEL_PRICING[normalized_model]

    input_cost = calculate_input_cost(
        input_tokens,
        pricing["input"],
    )

    output_cost = calculate_output_cost(
        output_tokens,
        pricing["output"],
    )

    total_cost = input_cost + output_cost

    return {
        "model": model,
        "model_normalized": normalized_model,
        "currency": "USD",
        "pricing": {
            "input_per_million": pricing["input"],
            "output_per_million": pricing["output"],
        },
        "usage": {
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "cached_tokens": cached_tokens or 0,
            "reasoning_tokens": reasoning_tokens or 0,
            "total_tokens": (
                (input_tokens or 0)
                + (output_tokens or 0)
            ),
        },
        "cost": {
            "input": round(input_cost, 8),
            "output": round(output_cost, 8),
            "total": round(total_cost, 8),
        },
    }