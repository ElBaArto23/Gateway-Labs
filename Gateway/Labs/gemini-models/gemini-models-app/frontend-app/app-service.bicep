// ------------------
//    PARAMETERS
// ------------------

@description('Location for the App Service Plan and Web App. Independent from the shared APIM lab — this app only needs to reach the gateway over HTTPS, not sit in the same region.')
param location string = resourceGroup().location

@description('SKU for the Linux App Service Plan. F1 is free but has a daily CPU-minute quota and no Always On (the app can cold-start after idling) — fine for occasional demos. Bump to B1 if you need it always warm.')
param appServicePlanSku string = 'F1'

@description('Node.js runtime version for the Web App.')
param nodeVersion string = '20-lts'

@description('APIM shared gateway base URL.')
param gatewayUrl string = 'https://apim-shared-pdcibwky2f5ms.azure-api.net'

@description('Path of the gemini-models OpenAI-compatible API on the shared APIM.')
param openAiApiPath string = 'gemini-models-openaicompatible'

param apiVersion string = 'v1beta'
param modelName string = 'gemini-3-flash-preview'
param systemPrompt string = 'Eres un asistente útil y profesional.'

@description('Azure APIM subscription key for Subscription 1 — the key printed by geminimodels.ipynb (NOT the Google Gemini key). Leave empty to disable this subscription in the app.')
@secure()
param subscription1Key string = ''

@description('Azure APIM subscription key for Subscription 2.')
@secure()
param subscription2Key string = ''

@description('Azure APIM subscription key for Subscription 3.')
@secure()
param subscription3Key string = ''

// ------------------
//    VARIABLES
// ------------------

// Web App names are globally unique DNS names across all of Azure, so this
// needs a suffix — same pattern as the rest of the lab.
var resourceSuffix = uniqueString(subscription().id, resourceGroup().id)
var appServicePlanName = 'plan-gemini-models-${resourceSuffix}'
var webAppName = 'gemini-models-app-${resourceSuffix}'

// ------------------
//    RESOURCES
// ------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  kind: 'linux'
  sku: {
    name: appServicePlanSku
  }
  properties: {
    reserved: true // required for Linux plans
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|${nodeVersion}'
      appCommandLine: 'npm start'
      // Always On isn't available on the Free (F1) tier — only turn it on
      // for a paid SKU, otherwise the deployment fails.
      alwaysOn: appServicePlanSku != 'F1'
      appSettings: [
        { name: 'GATEWAY_URL', value: gatewayUrl }
        { name: 'OPENAI_API_PATH', value: openAiApiPath }
        { name: 'API_VERSION', value: apiVersion }
        { name: 'MODEL_NAME', value: modelName }
        { name: 'SYSTEM_PROMPT', value: systemPrompt }
        { name: 'SUBSCRIPTION_1_KEY', value: subscription1Key }
        { name: 'SUBSCRIPTION_2_KEY', value: subscription2Key }
        { name: 'SUBSCRIPTION_3_KEY', value: subscription3Key }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~20' }
      ]
    }
  }
}

// ------------------
//    OUTPUTS
// ------------------

output webAppName string = webApp.name
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
