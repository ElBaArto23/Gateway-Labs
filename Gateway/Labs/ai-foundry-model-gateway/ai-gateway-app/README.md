# AI Logistics Assistant — App "disparadora" para tu Model Gateway

Chat web sencillo (Flask + HTML/JS) que llama de verdad a tu **Azure API
Management (Model Gateway)** y registra cada llamada **en los mismos
archivos `.jsonl` que lee tu dashboard real** (`azure-ai-foundry-dashboard`),
reutilizando sin modificar tus propios módulos de `services/`
(`usage_tracker`, `gateway_metrics`, `metrics_collector`, `event_logger`,
`cost_calculator`).

```
Usuario → Esta app (chat) → Azure API Management (/inference) → Azure AI Foundry
                                 │
                                 ├─ usage_log.jsonl       (tokens y costo reales)
                                 ├─ gateway_log.jsonl     (latencia y status del gateway)
                                 ├─ metrics_log.jsonl     (métricas agregadas)
                                 ├─ requests_index.jsonl  (índice de solicitudes)
                                 └─ events_<id>.json      (detalle etapa por etapa)
```

Esta app **no tiene su propio dashboard**: es solo el punto donde escribes
la pregunta. La evidencia real (costo, tokens, latencia, salud) la revisas
abriendo tu dashboard existente (`azure-ai-foundry-dashboard`) por separado.

## 1. Requisitos

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Tu APIM ya configurado como Model Gateway hacia Azure AI Foundry

## 2. Instalación

```bash
cd ai-gateway-app
uv sync
```

## 3. Configuración (`.env`)

```
APIM_ENDPOINT=https://apim-iqmgwkcufxsvk.azure-api.net
APIM_SUBSCRIPTION_KEY=********
MODEL_DEPLOYMENT=gpt-5.4-mini
AGENT_NAME=my-test-agent
PORT=5060

# Carpeta REAL donde vive tu dashboard (azure-ai-foundry-dashboard).
# El "~" se expande automáticamente a tu carpeta de usuario del sistema.
DASHBOARD_DATA_DIR=~/OneDrive - Controles Empresariales SAS/Escritorio/PowerShellPractice2/AI-GATEWAY/AI-gateway/labs/ai-foundry-model-gateway/azure-ai-foundry-dashboard
```

De `DASHBOARD_DATA_DIR` se derivan automáticamente las rutas de
`usage_log.jsonl`, `gateway_log.jsonl`, `metrics_log.jsonl`,
`requests_index.jsonl`, `errors_log.jsonl` y la carpeta de `events_<id>.json`
— todas dentro de esa misma carpeta, para que tu dashboard las lea sin
ningún cambio de su lado.

⚠️ **Verifica esa ruta antes de correr la app.** Si no es exacta, los logs
se crean en un lugar donde tu dashboard no los va a encontrar. Puedes
confirmar hacia dónde está apuntando la app sin enviar ningún mensaje:

```bash
uv run app.py
# en otra terminal:
curl http://localhost:5060/api/health
```

La respuesta incluye `logs_target` con las 4 rutas resueltas — compáralas
con la ubicación real de tu carpeta `azure-ai-foundry-dashboard`.

## 4. Ejecutar

```bash
uv run app.py
```

Abre `http://localhost:5060` (puerto distinto al `5050` de tu dashboard,
para poder tener ambas apps corriendo a la vez sin choque).

## 5. Cómo probarlo

1. Corre esta app (`uv run app.py`) y abre `http://localhost:5060`
2. Escribe una consulta real (o usa los accesos rápidos: Error 502,
   Optimizar rutas, Reporte semanal)
3. Cada mensaje del asistente muestra un pie con `request_id`, tokens,
   latencia y costo — y si el gateway no devolvió `usage` real, lo marca
   explícitamente en vez de inventar un número
4. Abre por separado tu dashboard real (`azure-ai-foundry-dashboard`,
   típicamente en `localhost:5050`) y revisa ahí las métricas, el
   historial de solicitudes y el flujo del sistema — la llamada que
   acabas de hacer debe aparecer, marcada con `"source": "ai-logistics-assistant-web"`
   en `requests_index.jsonl` y `errors_log.jsonl` para diferenciarla del
   tráfico que genera tu propio dashboard.

## 6. Notas importantes

- **Tokens reales, no simulados**: esta app solo registra el `usage`
  (prompt/completion tokens) que devuelve Azure en la respuesta. Si el
  gateway no lo incluye, se registra 0 y se avisa en la interfaz — nunca
  se inventa un número, a diferencia del `DummyResponse` de
  `foundry_service.py` que sí hardcodea `input_tokens=15` / `output_tokens=25`
  en su ruta de respaldo (vale la pena que lo revises en tu app real).
- **Endpoint usado**: `{APIM_ENDPOINT}/inference` con header
  `Ocp-Apim-Subscription-Key`, igual que el fallback real de
  `foundry_service.send_message()`.
- **Seguridad**: el `.env` está en `.gitignore`. No lo subas a git — tiene
  tu API key real. Si esa key ya quedó expuesta en algún chat/repo, rótala
  desde Azure Portal.
- **Puertos**: esta app usa `5060` por defecto para no chocar con el
  `5050` de tu dashboard. Ajusta `PORT` en `.env` si lo necesitas distinto.

## 7. Estructura

```
ai-gateway-app/
├── app.py                        # Backend: /api/chat, /api/health
├── services/                     # Copia exacta de tus módulos reales
│   ├── usage_tracker.py
│   ├── gateway_metrics.py
│   ├── metrics_collector.py
│   ├── event_logger.py
│   └── cost_calculator.py
├── pyproject.toml / uv.lock
├── .env                          # Tus credenciales + DASHBOARD_DATA_DIR
├── templates/index.html
└── static/{css,js}/
```
