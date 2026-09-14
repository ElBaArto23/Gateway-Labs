# FinOps Framework · Dashboard Web

Frontend + backend para visualizar en un App Service los mismos datos que
hoy ves en el Workbook nativo de Azure (`Cost Analysis`) del lab
`finops-framework`: costo vs cuota por suscripción, distribución de tokens,
consumo en el tiempo y estado de cada suscripción.

No inventa datos nuevos: reutiliza la misma query KQL que ya usan las
reglas de alerta `ruleSuspendSub` / `ruleActivateSub` en `main.bicep`.

## Estructura

```
finops-dashboard/
├── backend/
│   └── app.py            # FastAPI: consulta Log Analytics y expone /api/*
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js             # Chart.js, consume /api/* del mismo origen
└── requirements.txt        # a nivel raíz (no dentro de backend/) — Oryx lo busca
                             # ahí durante el build; deploy.sh lo incluye en el zip
                             # explícitamente por eso.
```

## 1. Obtener el Workspace ID

Es el mismo valor que ya expone el notebook como salida del deployment:

```bash
az deployment group show \
  --name <deployment_name> -g <resource_group_name> \
  --query "properties.outputs.logAnalyticsWorkspaceId.value" -o tsv
```

Guárdalo, lo necesitas en el paso 3.

## 2. Crear el App Service (Linux, Python)

```bash
az appservice plan create \
  --name plan-finops-dashboard \
  --resource-group <resource_group_name> \
  --sku B1 --is-linux

az webapp create \
  --name <nombre-unico-app> \
  --resource-group <resource_group_name> \
  --plan plan-finops-dashboard \
  --runtime "PYTHON:3.12"

# Habilita la Managed Identity - así el backend NO necesita ninguna API key
az webapp identity assign \
  --name <nombre-unico-app> \
  --resource-group <resource_group_name>
```

## 3. Configurar variables de entorno

```bash
az webapp config appsettings set \
  --name <nombre-unico-app> \
  --resource-group <resource_group_name> \
  --settings \
    LOG_ANALYTICS_WORKSPACE_ID="<workspace-id-del-paso-1>" \
    DEFAULT_TIMESPAN_HOURS="12" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
    STARTUP_COMMAND="uvicorn backend.app:app --host 0.0.0.0 --port 8000"

az webapp config set \
  --name <nombre-unico-app> \
  --resource-group <resource_group_name> \
  --startup-file "uvicorn backend.app:app --host 0.0.0.0 --port 8000"
```

## 4. Dar permiso de lectura al Log Analytics Workspace

Este es el paso que más se olvida — sin esto el backend responde 502.

> ⚠️ Desde que `finops-framework` se migró a usar el Log Analytics
> Workspace **compartido**, el workspace ya NO vive en el resource group
> del lab (`<resource_group_name>`) — vive en el resource group del APIM
> compartido (`rg-shared-apim-gateway-V2`, workspace
> `workspace-pdcibwky2f5ms`). Buscarlo con `--resource-group
> <resource_group_name>` (como hacía esta guía antes) devuelve vacío.
> Además, para asignar el rol ahí necesitas permisos de IAM sobre ese
> resource group compartido (Owner o User Access Administrator), no solo
> sobre el RG propio del lab.

```bash
PRINCIPAL_ID=$(az webapp identity show \
  --name <nombre-unico-app> -g <resource_group_name> \
  --query principalId -o tsv)

WORKSPACE_RESOURCE_ID=$(az monitor log-analytics workspace show \
  --resource-group rg-shared-apim-gateway-V2 \
  --workspace-name workspace-pdcibwky2f5ms \
  --query id -o tsv)

az role assignment create \
  --assignee $PRINCIPAL_ID \
  --role "Log Analytics Reader" \
  --scope $WORKSPACE_RESOURCE_ID
```

## 5. Desplegar el código

Desde la carpeta `finops-dashboard/`:

```bash
zip -r deploy.zip backend frontend
az webapp deploy \
  --name <nombre-unico-app> \
  --resource-group <resource_group_name> \
  --src-path deploy.zip --type zip
```

Abre `https://<nombre-unico-app>.azurewebsites.net` — debería verse el
dashboard con las 4 tarjetas KPI y los 4 gráficos.

## Probar localmente antes de desplegar

```bash
# desde la carpeta finops-dashboard/ (requirements.txt vive acá, no en backend/)
pip install -r requirements.txt
az login   # así DefaultAzureCredential puede autenticar con tu usuario
export LOG_ANALYTICS_WORKSPACE_ID="<workspace-id>"
uvicorn backend.app:app --reload --port 8000
```

Abre `http://localhost:8000`.

## Notas

- Los endpoints (`/api/summary`, `/api/costs-by-subscription`,
  `/api/tokens-by-subscription`, `/api/tokens-timeseries`,
  `/api/subscriptions/status`, `/api/subscriptions/detail`) aceptan
  `?hours=N` — el selector de rango del frontend ya lo usa.
- El backend agrupa "Producto" usando el campo nativo `ProductId` de
  `ApiManagementGatewayLogs` (platinum/gold/silver), igual que en el
  gráfico de líneas del Workbook original.
- Si tu tenant restringe *Public network access* en el Log Analytics
  Workspace, deberás integrar el App Service a una VNet con Private
  Endpoint hacia el workspace.

## Panel "Probar Gateway" (chat contra el APIM compartido)

Permite mandar mensajes de prueba al inference endpoint del APIM
compartido desde el navegador, **sin exponer nunca las subscription
keys** — se quedan como variable de entorno del lado del servidor y el
frontend solo ve nombres de suscripción y modelos.

`deploy.sh` ya arma esta configuración automáticamente leyendo los
outputs del deployment de Bicep. Si necesitas configurarla a mano:

```bash
az webapp config appsettings set \
  --name <nombre-unico-app> -g <resource_group_name> \
  --settings \
    APIM_GATEWAY_URL="<apimResourceGatewayURL del output del deployment>" \
    INFERENCE_API_PATH="finops-framework-inference" \
    INFERENCE_API_VERSION="2025-03-01-preview" \
    AVAILABLE_MODELS="gpt-5.4-mini,gpt-5.4,DeepSeek-V3.2" \
    APIM_SUBSCRIPTIONS_JSON='{"subscription1":"<key1>","subscription2":"<key2>"}'
```

Endpoints:
- `GET /api/chat/config` — nombres de suscripción y modelos disponibles (sin keys)
- `POST /api/chat` — `{subscription, model, message}` → reenvía al gateway y devuelve `reply`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens`