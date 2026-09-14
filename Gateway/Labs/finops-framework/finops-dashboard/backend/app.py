"""
FinOps Framework Dashboard - Backend
-------------------------------------
Consulta el mismo Log Analytics workspace que ya usa el lab
'finops-framework' (APIM + Cost Management), reutilizando la lógica KQL
que ya existe en main.bicep para las reglas de alerta de suspensión.

Autenticación: DefaultAzureCredential -> usa la Managed Identity del
App Service en producción, o `az login` / variables de entorno en local.

Requiere que la identidad del App Service tenga el rol
"Log Analytics Reader" sobre el Log Analytics Workspace del lab.
"""

import os
import json
import time
import uuid
import logging
from datetime import timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from azure.core.exceptions import HttpResponseError
from openai import AzureOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("finops-dashboard")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# customerId (GUID) del Log Analytics Workspace del lab.
# Es el mismo valor que expone el output `logAnalyticsWorkspaceId` del
# main.bicep (lawModule.outputs.customerId).
WORKSPACE_ID = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")

# Ventana de tiempo por defecto para las queries (en horas)
DEFAULT_TIMESPAN_HOURS = int(os.environ.get("DEFAULT_TIMESPAN_HOURS", "12"))

# --- Config del APIM compartido para el panel "Probar Gateway" ---
# apim_resource_gateway_url es un output del deployment (Cell 8 del notebook).
APIM_GATEWAY_URL = os.environ.get("APIM_GATEWAY_URL", "")

# NUNCA usar un default genérico aquí (ej. "inference") — ese path es
# compartido por el APIM y colisiona con otros labs (root cause del
# incidente donde este lab pisó/fue pisado por otro). Cada lab DEBE tener
# su propio path aislado (ej. "finops-framework-inference"), seteado
# explícitamente vía app setting INFERENCE_API_PATH en el App Service.
INFERENCE_API_PATH = os.environ.get("INFERENCE_API_PATH")
if not INFERENCE_API_PATH:
    raise RuntimeError(
        "INFERENCE_API_PATH no está configurado en el App Service. "
        "Debe apuntar a la API aislada de este lab en el APIM compartido "
        "(ej. 'finops-framework-inference'), nunca a un path genérico como "
        "'inference', ya que eso colisiona con otros labs que comparten el "
        "mismo APIM."
    )

INFERENCE_API_VERSION = os.environ.get("INFERENCE_API_VERSION", "2025-03-01-preview")

# ApiId de la API de este lab en el APIM COMPARTIDO (mismo valor que
# `inferenceAPIName` en main.bicep — "Unique API resource id in the shared
# APIM - MUST be unique across all labs"). ApiManagementGatewayLogs /
# ApiManagementGatewayLlmLog reciben tráfico de TODOS los labs que comparten
# ese APIM y ese Log Analytics workspace, no solo de finops-framework — sin
# este filtro, los gráficos de tokens/costo mezclan datos de otros labs
# (visto en producción el 07/09/2026: "Tokens consumidos en el tiempo"
# mostraba series de hosted-agents-product y gemini-models-product además de
# las de finops-framework). El filtro vive en llm_logs_base()/
# llm_logs_month_to_date() (compartidas por todos los endpoints), no acá.
INFERENCE_API_ID = os.environ.get("INFERENCE_API_ID", "finops-framework-inference-api")

# Modelos deployados en el Foundry compartido (Cell 2 del notebook,
# models_config). Configurable por si cambian.
AVAILABLE_MODELS = [
    m.strip() for m in os.environ.get("AVAILABLE_MODELS", "gpt-5.4-mini,gpt-5.4,DeepSeek-V3.2").split(",") if m.strip()
]

# Subscription keys del APIM compartido. NUNCA se exponen al frontend.
# Formato esperado en el app setting APIM_SUBSCRIPTIONS_JSON:
#   {"subscription1": "key1...", "subscription2": "key2...", ...}
# Se cargan una sola vez al arrancar el proceso, desde el output
# `apimSubscriptions` del deployment de Bicep (Cell 8/17 del notebook).
try:
    APIM_SUBSCRIPTIONS = json.loads(os.environ.get("APIM_SUBSCRIPTIONS_JSON", "{}"))
except json.JSONDecodeError:
    logger.error("APIM_SUBSCRIPTIONS_JSON no es JSON válido — el panel de chat quedará deshabilitado.")
    APIM_SUBSCRIPTIONS = {}

# Suscripciones que se excluyen de TODO el reporting del dashboard
# (totales, desgloses por unidad de negocio y "Estado de unidades de
# negocio"), aunque sigan siendo utilizables desde "Probar Gateway".
# `shared-subscription` no está vinculada a ningún Product en APIM (scope
# directo a la API), por eso ProductId venía vacío en los logs y se veía
# como "sin producto" en la leyenda de "Tokens por unidad de negocio en
# el tiempo". Por decisión explícita (01/09/2026) se filtra desde la raíz
# (llm_logs_base / llm_logs_month_to_date) para que no aparezca en NINGÚN
# endpoint de métricas, ni siquiera sumando a los totales generales.
# `master` es la subscription key maestra built-in de APIM (acceso total,
# sin Product asociado) — cualquier llamada hecha con esa key (pruebas,
# health checks) cae acá con el mismo síntoma que shared-subscription
# (ProductId vacío). Se excluye por la misma razón (visto en producción el
# 07/09/2026, junto con el fix de ApiId de INFERENCE_API_ID más abajo).
EXCLUDED_SUBSCRIPTIONS = {"shared-subscription", "master"}


def _excluded_subscriptions_values() -> str:
    """Lista de EXCLUDED_SUBSCRIPTIONS entrecomillada para un `!in (...)` de
    KQL, ej. `"shared-subscription"`. Cadena vacía si no hay exclusiones."""
    return ", ".join(f'"{s}"' for s in sorted(EXCLUDED_SUBSCRIPTIONS))


def _exclusion_where_clause(column: str = "SubscriptionName") -> str:
    """Línea `| where <column> !in (...)` para insertar al final de un
    bloque KQL, filtrando EXCLUDED_SUBSCRIPTIONS por la columna indicada
    (`SubscriptionName` en llm_logs_base()/llm_logs_month_to_date(),
    `Subscription` en el join contra SUBSCRIPTION_QUOTA_CL de
    costs_by_subscription()). Devuelve "" si EXCLUDED_SUBSCRIPTIONS está
    vacío, para no dejar un `!in ()` inválido en la KQL."""
    values = _excluded_subscriptions_values()
    if not values:
        return ""
    return f"| where {column} !in ({values})\n        "


credential = DefaultAzureCredential()
logs_client = LogsQueryClient(credential)

app = FastAPI(title="FinOps Framework Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajustar en producción al dominio del App Service
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_kql(query: str, timespan_hours: float = DEFAULT_TIMESPAN_HOURS):
    """Ejecuta una query KQL contra el workspace del lab y devuelve filas como dicts."""
    if not WORKSPACE_ID:
        raise HTTPException(
            status_code=500,
            detail="LOG_ANALYTICS_WORKSPACE_ID no está configurado en el App Service.",
        )
    # Log Analytics/Kusto cachea el resultado de queries IDÉNTICAS (mismo
    # texto + mismo workspace + mismo timespan) por unos minutos. Las
    # queries de este dashboard son deterministas — el mismo texto sale en
    # cada request para los mismos parámetros (ej. costs_by_subscription
    # con month_to_date=True siempre arma la misma query) — así que sin
    # esto, un cambio de datos recién ingerido (ej. un CostQuota nuevo
    # subido a SUBSCRIPTION_QUOTA_CL vía quota_control.py) puede tardar
    # mucho más de lo esperado en reflejarse en el dashboard, porque el
    # motor devuelve el resultado cacheado de la primera vez que corrió
    # esa query exacta en vez de re-evaluarla contra los datos actuales.
    # Detectado el 07/09/2026: una query manual en el portal (texto
    # distinto cada vez) sí mostraba el CostQuota recién subido, pero el
    # endpoint del dashboard (mismo texto siempre) seguía devolviendo el
    # valor viejo. `set query_results_cache_max_age=time(0)` desactiva ese
    # cache para esta query puntual.
    # FIX 07/09/2026 (2da vuelta): el `set query_results_cache_max_age=time(0)`
    # de abajo no está garantizando resultados frescos en producción — puede
    # que Log Analytics no respete ese `set` statement igual que un cluster
    # nativo de Kusto/ADX (lo ignora en silencio, sin error). La única forma
    # 100% robusta de evitar que el motor reuse un resultado cacheado es que
    # el TEXTO de la query nunca sea idéntico entre llamadas — por eso se
    # agrega acá un comentario con un UUID único en cada ejecución. Esto es
    # justamente por lo que las queries manuales en el portal (texto distinto
    # cada vez) siempre devolvían datos frescos mientras el endpoint del
    # dashboard (mismo texto siempre) no.
    # El comentario "// cache-buster:<uuid>" no alcanzó (confirmado el
    # 07/09/2026: /api/debug/quota-raw seguía devolviendo filas de hace 3
    # días) — el motor probablemente ignora comentarios al decidir si el
    # texto de la query es "igual al de la última vez". Un `print` real
    # (no comentario) con el UUID, como statement separado antes de la
    # query de verdad, no se puede normalizar/ignorar de la misma forma.
    query = (
        f"set query_results_cache_max_age=time(0);\n"
        f'print _cache_buster_uuid = "{uuid.uuid4()}";\n'
    ) + query
    try:
        response = logs_client.query_workspace(
            workspace_id=WORKSPACE_ID,
            query=query,
            # OJO: acá NO se pasa timedelta(hours=timespan_hours). El
            # parámetro `timespan` del SDK restringe TODAS las tablas
            # referenciadas en la query que tengan columna TimeGenerated —
            # incluida PRICING_CL, no solo ApiManagementGatewayLlmLog.
            # PRICING_CL casi nunca tiene una fila nueva dentro de una
            # ventana corta (1h, 12h), así que su `arg_max(TimeGenerated, *)
            # by Model` quedaba vacío para esas ventanas, y el
            # `join kind=inner` con eso vaciaba TODO el resultado aunque los
            # logs de esa ventana sí existieran (bug real: hours=1/12 daban
            # 0, hours=24 sí traía datos, porque coincidía con la última
            # actualización de precios). El filtro real por rango pedido
            # ahora vive explícito en el texto de la query, con ago(), en
            # llm_logs_base() — acá solo dejamos una ventana amplia fija
            # para no limitar de más al SDK.
            timespan=timedelta(days=90),
        )
    except HttpResponseError as e:
        logger.error("Error consultando Log Analytics: %s", e)
        raise HTTPException(status_code=502, detail=f"Error consultando Log Analytics: {e.message}")

    if response.status != LogsQueryStatus.SUCCESS:
        raise HTTPException(status_code=502, detail="La query a Log Analytics falló o devolvió resultados parciales.")

    # El statement "print" del cache-buster (arriba) genera su propia tabla
    # de resultado, que la API devuelve como la PRIMERA de response.tables.
    # La tabla que realmente importa (el resultado de la query real) es
    # siempre la ÚLTIMA, sin importar cuántos statements previos (set/print)
    # hayan generado tablas propias.
    table = response.tables[-1]
    columns = table.columns
    return [dict(zip(columns, row)) for row in table.rows]


def llm_logs_base(hours: float) -> str:
    """
    Base compartida: filtra los logs de APIM al rango solicitado (por
    `hours`, con ago() explícito en la KQL) y los une con el subscription
    id. Antes este filtro era un `where TimeGenerated >= startofmonth(now())`
    fijo, y la restricción real por `hours` se delegaba al parámetro
    `timespan` del SDK — eso era lo que rompía PRICING_CL para ventanas
    cortas (ver nota en run_kql). Ahora el filtro por `hours` vive acá,
    aplicado SOLO a esta tabla, no a PRICING_CL.

    IMPORTANTE: esta base es a propósito una ventana RODANTE de `hours`,
    pensada para el gráfico de tendencia ("Tokens consumidos en el
    tiempo") y para las tarjetas del Panel Operativo, donde el usuario
    elige el rango a mirar. NO usar esta base para decidir si una
    suscripción está sobre cuota — para eso existe `llm_logs_month_to_date()`
    más abajo, que es la ventana que realmente corresponde a CostQuota
    (cuota MENSUAL). Mezclar ambas fue la causa raíz de que
    `/api/chat` y el panel "Estado de unidades de negocio" mostraran una
    suscripción como excedida (o no) según el `hours` de turno, en vez de
    según el gasto real acumulado del mes (bug detectado el 21/08/2026:
    Unidad de Negocio D aparecía con TotalCost=$0.083 en una ventana de
    12h, muy por encima de su TotalCost mensual real de ~$0.024).

    IMPORTANTE (fix 21/08/2026, 2da vuelta): `ApiManagementGatewayLogs`
    puede tener mas de una fila por `CorrelationId` (reintentos, filas de
    request/response separadas, etc.). Un `join kind=leftouter` directo
    contra esa tabla multiplica cada fila de `ApiManagementGatewayLlmLog`
    por la cantidad de filas coincidentes del lado derecho, y el costo de
    ese mensaje termina sumado varias veces en el `summarize sum(...)`
    posterior. Confirmado con `summarize RowCount = count() by
    CorrelationId` sobre el join: subscription4 y subscription3 (las mas
    usadas en pruebas) tenian CorrelationId con RowCount > 1, mientras que
    subscription1/2 no -- coincide con el patron de inflacion observado
    (subscription4: $0.127 mostrado vs $0.0241 real). Por eso acá
    deduplicamos `ApiManagementGatewayLogs` por `CorrelationId` ANTES del
    join, quedandonos con una sola fila representativa (arg_max por
    TimeGenerated). Validado contra la query de referencia manual: los 4
    totales coinciden ahora a nivel de centesimas de centavo.

    IMPORTANTE (fix 01/09/2026): se excluyen acá, en la raíz, las
    suscripciones de EXCLUDED_SUBSCRIPTIONS (ver definición arriba) — por
    ejemplo `shared-subscription`, que no está vinculada a ningún Product
    en APIM. Al filtrar en esta base compartida por TODOS los endpoints,
    quedan afuera de absolutamente todo: tarjetas de totales, gráficos por
    unidad de negocio y "Estado de unidades de negocio".

    IMPORTANTE (fix 07/09/2026): `ApiManagementGatewayLogs` y
    `ApiManagementGatewayLlmLog` reciben tráfico de TODOS los labs que
    comparten el mismo APIM y el mismo Log Analytics workspace, no solo de
    finops-framework — confirmado en producción: "Tokens consumidos en el
    tiempo" mostraba series de `hosted-agents-product`/`gemini-models-product`
    de otros labs, mezcladas con las de este. Antes SOLO se filtraba por
    `SubscriptionName` (vía EXCLUDED_SUBSCRIPTIONS / el join con
    SUBSCRIPTION_QUOTA_CL en costs_by_subscription), lo cual protegía a las
    vistas que hacen ese join pero NO a tokens_by_subscription() ni
    tokens_timeseries(), que agrupan directo por SubscriptionName sin cruzar
    contra SUBSCRIPTION_QUOTA_CL. El filtro correcto es por `ApiId`
    (INFERENCE_API_ID, único por lab — ver comentario junto a su definición),
    aplicado acá en la base compartida por todos los endpoints. Cambiar el
    join de `leftouter` a `inner` es intencional: descarta cualquier fila de
    ApiManagementGatewayLlmLog cuyo CorrelationId no matchee un gateway log
    de ESTA API — que es exactamente lo que se busca (afuera lo de otros
    labs), pero como efecto secundario también descartaría una llamada
    legítima de este lab si por algún motivo de timing de ingesta su
    CorrelationId nunca aparece en ApiManagementGatewayLogs. Si después de
    este fix el conteo total de llamadas/tokens baja más de lo esperado,
    revisar si es por esto antes de asumir que es solo la limpieza de datos
    de otros labs.
    """
    minutes = max(1, round(hours * 60))
    return f"""
    let llmHeaderLogs = ApiManagementGatewayLlmLog
        | where TimeGenerated >= ago({minutes}m)
        | where DeploymentName != '';
    let gatewayLogsDedup = ApiManagementGatewayLogs
        | where ApiId has "{INFERENCE_API_ID}"
        | summarize arg_max(TimeGenerated, ApimSubscriptionId, ProductId) by CorrelationId;
    let llmLogsWithSubscriptionId = llmHeaderLogs
        | join kind=inner gatewayLogsDedup on CorrelationId
        | project
            TimeGenerated,
            SubscriptionName = ApimSubscriptionId,
            ProductName = ProductId,
            DeploymentName,
            PromptTokens,
            CompletionTokens,
            TotalTokens
        {_exclusion_where_clause()};
    """


def llm_logs_month_to_date() -> str:
    """
    Igual que `llm_logs_base()`, pero filtrando por MES CALENDARIO
    (startofmonth..endofmonth), no por una ventana rodante de horas. Esta
    es la ventana que corresponde de verdad a `CostQuota`, que es una
    cuota mensual (ver quota_control.py / README, sección 4: la query de
    referencia para recalibrar usa exactamente este mismo rango).

    Usar SIEMPRE esta base — nunca `llm_logs_base(hours)` — para calcular
    si una suscripción está sobre cuota (get_quota_remaining,
    get_quota_status, suggest_alternative_subscription, y el chequeo de
    `/api/chat`). El selector de "Últimas X horas" del dashboard debe
    afectar solo a los gráficos de tendencia, nunca al chequeo de cuota.

    IMPORTANTE (fix 21/08/2026, 2da vuelta): misma corrección que
    `llm_logs_base()` — `ApiManagementGatewayLogs` se deduplica por
    `CorrelationId` antes del join para evitar el fan-out que inflaba
    `total_cost` (ver docstring de `llm_logs_base()` para el detalle
    completo de la causa raíz y la validación).
    """
    return f"""
    let llmHeaderLogs = ApiManagementGatewayLlmLog
        | where TimeGenerated >= startofmonth(now()) and TimeGenerated <= endofmonth(now())
        | where DeploymentName != '';
    let gatewayLogsDedup = ApiManagementGatewayLogs
        | where ApiId has "{INFERENCE_API_ID}"
        | summarize arg_max(TimeGenerated, ApimSubscriptionId, ProductId) by CorrelationId;
    let llmLogsWithSubscriptionId = llmHeaderLogs
        | join kind=inner gatewayLogsDedup on CorrelationId
        | project
            TimeGenerated,
            SubscriptionName = ApimSubscriptionId,
            ProductName = ProductId,
            DeploymentName,
            PromptTokens,
            CompletionTokens,
            TotalTokens
        {_exclusion_where_clause()};
    """


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "workspace_configured": bool(WORKSPACE_ID)}


@app.get("/api/debug/quota-raw")
def debug_quota_raw():
    """
    TEMPORAL — diagnóstico del bug de cuota demo no reflejada. Corre SOLO la
    parte de la query que lee SUBSCRIPTION_QUOTA_CL (el arg_max por
    Subscription), sin el join contra costos, y devuelve las filas crudas
    con TimeGenerated. Sirve para comparar, lado a lado, exactamente lo que
    ve esta app (vía Managed Identity + LogsQueryClient) contra lo que se ve
    en el portal (usuario interactivo) para la MISMA tabla en el MISMO
    instante. Borrar este endpoint una vez resuelto el bug.
    """
    query = """
    SUBSCRIPTION_QUOTA_CL
    | summarize arg_max(TimeGenerated, *) by Subscription
    | project Subscription, CostQuota, TimeGenerated
    | order by Subscription asc
    """
    rows = run_kql(query)
    return {
        "workspace_id": WORKSPACE_ID,
        "server_utc_now": __import__("datetime").datetime.utcnow().isoformat(),
        "rows": [
            {
                "subscription": r.get("Subscription"),
                "cost_quota": r.get("CostQuota"),
                "time_generated": r.get("TimeGenerated").isoformat() if r.get("TimeGenerated") else None,
            }
            for r in rows
        ],
    }


@app.get("/api/summary")
def summary(hours: float = Query(DEFAULT_TIMESPAN_HOURS, ge=0.0333, le=720)):
    """Tarjetas superiores: gasto total, tokens totales, llamadas de hoy, salud del gateway."""
    query = llm_logs_base(hours) + """
    llmLogsWithSubscriptionId
    | join kind=inner (
        PRICING_CL
        | summarize arg_max(TimeGenerated, *) by Model
        | project Model, InputTokensPrice, OutputTokensPrice
        )
        on $left.DeploymentName == $right.Model
    | extend InputCost = PromptTokens * InputTokensPrice
    | extend OutputCost = CompletionTokens * OutputTokensPrice
    | extend RowCost = (InputCost + OutputCost) / 1000000
    | summarize
        TotalCost = sum(RowCost),
        TotalTokens = sum(TotalTokens),
        TotalCalls = count()
    """
    rows = run_kql(query, hours)
    if not rows:
        return {"total_cost": 0, "total_tokens": 0, "total_calls": 0}
    row = rows[0]
    return {
        "total_cost": row.get("TotalCost") or 0,
        "total_tokens": row.get("TotalTokens") or 0,
        "total_calls": row.get("TotalCalls") or 0,
    }


@app.get("/api/costs-by-subscription")
def costs_by_subscription(
    hours: float = Query(DEFAULT_TIMESPAN_HOURS, ge=0.0333, le=720),
    month_to_date: bool = Query(
        False,
        description=(
            "Si es true, ignora `hours` y calcula TotalCost del mes "
            "calendario actual (startofmonth..endofmonth), que es la "
            "ventana que corresponde de verdad a CostQuota (cuota "
            "mensual). Usar siempre true para comparar contra cuota; "
            "usar false (default) solo para gráficos de tendencia por "
            "rango de horas."
        ),
    ),
):
    """
    Gráfico de barras CostQuota vs TotalCost por suscripción, y fuente de
    verdad para saber si una suscripción está sobre cuota.

    Reutiliza EXACTAMENTE la lógica de la query de alerta 'ruleSuspendSub' / 'ruleActivateSub'
    definida en main.bicep, sin el filtro de threshold.

    OJO: `hours` solo tiene efecto si `month_to_date=False`. Cualquier
    llamador que necesite saber si hay que BLOQUEAR una suscripción
    (chequeo de cuota, panel "Estado de unidades de negocio", sugerencia
    de alternativa) debe pasar `month_to_date=True` — CostQuota es una
    cuota MENSUAL, no una cuota "de las últimas N horas".

    IMPORTANTE (fix 01/09/2026): la query arranca SIEMPRE desde
    SUBSCRIPTION_QUOTA_CL (todas las suscripciones configuradas) y hace
    `leftouter` join contra los costos calculados, nunca al revés. Antes
    era `costs | join kind=inner SUBSCRIPTION_QUOTA_CL`: al ser inner join
    sobre el resultado de un `summarize ... by SubscriptionName`, cualquier
    suscripción SIN llamadas dentro de la ventana consultada no generaba
    fila alguna en `costs`, así que el inner join la hacía desaparecer por
    completo del resultado — aunque tuviera cuota asignada y $0 gastado.
    Esto era especialmente visible con `month_to_date=True` a inicios de
    mes (la ventana "mes calendario" recién empezada tiene pocas o ninguna
    llamada para la mayoría de las suscripciones), que es justo lo que usa
    el panel "Estado de unidades de negocio": solo la(s) suscripción(es)
    que ya habían hecho una llamada en lo que iba del mes aparecían en la
    lista, en vez de mostrar las 4 unidades de negocio con su cuota. Con
    `leftouter` desde SUBSCRIPTION_QUOTA_CL, todas las suscripciones
    configuradas aparecen siempre, con TotalCost=0 cuando aún no hay uso.
    """
    base_query = llm_logs_month_to_date() if month_to_date else llm_logs_base(hours)
    query = base_query + f"""
    let costs = llmLogsWithSubscriptionId
        | join kind=inner (
            PRICING_CL
            | summarize arg_max(TimeGenerated, *) by Model
            | project Model, InputTokensPrice, OutputTokensPrice
            )
            on $left.DeploymentName == $right.Model
        | extend InputCost = PromptTokens * InputTokensPrice
        | extend OutputCost = CompletionTokens * OutputTokensPrice
        | summarize
            InputCost = sum(InputCost),
            OutputCost = sum(OutputCost)
            by SubscriptionName
        | extend TotalCost = (InputCost + OutputCost) / 1000000;
    SUBSCRIPTION_QUOTA_CL
    | summarize arg_max(TimeGenerated, *) by Subscription
    | project Subscription, CostQuota
    {_exclusion_where_clause("Subscription")}| join kind=leftouter costs on $left.Subscription == $right.SubscriptionName
    | extend TotalCost = coalesce(TotalCost, 0.0)
    | project SubscriptionName = Subscription, CostQuota, TotalCost
    | order by TotalCost desc
    """
    # run_kql ya usa timespan=timedelta(days=90) fijo del lado del SDK
    # (ver nota en run_kql) — el filtro real siempre vive en el texto de
    # la KQL, sea por `ago()` (llm_logs_base) o por `startofmonth`
    # (llm_logs_month_to_date). El valor de `hours` que se le pasa acá es
    # solo informativo para logs/debug cuando month_to_date=True.
    rows = run_kql(query, hours)
    return {
        "data": [
            {
                "subscription": r.get("SubscriptionName"),
                "cost_quota": r.get("CostQuota"),
                "total_cost": round(r.get("TotalCost") or 0, 3),
                "exceeded": (r.get("TotalCost") or 0) > (r.get("CostQuota") or 0),
            }
            for r in rows
        ]
    }


def get_quota_remaining() -> dict:
    """
    Devuelve, por suscripción, el margen restante real (CostQuota - TotalCost),
    el costo ya consumido (TotalCost), y si está excedida. Suscripciones sin
    llamadas registradas en el mes se consideran con margen igual a su
    CostQuota completa (costo 0), o con margen infinito si tampoco tienen
    CostQuota conocida todavía.

    SIEMPRE calcula sobre el mes calendario (month_to_date=True), nunca
    sobre un rango de horas — CostQuota es una cuota MENSUAL. Antes esta
    función tomaba `hours=DEFAULT_TIMESPAN_HOURS` (12h por default), lo
    que hacía que el chequeo de cuota dependiera de una ventana rodante
    de 12h en vez del gasto real acumulado del mes (bug detectado el
    21/08/2026 con Unidad de Negocio D). Ya no recibe `hours` como
    parámetro a propósito, para que no se pueda volver a colar por error.
    """
    result = costs_by_subscription(hours=DEFAULT_TIMESPAN_HOURS, month_to_date=True)["data"]
    remaining = {}
    for row in result:
        quota = row.get("cost_quota")
        cost = row.get("total_cost") or 0
        remaining[row["subscription"]] = {
            "remaining": (quota - cost) if quota is not None else float("inf"),
            "total_cost": cost,
            "exceeded": row["exceeded"],
        }
    # Cualquier suscripción configurada que no aparezca en las métricas
    # (sin tráfico todavía en el mes) se asume disponible y con margen
    # completo desconocido -> se prioriza como candidata segura.
    for sub in APIM_SUBSCRIPTIONS.keys():
        remaining.setdefault(sub, {"remaining": float("inf"), "total_cost": 0, "exceeded": False})
    return remaining


def get_quota_status() -> dict:
    """Compatibilidad: devuelve solo el booleano `exceeded` por suscripción (mes calendario)."""
    return {sub: info["exceeded"] for sub, info in get_quota_remaining().items()}


def suggest_alternative_subscription(exclude: list[str]) -> Optional[str]:
    """
    Sugiere la siguiente suscripción alternativa entre las que NO están
    excedidas (según el gasto MENSUAL real), excluyendo `exclude`.
    Criterio: la de MAYOR consumo ya acumulado (TotalCost), no la de mayor
    margen restante. Esto sigue el orden natural de agotamiento (la más
    cerca de gastarse también, después de la que ya se agotó) en vez de
    saltar siempre a la suscripción más "sana"/fresca — para que la
    cascada de la demo vaya cayendo de a una en vez de quedarse pegada
    siempre en la que tiene más margen.
    Ya no elige ni usa la alternativa automáticamente — /api/chat ya no hace
    failover: cuando una suscripción se queda sin cuota, se detiene y
    devuelve esta sugerencia para que el usuario decida si cambiar de
    suscripción y reenviar el mensaje él mismo.
    """
    quota = get_quota_remaining()
    candidates = [s for s in APIM_SUBSCRIPTIONS.keys() if s not in exclude]
    candidates = [s for s in candidates if not quota.get(s, {}).get("exceeded", False)]
    candidates.sort(key=lambda s: quota.get(s, {}).get("total_cost", 0), reverse=True)
    return candidates[0] if candidates else None


@app.get("/api/tokens-by-subscription")
def tokens_by_subscription(hours: float = Query(DEFAULT_TIMESPAN_HOURS, ge=0.0333, le=720)):
    """Dona: distribución de tokens totales consumidos por suscripción."""
    query = llm_logs_base(hours) + """
    llmLogsWithSubscriptionId
    | summarize TotalTokens = sum(TotalTokens) by SubscriptionName
    | order by TotalTokens desc
    """
    rows = run_kql(query, hours)
    total = sum(r.get("TotalTokens") or 0 for r in rows) or 1
    return {
        "data": [
            {
                "subscription": r.get("SubscriptionName"),
                "total_tokens": r.get("TotalTokens") or 0,
                "percentage": round(100 * (r.get("TotalTokens") or 0) / total, 2),
            }
            for r in rows
        ]
    }


@app.get("/api/tokens-timeseries")
def tokens_timeseries(
    hours: float = Query(DEFAULT_TIMESPAN_HOURS, ge=0.0333, le=720),
    bin_minutes: int = Query(15, ge=1, le=1440),
):
    """Línea: tokens consumidos en el tiempo, agrupados por producto + suscripción."""
    query = llm_logs_base(hours) + f"""
    llmLogsWithSubscriptionId
    | summarize TotalTokens = sum(TotalTokens) by
        bin(TimeGenerated, {bin_minutes}m), ProductName, SubscriptionName
    | order by TimeGenerated asc
    """
    rows = run_kql(query, hours)
    return {
        "data": [
            {
                "time": r.get("TimeGenerated").isoformat() if r.get("TimeGenerated") else None,
                "product": r.get("ProductName"),
                "subscription": r.get("SubscriptionName"),
                "total_tokens": r.get("TotalTokens") or 0,
            }
            for r in rows
        ]
    }


@app.get("/api/subscriptions/detail")
def subscriptions_detail(hours: float = Query(DEFAULT_TIMESPAN_HOURS, ge=0.0333, le=720)):
    """
    Detalle combinado por suscripción + producto: costo, cuota, tokens y
    llamadas. Alimenta las vistas 'Suscripciones', 'Productos & Cuotas' y
    'Alertas & Suspensiones' (agrupando/filtrando en el frontend).
    """
    query = llm_logs_base(hours) + """
    llmLogsWithSubscriptionId
    | join kind=inner (
        PRICING_CL
        | summarize arg_max(TimeGenerated, *) by Model
        | project Model, InputTokensPrice, OutputTokensPrice
        )
        on $left.DeploymentName == $right.Model
    | extend InputCost = PromptTokens * InputTokensPrice
    | extend OutputCost = CompletionTokens * OutputTokensPrice
    | summarize
        InputCost = sum(InputCost),
        OutputCost = sum(OutputCost),
        TotalTokens = sum(TotalTokens),
        Calls = count()
        by SubscriptionName, ProductName
    | extend TotalCost = (InputCost + OutputCost) / 1000000
    | join kind=leftouter (
        SUBSCRIPTION_QUOTA_CL
        | summarize arg_max(TimeGenerated, *) by Subscription
        | project Subscription, CostQuota
        )
        on $left.SubscriptionName == $right.Subscription
    | project SubscriptionName, ProductName, CostQuota, TotalCost, TotalTokens, Calls
    | order by TotalCost desc
    """
    rows = run_kql(query, hours)
    return {
        "data": [
            {
                "subscription": r.get("SubscriptionName"),
                "product": r.get("ProductName"),
                "cost_quota": r.get("CostQuota"),
                "total_cost": round(r.get("TotalCost") or 0, 3),
                "total_tokens": r.get("TotalTokens") or 0,
                "calls": r.get("Calls") or 0,
                "exceeded": (r.get("TotalCost") or 0) > (r.get("CostQuota") or 0),
            }
            for r in rows
        ]
    }


@app.get("/api/subscriptions/status")
def subscriptions_status(
    hours: float = Query(DEFAULT_TIMESPAN_HOURS, ge=0.0333, le=720),
    month_to_date: bool = Query(
        True,
        description=(
            "Default True: esta tarjeta/panel se muestra en el frontend "
            "como 'vs cuota mensual', así que por default calcula sobre "
            "el mes calendario, sin importar el selector de horas del "
            "resto del dashboard. Pasar False explícitamente solo si se "
            "necesita ver el estado 'excedida' recalculado sobre una "
            "ventana corta a propósito (debug), no para uso normal."
        ),
    ),
):
    """Estado de cada suscripción respecto a su cuota (para la tarjeta 'Salud del gateway'
    y el panel 'Estado de unidades de negocio')."""
    costs = costs_by_subscription(hours=hours, month_to_date=month_to_date)["data"]
    exceeded = [c for c in costs if c["exceeded"]]
    return {
        "total_subscriptions": len(costs),
        "exceeded_quota": len(exceeded),
        "healthy_percentage": round(100 * (len(costs) - len(exceeded)) / len(costs), 1) if costs else 100.0,
        "details": costs,
    }


# ---------------------------------------------------------------------------
# Panel "Probar Gateway" — la key de APIM se queda 100% en el servidor.
# El frontend solo ve nombres de suscripciones y modelos, nunca las keys.
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    subscription: str
    model: str
    message: str


@app.get("/api/chat/config")
def chat_config():
    """Qué puede elegir el frontend: nombres de suscripción y modelos disponibles. Sin keys."""
    return {
        "gateway_configured": bool(APIM_GATEWAY_URL) and bool(APIM_SUBSCRIPTIONS),
        "subscriptions": sorted(APIM_SUBSCRIPTIONS.keys()),
        "models": AVAILABLE_MODELS,
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    """
    Reenvía un mensaje al inference endpoint del APIM compartido usando la
    subscription key correspondiente, SIN exponerla nunca al navegador.
    Devuelve la respuesta + trazabilidad (tokens, latencia).
    """
    if not APIM_GATEWAY_URL:
        raise HTTPException(status_code=500, detail="APIM_GATEWAY_URL no está configurado en el App Service.")

    if req.subscription not in APIM_SUBSCRIPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Suscripción '{req.subscription}' no encontrada. Disponibles: {sorted(APIM_SUBSCRIPTIONS.keys())}",
        )

    if req.model not in AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Modelo '{req.model}' no disponible. Disponibles: {AVAILABLE_MODELS}",
        )

    # Chequeo proactivo: si la suscripción pedida ya se ve sobre cuota en
    # Log Analytics, NO se cambia de suscripción sola. Se detiene acá mismo
    # y se devuelve un aviso + sugerencia para que el usuario decida.
    # (El chequeo puede ir desactualizado por el delay de ingesta de logs —
    # por eso también hay una segunda detección más abajo, reactiva, por si
    # el Logic App ya suspendió la key de verdad en APIM.)
    quota_status = get_quota_status()
    if quota_status.get(req.subscription, False):
        suggestion = suggest_alternative_subscription(exclude=[req.subscription])
        return {
            "quota_exceeded": True,
            "requested_subscription": req.subscription,
            "suggested_subscription": suggestion,
            "message": (
                f"Se acabó el consumo disponible de {req.subscription}. "
                + (
                    f"Te sugerimos cambiar a {suggestion}."
                    if suggestion
                    else "No hay otra suscripción con cuota disponible ahora mismo."
                )
            ),
        }

    key = APIM_SUBSCRIPTIONS[req.subscription]
    client = AzureOpenAI(
        azure_endpoint=f"{APIM_GATEWAY_URL}/{INFERENCE_API_PATH}",
        api_key=key,
        api_version=INFERENCE_API_VERSION,
    )

    started_at = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=req.model,
            messages=[{"role": "user", "content": req.message}],
        )
    except Exception as e:
        error_text = str(e)
        is_invalid_subscription = "401" in error_text or "invalid subscription key" in error_text.lower()
        if is_invalid_subscription:
            # La key fue suspendida de verdad en APIM (vía Logic App) recién
            # ahora, y el chequeo proactivo iba desactualizado por el delay
            # de ingesta. Mismo criterio que arriba: no se cambia de
            # suscripción sola, se avisa y se sugiere.
            logger.info(
                "'%s' rechazada por APIM (401) — cuota agotada en tiempo real, sin failover automático.",
                req.subscription,
            )
            suggestion = suggest_alternative_subscription(exclude=[req.subscription])
            return {
                "quota_exceeded": True,
                "requested_subscription": req.subscription,
                "suggested_subscription": suggestion,
                "message": (
                    f"Se acabó el consumo disponible de {req.subscription}. "
                    + (
                        f"Te sugerimos cambiar a {suggestion}."
                        if suggestion
                        else "No hay otra suscripción con cuota disponible ahora mismo."
                    )
                ),
            }
        logger.error("Error llamando al gateway APIM: %s", e)
        raise HTTPException(status_code=502, detail=f"Error llamando al gateway: {e}")

    latency_ms = round((time.monotonic() - started_at) * 1000, 1)
    usage = response.usage
    return {
        "reply": response.choices[0].message.content,
        "subscription": req.subscription,
        "requested_subscription": req.subscription,
        "failed_over": False,
        "quota_exceeded": False,
        "model": req.model,
        "latency_ms": latency_ms,
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
    }


# ---------------------------------------------------------------------------
# Servir el frontend estático (mismo App Service, sin CORS que resolver)
# ---------------------------------------------------------------------------
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))