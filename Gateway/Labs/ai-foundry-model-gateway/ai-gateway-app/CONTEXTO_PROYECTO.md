# Contexto del proyecto — AI Foundry Model Gateway (pegar esto si necesitas ayuda)

> Copia y pega todo este documento al inicio de una conversación nueva con
> Claude cuando necesites ayuda con este proyecto (un error, una duda, un
> cambio). Con esto tiene todo el contexto de una vez, sin tener que
> explicar todo desde cero.

## 1. Qué es este proyecto

Evidencia práctica (SENA / empresa) del patrón **Model Gateway**: todo el
tráfico de inferencia de IA pasa por **Azure API Management (APIM)** antes
de llegar a **Azure AI Foundry**, lo que permite aplicar ahí seguridad,
rate limiting, cuotas y observabilidad.

Hay **dos aplicaciones separadas**, cada una en su propia carpeta, cada
una con su propio `.venv` (gestionado con `uv`):

### App 1 — Dashboard real (ya existía, la principal)
- Carpeta: `labs/ai-foundry-model-gateway/azure-ai-foundry-dashboard/`
- Puerto: `5050`
- Stack: Flask + HTML/JS simple, con módulos en `services/`
  (`usage_tracker.py`, `gateway_metrics.py`, `metrics_collector.py`,
  `event_logger.py`, `cost_calculator.py`, `dashboard_service.py`,
  `foundry_service.py`)
- Usa **Microsoft Agent Framework** (modos "semantic" y "agentic") y
  también puede caer a una llamada directa a APIM si el SDK falla por DNS
- Lee/escribe archivos `.jsonl` locales en su propia carpeta:
  `usage_log.jsonl`, `gateway_log.jsonl`, `metrics_log.jsonl`,
  `requests_index.jsonl`, `errors_log.jsonl`, más `events_<id>.json` por
  cada solicitud
- Esta es la app donde se **ven** las métricas: costo, tokens, latencia,
  salud del gateway, "Flujo del Sistema", etc.

### App 2 — "AI Logistics Assistant" (la que construimos con Claude)
- Carpeta: `labs/ai-foundry-model-gateway/ai-gateway-app/`
- Puerto: `5060` (a propósito distinto del 5050, para correr ambas a la vez)
- Stack: Flask + HTML/JS simple, gestionado con `uv` (`pyproject.toml` +
  `uv.lock`)
- **No tiene dashboard propio.** Es solo un chat "disparador": la persona
  pregunta ahí, la pregunta viaja real por APIM → Azure AI Foundry, y el
  resultado se registra en los **mismos** archivos `.jsonl` de la App 1
  (copiamos sus módulos `services/*.py` tal cual, sin modificarlos, para
  garantizar el mismo formato)
- La ruta a la carpeta de la App 1 se configura en `.env` con
  `DASHBOARD_DATA_DIR` (soporta `~` para la carpeta de usuario)
- Cada entrada que escribe esta app lleva `"source": "ai-logistics-assistant-web"`
  en `requests_index.jsonl` / `errors_log.jsonl`, para diferenciarla del
  tráfico que genera la App 1 por sí sola

**Flujo de uso normal**: corres la App 2, preguntas algo real, y luego
abres la App 1 (dashboard) para ver esa consulta reflejada con sus
métricas.

## 2. Detalles técnicos clave

- **Endpoint de APIM usado por la App 2**: `{APIM_ENDPOINT}/inference`
  (POST), header `Ocp-Apim-Subscription-Key`. Es el mismo patrón que usa
  el fallback real de `foundry_service.send_message()` en la App 1.
- **APIM_ENDPOINT**: `https://apim-iqmgwkcufxsvk.azure-api.net`
- **Modelo**: `gpt-5.4-mini` (también existe `gpt-5.4` en
  `cost_calculator.py`, con otro precio)
- **Precios** (en `services/cost_calculator.py`, por 1M tokens):
  - `gpt-5.4-mini`: input $0.75 / output $4.50
  - `gpt-5.4`: input $2.50 / output $15.00
- **Tokens reales, nunca simulados**: la App 2 solo registra el `usage`
  que devuelve Azure. Si no viene, deja 0 y lo avisa en el chat — no
  inventa números.

## 3. ⚠️ Cosas ya detectadas que hay que tener presentes

1. **`foundry_service.py` (App 1) tiene un fallback `DummyResponse` que
   hardcodea `input_tokens=15` / `output_tokens=25`** cuando el SDK de
   Azure falla y no hay forma de leer `usage` real de la respuesta HTTP
   directa. Si algún día ves números de tokens sospechosamente iguales
   en el dashboard, es por esto — no es un bug de la App 2.
2. **API keys hardcodeadas encontradas en el código fuente** (no en
   `.env`) dentro de `foundry_service.py` original. Si compartes ese
   código o lo subes a git, rota esa key desde Azure Portal.
3. **El `.env` de la App 2 tiene la subscription key real en texto
   plano.** Está en `.gitignore`, pero si en algún momento se pegó en un
   chat o repo público, rótala también.
4. **Los archivos `.jsonl` son locales, no leen Application Insights de
   Azure directamente.** Si algún día se agrega otra fuente de tráfico
   (por ejemplo, alguien llamando a APIM desde Postman), esa llamada NO
   va a aparecer en el dashboard, porque el dashboard solo ve lo que
   pasa por esos archivos locales.

## 4. Cómo correr cada app

```bash
# App 1 (dashboard real)
cd labs/ai-foundry-model-gateway/azure-ai-foundry-dashboard
python app.py            # puerto 5050 (revisar si usa venv/pip o uv)

# App 2 (chat disparador)
cd labs/ai-foundry-model-gateway/ai-gateway-app
uv sync
uv run app.py             # puerto 5060
```

Para confirmar que la App 2 apunta a la carpeta correcta antes de mandar
mensajes:
```bash
curl http://localhost:5060/api/health
```
Revisa el campo `logs_target` — deben ser rutas dentro de
`azure-ai-foundry-dashboard/`.

## 5. Errores comunes y qué hacer

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `HTTP 404` al preguntar en la App 2 | `MODEL_DEPLOYMENT` o la ruta `/inference` no coincide con cómo está configurado tu APIM | Revisar en Azure Portal (APIM → APIs) el nombre exacto del deployment y la ruta expuesta; ajustar `MODEL_DEPLOYMENT` en `.env` |
| `HTTP 401/403` | La subscription key no es válida o no tiene permiso sobre esa API | Verificar `APIM_SUBSCRIPTION_KEY` en `.env`; revisar en APIM que la key pertenece a un producto con acceso a esa API |
| `Host not in allowlist` | Estás probando desde un entorno con egress restringido (ej. un sandbox) | Solo pasa fuera de tu máquina real; en tu PC con internet normal no debería aparecer |
| La consulta se ve en la App 2 pero no en el dashboard (App 1) | `DASHBOARD_DATA_DIR` mal escrito, o la App 1 lee de una carpeta distinta | Comparar `logs_target` de `/api/health` (App 2) contra la ruta real donde corres la App 1; revisar si la App 1 usa rutas relativas (`usage_log.jsonl` sin carpeta) y en qué directorio la ejecutas |
| Puerto ocupado (`Address already in use`) | Las dos apps usan el mismo puerto, o ya hay un proceso corriendo | Confirmar que la App 1 usa `5050` y la App 2 usa `5060`; si sigue, buscar el proceso viejo (`Get-Process -Id (Get-NetTCPConnection -LocalPort 5060).OwningProcess` en PowerShell) y cerrarlo |
| Tokens en 0 aunque la respuesta llegó bien | El JSON de respuesta de APIM no trae el campo `usage` | Revisar en Azure Portal / Application Insights cómo responde esa ruta; puede que la policy de APIM esté recortando el `usage` de la respuesta |
| `uv sync` falla | Versión de Python muy vieja, o `uv` no está instalado | Confirmar `python --version` (necesita 3.10+) y que `uv` esté en el PATH |

## 6. Si necesitas pedir ayuda con un error puntual

Pega, además de este documento:
1. El mensaje de error completo (de la terminal o de la respuesta JSON)
2. Qué app estabas usando (App 1 o App 2) y qué botón/acción disparó el error
3. El resultado de `curl http://localhost:5060/api/health` (si es la App 2)
4. Si el error es sobre datos que no cuadran, la línea exacta del
   `.jsonl` correspondiente (`usage_log.jsonl`, `gateway_log.jsonl`, etc.)
