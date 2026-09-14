// ------------------
//    PARAMETERS
// ------------------

@description('Configuration array for AI Services')
param aiServicesConfig array = []

@description('Configuration array for model deployments')
param modelsConfig array = []

@description('Name of the shared API Management instance (created by labs/shared-apim-gateway)')
param sharedApimName string

@description('Resource Group name where the shared API Management instance lives')
param sharedApimResourceGroupName string

@description('Name of the subscription in the shared APIM to reuse for the Model Gateway connection')
param sharedApimSubscriptionName string = 'shared-subscription'

@description('Path for the inference API')
param inferenceAPIPath string = 'inference'

@description('Type of inference API')
param inferenceAPIType string = 'AzureAI'

@description('Name of the AI Foundry project')
param foundryProjectName string = 'default'

// ------------------
//    VARIABLES
// ------------------

var resourceSuffix = uniqueString(subscription().id, resourceGroup().id)

// Nombre/identificadores únicos para la API de este lab dentro del APIM COMPARTIDO.
// Sin esto, todos los labs que reutilicen el APIM compartido crearían/pisarían
// la misma API ('inference-api'), el mismo path ('inference') y el mismo
// backend pool ('inference-backend-pool'), sobrescribiéndose entre sí.
var uniqueInferenceAPIPath = '${inferenceAPIPath}-${resourceSuffix}'
var uniqueInferenceAPIName = 'inference-api-${resourceSuffix}'
var uniqueInferenceBackendPoolName = 'inference-backend-pool-${resourceSuffix}'

// Model Gateway models array (lista TODOS los modelos de modelsConfig, sin filtrar)
var modelGatewayModels = [for model in modelsConfig: {
  name: model.name
  properties: {
    model: {
      name: model.name
      version: model.version
      format: model.publisher
    }
  }
}]

// ------------------
//    RESOURCES
// ------------------

// 1. Log Analytics Workspace
module lawModule '../../modules/operational-insights/v1/workspaces.bicep' = {
  name: 'lawModule'
}

// 2. Application Insights
module appInsightsModule '../../modules/monitor/v1/appinsights.bicep' = {
  name: 'appInsightsModule'
  params: {
    lawId: lawModule.outputs.id
    customMetricsOptedInType: 'WithDimensions'
  }
}

// 3. API Management COMPARTIDO — ya NO se crea aquí.
//    Se referencia el que provisiona labs/shared-apim-gateway (existing, cross-resource-group).
resource sharedApim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: sharedApimName
  scope: resourceGroup(sharedApimResourceGroupName)
}

resource sharedApimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' existing = {
  parent: sharedApim
  name: 'azuremonitor'
}

resource sharedApimSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' existing = {
  parent: sharedApim
  name: sharedApimSubscriptionName
}

// 4. AI Foundry
module foundryModule '../../modules/cognitive-services/v3/foundry.bicep' = {
  name: 'foundryModule'
  params: {
    aiServicesConfig: aiServicesConfig
    modelsConfig: modelsConfig
    apimPrincipalId: sharedApim.identity.principalId
    foundryProjectName: foundryProjectName
    appInsightsId: appInsightsModule.outputs.id
    appInsightsInstrumentationKey: appInsightsModule.outputs.instrumentationKey
  }
}

// 5. APIM Inference API — se registra DENTRO del APIM compartido (existing)
module inferenceAPIModule '../../modules/apim/v3/inference-api.bicep' = {
  name: 'inferenceAPIModule-${resourceSuffix}'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apiManagementName: sharedApim.name
    policyXml: loadTextContent('policy.xml')
    apimLoggerId: sharedApimLogger.id
    appInsightsId: appInsightsModule.outputs.id
    appInsightsInstrumentationKey: appInsightsModule.outputs.instrumentationKey
    aiServicesConfig: foundryModule.outputs.extendedAIServicesConfig
    inferenceAPIType: inferenceAPIType
    inferenceAPIName: uniqueInferenceAPIName
    inferenceAPIDisplayName: 'Foundry AI Gateway - Inference API (${resourceSuffix})'
    inferenceAPIDescription: 'Inference API for the foundry-ai-gateway lab (RG: ${resourceGroup().name})'
    inferenceAPIPath: uniqueInferenceAPIPath
    inferenceBackendPoolName: uniqueInferenceBackendPoolName
  }
}

// 6. Reference the existing Cognitive Services account created by foundry module
resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: '${aiServicesConfig[1].name}-${resourceSuffix}'   // ← usa el índice [1]
  scope: resourceGroup()
}

// 7. Model Gateway Connection - Connect APIM as a model gateway to Foundry
resource modelGatewayConnection 'Microsoft.CognitiveServices/accounts/connections@2025-04-01-preview' = {
  parent: aiFoundry
  name: 'ai-gateway'
  properties: {
    authType: 'ApiKey'
    category: 'ApiManagement'
    target: '${sharedApim.properties.gatewayUrl}/${uniqueInferenceAPIPath}/openai'
    isSharedToAll: true
    credentials: {
      #disable-next-line BCP037
      key: sharedApimSubscription.listSecrets().primaryKey
    }
    metadata: {
      ApiType: 'Azure'
      inferenceAPIVersion: '2024-12-01-preview'
      //deploymentAPIVersion: '2024-12-01-preview'
      Location: aiServicesConfig[0].location
      deploymentInPath: 'true'
      models: string(modelGatewayModels)   // ← lista TODOS los modelos de modelsConfig, sin filtrar
    }
  }
  dependsOn: [
    inferenceAPIModule
  ]
}

// ------------------
//    OUTPUTS
// ------------------

output logAnalyticsWorkspaceId string = lawModule.outputs.customerId
output apimServiceId string = sharedApim.id
output apimResourceGatewayURL string = sharedApim.properties.gatewayUrl
output AgentsfoundryProjectEndpoint string = foundryModule.outputs.extendedAIServicesConfig[1].foundryProjectEndpoint
output AgentsfoundryAIServicesEndpoint string = foundryModule.outputs.extendedAIServicesConfig[1].endpoint
output aiGatewayUrl string = '${sharedApim.properties.gatewayUrl}/${uniqueInferenceAPIPath}'
output aiGatewayApiName string = uniqueInferenceAPIName
output aiGatewayConnectionName string = modelGatewayConnection.name
