@description('Nombre del API Management existente del laboratorio.')
param apimName string

@description('Resource group donde vive la APIM, si es distinto al de este deployment.')
param apimResourceGroup string = resourceGroup().name

@description('Identificador de la suscripción de APIM existente, no el ID de suscripción de Azure.')
param apimSubscriptionName string = 'subscription1'

@description('Modelos desplegados que la app debe ofrecer en el selector (p.ej. ["gpt-5.6-luna","Phi-4"]). El primero queda como modelo por defecto.')
param modelNames array
@minValue(0)
@maxValue(7)
param safetyThreshold int = 4
@description('Nombre del recurso Azure AI Content Safety existente del laboratorio.')
param contentSafetyName string
param inferencePath string = 'inference'
param inferenceApiVersion string = '2024-05-01-preview'
param location string = resourceGroup().location
param webAppName string = 'content-safety-${uniqueString(resourceGroup().id)}'
param planName string = 'plan-${webAppName}'

@allowed(['B1', 'B2', 'B3'])
param planSku string = 'B1'

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
  scope: resourceGroup(apimResourceGroup)
}

resource apimSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' existing = {
  parent: apim
  name: apimSubscriptionName
}

resource contentSafety 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' existing = {
  name: contentSafetyName
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: planSku
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    reserved: true
  }
  tags: { workload: 'content-safety', component: 'web-chat' }
}

resource webApp 'Microsoft.Web/sites@2024-04-01' = {
  name: webAppName
  location: location
  kind: 'app,linux'
  tags: { workload: 'content-safety', component: 'web-chat' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python app.py'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      healthCheckPath: '/health'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
        { name: 'SAFETY_ENDPOINT', value: '${apim.properties.gatewayUrl}/${inferencePath}/models' }
        { name: 'SAFETY_API_KEY', value: apimSubscription.listSecrets().primaryKey }
        { name: 'SAFETY_MODELS', value: join(modelNames, ',') }
        { name: 'SAFETY_THRESHOLD', value: string(safetyThreshold) }
        { name: 'SAFETY_API_VERSION', value: inferenceApiVersion }
        { name: 'SAFETY_CONTENT_ENDPOINT', value: contentSafety.properties.endpoint }
        { name: 'SAFETY_CONTENT_KEY', value: contentSafety.listKeys().key1 }
      ]
    }
  }
}

output webAppName string = webApp.name
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
output appServicePlanName string = plan.name
