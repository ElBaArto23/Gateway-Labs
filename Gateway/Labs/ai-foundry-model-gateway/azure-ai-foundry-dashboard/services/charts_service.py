"""
services/charts_service.py
===========================
Genera estructuras de datos listas para graficar en el Frontend con ApexCharts.
"""

import json
import os
from typing import Any, Dict, List
from .usage_tracker import USAGE_LOG_PATH
from .gateway_metrics import GATEWAY_LOG_PATH


def generate_chart_data() -> Dict[str, Any]:
    """
    Genera series de tiempo y datos de distribución para todos los gráficos del Dashboard.
    """
    # 1. Inicializamos TODAS las listas que usará el diccionario
    timestamps: List[str] = []
    costs: List[float] = []
    tokens: List[int] = []
    input_tokens_list: List[int] = []
    output_tokens_list: List[int] = []
    model_distribution: Dict[str, float] = {}

    # 2. Lectura y extracción desde usage_log.jsonl (Costos y Tokens)
    if os.path.exists(USAGE_LOG_PATH):
        with open(USAGE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    # Timestamp amigable
                    ts = rec.get("timestamp", "")[:19].replace("T", " ")
                    timestamps.append(ts)
                    costs.append(rec.get("total_cost", 0.0))
                    tokens.append(rec.get("total_tokens", 0))

                    # Desglose de tokens de entrada y salida
                    input_tokens_list.append(rec.get("input_tokens", 0))
                    output_tokens_list.append(rec.get("output_tokens", 0))

                    # Distribución por modelo
                    model = rec.get("model_normalized", "desconocido")
                    model_distribution[model] = model_distribution.get(model, 0.0) + rec.get("total_cost", 0.0)
                except json.JSONDecodeError:
                    continue

    # Redondear costos acumulados por modelo
    for k in model_distribution:
        model_distribution[k] = round(model_distribution[k], 6)

    # 3. Lectura y extracción desde gateway_log.jsonl (Latencias del Gateway)
    #
    # IMPORTANTE: gateway_log.jsonl y usage_log.jsonl son archivos distintos,
    # escritos en pasos distintos del flujo (no toda solicitud que registra
    # uso pasa necesariamente por record_gateway_event, y viceversa), así que
    # NO tienen garantizado el mismo número de líneas. Por eso este bloque
    # arma su PROPIA lista de timestamps (gateway_timestamps) en vez de
    # reutilizar `timestamps` (que viene de usage_log.jsonl). Si antes se
    # reutilizaba `timestamps` como eje X de latency_series, un desfase de
    # tamaños entre los dos archivos hacía que ApexCharts emparejara cada
    # valor de latencia con la categoría en la misma POSICIÓN del arreglo,
    # en vez de con su fecha real -- si gateway_log.jsonl tenía menos
    # registros que usage_log.jsonl, la gráfica terminaba mostrando solo
    # las fechas más antiguas y nunca llegaba a la llamada más reciente.
    gateway_timestamps: List[str] = []
    gateway_latencies: List[float] = []
    backend_latencies: List[float] = []

    if os.path.exists(GATEWAY_LOG_PATH):
        with open(GATEWAY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    gw_ts = rec.get("timestamp", "")[:19].replace("T", " ")
                    gateway_timestamps.append(gw_ts)
                    gateway_latencies.append(rec.get("gateway_latency_ms", 0.0))
                    backend_latencies.append(rec.get("backend_latency_ms", 0.0))
                except json.JSONDecodeError:
                    continue

    # 4. Retorno estructurado (Todas las variables ya están definidas arriba)
    return {
        "time_series": {
            "labels": timestamps,
            "cost_series": costs,
            "token_series": tokens,
            "input_tokens": input_tokens_list,
            "output_tokens": output_tokens_list,
        },
        "cost_by_model_pie": {
            "labels": list(model_distribution.keys()),
            "series": list(model_distribution.values()),
        },
        "latency_series": {
            "labels": gateway_timestamps,
            "gateway_latency": gateway_latencies,
            "backend_latency": backend_latencies,
        },
    }