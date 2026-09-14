#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Despliega el dashboard de FinOps al App Service, conectado con Managed
# Identity al Log Analytics Workspace del lab finops-framework.
#
# Uso:
#   1. Rellena las 3 variables de abajo.
#   2. Párate en la carpeta finops-dashboard/ (donde están backend/ y frontend/).
#   3. Ejecuta: bash deploy.sh
# ---------------------------------------------------------------------------
set -euo pipefail

# =========== 1. RELLENA ESTO ===========
RESOURCE_GROUP_NAME="lab-finops-framework-V24" # confirmado contra quota_control.py (RESOURCE_GROUP) — verifica que siga siendo el RG activo si el lab se volvió a desplegar desde entonces
DEPLOYMENT_NAME="finops-framework"             # confirmado contra quota_control.py (DEPLOYMENT_NAME)
APP_NAME="dashboard-finops-$RANDOM"            # nombre único global del App Service; cámbialo si quieres algo fijo

# RG del APIM + Log Analytics Workspace + Application Insights COMPARTIDOS
# (mismo patrón/valor que sharedApimResourceGroupName en main.bicep). El
# workspace de este lab YA NO vive en $RESOURCE_GROUP_NAME desde la
# migración a recursos compartidos — vive acá.
SHARED_RESOURCE_GROUP_NAME="rg-shared-apim-gateway-V2"
# ========================================

LOCATION="swedencentral"
PLAN_NAME="plan-${APP_NAME}"

echo "▶️  Resource Group: $RESOURCE_GROUP_NAME"
echo "▶️  Deployment:      $DEPLOYMENT_NAME"
echo "▶️  App Service:     $APP_NAME"
echo

# --- 1. Obtener el Workspace ID (customerId) desde el deployment de Bicep ---
echo "🔎 Obteniendo Log Analytics Workspace ID..."
if ! az deployment group show --name "$DEPLOYMENT_NAME" -g "$RESOURCE_GROUP_NAME" &>/dev/null; then
  echo "⚠️  No existe un deployment llamado '$DEPLOYMENT_NAME' en '$RESOURCE_GROUP_NAME'."
  echo "    Deployments disponibles en ese resource group:"
  az deployment group list -g "$RESOURCE_GROUP_NAME" --query "[].name" -o tsv | sed 's/^/    - /'
  echo "    Edita DEPLOYMENT_NAME en este script con el nombre correcto y vuelve a correrlo."
  exit 1
fi

WORKSPACE_ID=$(az deployment group show \
  --name "$DEPLOYMENT_NAME" -g "$RESOURCE_GROUP_NAME" \
  --query "properties.outputs.logAnalyticsWorkspaceId.value" -o tsv)

if [ -z "$WORKSPACE_ID" ]; then
  echo "❌ No se pudo obtener el Workspace ID. Revisa RESOURCE_GROUP_NAME y DEPLOYMENT_NAME."
  exit 1
fi
echo "✅ Workspace ID: $WORKSPACE_ID"

# --- 1b. Obtener config del APIM compartido + subscription keys para el panel "Probar Gateway" ---
echo "🔎 Obteniendo APIM Gateway URL y subscription keys..."
APIM_GATEWAY_URL=$(az deployment group show \
  --name "$DEPLOYMENT_NAME" -g "$RESOURCE_GROUP_NAME" \
  --query "properties.outputs.apimResourceGatewayURL.value" -o tsv)

# apimSubscriptions viene como string con comillas simples (formato Python repr)
# en el output del deployment; lo normalizamos a JSON válido.
APIM_SUBSCRIPTIONS_RAW=$(az deployment group show \
  --name "$DEPLOYMENT_NAME" -g "$RESOURCE_GROUP_NAME" \
  --query "properties.outputs.apimSubscriptions.value" -o tsv)

APIM_SUBSCRIPTIONS_JSON=$(python3 -c "
import json, sys
raw = sys.argv[1]
subs = json.loads(raw.replace(\"'\", '\"')) if isinstance(raw, str) else raw
print(json.dumps({s['name']: s['key'] for s in subs}))
" "$APIM_SUBSCRIPTIONS_RAW")

if [ -z "$APIM_GATEWAY_URL" ] || [ "$APIM_SUBSCRIPTIONS_JSON" = "{}" ]; then
  echo "⚠️  No se pudo armar la config del gateway automáticamente."
  echo "    El panel 'Probar Gateway' quedará deshabilitado hasta que configures a mano:"
  echo "    APIM_GATEWAY_URL y APIM_SUBSCRIPTIONS_JSON (ver README.md)."
else
  echo "✅ APIM Gateway URL: $APIM_GATEWAY_URL"
  echo "✅ Subscriptions cargadas: $(echo "$APIM_SUBSCRIPTIONS_JSON" | python3 -c "import json,sys; print(list(json.load(sys.stdin).keys()))")"
fi

# --- 2. Crear el App Service Plan + Web App (Linux, Python 3.12) ---
echo "🏗️  Creando App Service Plan..."
az appservice plan create \
  --name "$PLAN_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --location "$LOCATION" \
  --sku B1 --is-linux \
  --output none

echo "🏗️  Creando Web App..."
az webapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP_NAME" \
  --plan "$PLAN_NAME" \
  --runtime "PYTHON:3.12" \
  --output none

# --- 3. Habilitar Managed Identity ---
echo "🔐 Habilitando Managed Identity..."
PRINCIPAL_ID=$(az webapp identity assign \
  --name "$APP_NAME" -g "$RESOURCE_GROUP_NAME" \
  --query principalId -o tsv)
echo "✅ Managed Identity: $PRINCIPAL_ID"

# --- 4. Asignar rol "Log Analytics Reader" sobre el workspace ---
# OJO: desde que el lab se migró a Log Analytics compartido, el workspace
# vive en $SHARED_RESOURCE_GROUP_NAME (rg-shared-apim-gateway-V2), NO en
# $RESOURCE_GROUP_NAME (el RG propio del lab). Buscarlo en
# $RESOURCE_GROUP_NAME devuelve vacío y el script fallaba acá.
echo "🔎 Buscando el resource ID del Log Analytics Workspace (compartido, en $SHARED_RESOURCE_GROUP_NAME)..."
WORKSPACE_RESOURCE_ID=$(az monitor log-analytics workspace list \
  --resource-group "$SHARED_RESOURCE_GROUP_NAME" \
  --query "[?customerId=='$WORKSPACE_ID'].id" -o tsv)

if [ -z "$WORKSPACE_RESOURCE_ID" ]; then
  echo "❌ No se encontró el workspace por customerId en $SHARED_RESOURCE_GROUP_NAME. Revisa manualmente con:"
  echo "   az monitor log-analytics workspace list -g $SHARED_RESOURCE_GROUP_NAME -o table"
  echo "   (si el workspace compartido se movió de resource group, actualiza SHARED_RESOURCE_GROUP_NAME arriba)"
  exit 1
fi

echo "🔐 Asignando rol 'Log Analytics Reader'..."
# Como el scope ahora es un recurso del RG compartido (no del RG propio del
# lab), esto requiere que quien corre el script tenga permiso para asignar
# roles ahí (Owner o User Access Administrator sobre $SHARED_RESOURCE_GROUP_NAME
# o sobre el workspace puntual) — antes de la migración alcanzaba con ser
# dueño del RG del lab.
az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Log Analytics Reader" \
  --scope "$WORKSPACE_RESOURCE_ID" \
  --output none
echo "✅ Rol asignado sobre: $WORKSPACE_RESOURCE_ID"

# --- 5. Configurar variables de entorno + startup command ---
echo "⚙️  Configurando app settings..."
# ApplicationInsightsAgent_EXTENSION_VERSION=disabled: si la suscripción
# tiene una política que auto-activa el "codeless agent" de Application
# Insights en App Services nuevos, ese agente monta /agents/python y lo
# pone ANTES del entorno virtual en PYTHONPATH — su typing_extensions.py
# viejo (sin `sentinel`) tapa al de requirements.txt y anyio/starlette/
# fastapi truenan al arrancar (ImportError: cannot import name 'sentinel'
# from 'typing_extensions'). Ver:
# https://github.com/microsoft/oryx/issues/2685 y
# https://azureossd.github.io/2025/10/14/Python-on-App-Service-Linux-Depedency-conflicts-when-using-the-app-insights-codeless-agent/
az webapp config appsettings set \
  --name "$APP_NAME" -g "$RESOURCE_GROUP_NAME" \
  --settings \
    LOG_ANALYTICS_WORKSPACE_ID="$WORKSPACE_ID" \
    DEFAULT_TIMESPAN_HOURS="12" \
    APIM_GATEWAY_URL="$APIM_GATEWAY_URL" \
    INFERENCE_API_PATH="finops-framework-inference" \
    INFERENCE_API_VERSION="2025-03-01-preview" \
    INFERENCE_API_ID="finops-framework-inference-api" \
    AVAILABLE_MODELS="gpt-5.4-mini,gpt-5.4,DeepSeek-V3.2" \
    APIM_SUBSCRIPTIONS_JSON="$APIM_SUBSCRIPTIONS_JSON" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
    ApplicationInsightsAgent_EXTENSION_VERSION="disabled" \
  --output none

az webapp config set \
  --name "$APP_NAME" -g "$RESOURCE_GROUP_NAME" \
  --startup-file "uvicorn backend.app:app --host 0.0.0.0 --port 8000" \
  --output none

# --- 6. Empaquetar y desplegar el código ---
echo "📦 Empaquetando código..."
rm -f deploy.zip
zip -r deploy.zip backend frontend requirements.txt -x "*__pycache__*" -x "*.pyc" > /dev/null

echo "🚀 Desplegando..."
az webapp deploy \
  --name "$APP_NAME" -g "$RESOURCE_GROUP_NAME" \
  --src-path deploy.zip --type zip \
  --output none

echo
echo "✅ Listo. Puede tardar 1-2 minutos en arrancar."
echo "🌐 URL: https://${APP_NAME}.azurewebsites.net"
echo
echo "Si ves un error 502, revisa los logs con:"
echo "  az webapp log tail --name $APP_NAME -g $RESOURCE_GROUP_NAME"