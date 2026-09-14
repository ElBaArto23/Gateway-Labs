/**
 * @module shared-apim-gateway
 * @description Provisiona el APIM COMPARTIDO usado por los 3 labs (Model Gateway,
 * Foundry IQ, Access Controlling). No crea ninguna API ni recurso de Foundry:
 * solo la instancia de API Management, Log Analytics y Application Insights.
 *
 * Los labs referencian este APIM como `existing` (ver informe de arquitectura).
 * Este componente se despliega UNA sola vez, en su propio Resource Group,
 * independiente del ciclo de vida de cualquier lab.
 */

// ------------------
//    PARAMETERS
// ------------------

@description('SKU for the shared API Management instance. "Developer" se usa para pruebas/labs (sin SLA, capacidad fija en 1, el más económico). Para producción evalúa "Basicv2", "Standardv2" o "Premium" según tus necesidades de SLA, autoscaling, VNET y multi-región — ver README.md.')
@allowed([
  'Consumption'
  'Developer'
  'Basic'
  'Basicv2'
  'Standard'
  'Standardv2'
  'Premium'
])
param apimSku string = 'Developer'

@description('Configuration array for APIM subscriptions. Por defecto crea una suscripción con scope /apis, válida para todas las APIs que los labs registren después (inference, ai-search, access-inference).')
param apimSubscriptionsConfig array = [
  {
    name: 'shared-subscription'
    displayName: 'Shared AI Gateway Subscription'
  }
]

@description('Optional explicit name for the API Management instance. Defaults to "apim-shared-<uniqueString>".')
param apiManagementName string = 'apim-shared-${uniqueString(subscription().id, resourceGroup().id)}'

// ------------------
//    RESOURCES
// ------------------

// 1. Log Analytics Workspace (centralizado para los 3 labs)
module lawModule '../../modules/operational-insights/v1/workspaces.bicep' = {
  name: 'lawModule'
}

// 2. Application Insights (centralizado para los 3 labs)
module appInsightsModule '../../modules/monitor/v1/appinsights.bicep' = {
  name: 'appInsightsModule'
  params: {
    lawId: lawModule.outputs.id
    customMetricsOptedInType: 'WithDimensions'
  }
}

// 3. API Management ÚNICO — este es el único módulo del repo que crea el servicio APIM.
//    Ningún lab debe volver a llamar a este módulo.
module apimModule '../../modules/apim/v3/apim.bicep' = {
  name: 'apimModule'
  params: {
    apiManagementName: apiManagementName
    apimSku: apimSku
    apimSubscriptionsConfig: apimSubscriptionsConfig
    lawId: lawModule.outputs.id
    appInsightsId: appInsightsModule.outputs.id
    appInsightsInstrumentationKey: appInsightsModule.outputs.instrumentationKey
  }
}

// ------------------
//    OUTPUTS
// ------------------

// Estos son los valores que cada lab (1, 2 y 3) necesitará como parámetros de entrada
// para referenciar este APIM como `existing`.

output resourceGroupName string = resourceGroup().name
output logAnalyticsWorkspaceId string = lawModule.outputs.customerId

output apimServiceId string = apimModule.outputs.id
output apimServiceName string = apimModule.outputs.name
output apimResourceGatewayURL string = apimModule.outputs.gatewayUrl
output apimPrincipalId string = apimModule.outputs.principalId
output apimLoggerId string = apimModule.outputs.loggerId

#disable-next-line outputs-should-not-contain-secrets
output apimSubscriptions array = apimModule.outputs.apimSubscriptions
