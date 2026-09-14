# AI Foundry Model Gateway — Dashboard + Chat en Vivo

Capa de visualización sobre el laboratorio de Azure AI Foundry ya validado.
No modifica infraestructura ni el notebook original: los envuelve para
mostrar en tiempo real cómo se procesa cada solicitud.

## Archivos

| Archivo           | Rol                                                                 |
|--------------------|----------------------------------------------------------------------|
| `index.html`       | Estructura: Dashboard, Chat en Vivo, Flujo, Arquitectura, Métricas, etc. |
| `style.css`        | Sistema de diseño (sidebar azul marino, tarjetas, tipografía).       |
| `script.js`        | Navegación, chat, panel de monitoreo, gráficos (Chart.js).           |
| `app.py`           | Backend Flask. Expone `POST /api/chat` y sirve el frontend.          |
| `event_logger.py`  | Event Logger: mide y registra cada etapa del procesamiento.          |

## Cómo se ve sin backend (modo offline)

Puedes abrir `index.html` directamente en el navegador, o correr `app.py`
sin haber conectado tu SDK todavía. El chat funcionará con **datos
simulados**: verás el timeline, el diagrama de flujo y las métricas
completos, pero las respuestas del agente serán de ejemplo. Esto sirve
para validar la interfaz mientras conectas el laboratorio real.

## Cómo conectar tu código real (sin reescribir el laboratorio)

1. Instala dependencias:
   ```bash
   pip install flask
   pip install azure-ai-projects azure-identity   # las que ya usa tu notebook
   ```
2. Abre `app.py` y busca los bloques marcados con `# TODO`. Ahí debes
   pegar (o importar) tus funciones ya validadas:
   - Autenticación y creación del `AIProjectClient`.
   - Recuperación del Persistent Agent.
   - Creación/reutilización de la Conversation.
   - Llamada a la Responses API y extracción del texto final.
3. **No borres los `with logger.stage(...)`.** Son los que alimentan el
   panel de monitoreo y el diagrama de flujo del frontend — deben envolver
   tu código real, no reemplazarlo.
4. Corre el backend:
   ```bash
   python app.py
   ```
5. Abre `http://localhost:5050`. En cuanto `/api/chat` responda con datos
   reales, el indicador del sidebar cambia a "Backend conectado" y el modo
   demo se desactiva automáticamente.

## Evolución sugerida

- Los eventos también se guardan en `events_<request_id>.json` en cada
  solicitud (tal como pide el prompt original), listos para evolucionar
  hacia streaming vía WebSocket o Server-Sent Events sin cambiar el
  formato que ya consume `script.js`.
- Las etapas (`STAGE_LABELS` en `event_logger.py` y `STAGES` en
  `script.js`) deben mantenerse sincronizadas si agregas o renombras pasos.
