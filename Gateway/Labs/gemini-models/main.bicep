// ------------------
//    PARAMETERS
// ------------------

@description('One entry per Azure APIM subscription: { name, displayName }. No secrets in here — Bicep\'s @secure() decorator only supports string/object, not array, so the per-subscription Google API keys live in the separate geminiApiKeysBySubscription object param below instead.')
param apimSubscriptionsConfig array = []

param geminiInferenceAPIPath string = 'gemini-models-geminiapi' // Path to the native Gemini inference API in the shared APIM service
param openAICompatibleAPIPath string = 'gemini-models-openaicompatible' // Path to the OpenAI-compatible API in the shared APIM service
param geminiAPIURL string

@description('Fallback Google Gemini API key, used only for requests that do not match one of the subscriptions in apimSubscriptionsConfig.')
@secure()
param geminiAPIKey string

@description('Maps each apimSubscriptionsConfig[].name to its own Google Gemini API key, e.g. { subscription1: \'...\', subscription2: \'...\', subscription3: \'...\' } — so each subscription\'s free-tier daily quota (20 requests/day/project/model) stays independent instead of stacking. An object (not array) so @secure() can hide it.')
@secure()
param geminiApiKeysBySubscription object = {}

// Unique resource ids on the shared APIM. MUST stay unique across every lab
// attached to the shared instance, or a redeploy silently overwrites another
// lab's API/backend/product (this is what caused the earlier outage on the
// finops-framework lab — see that lab's main.bicep for the writeup).
param geminiInferenceAPIName string = 'gemini-models-inference-api'
param openAICompatibleAPIName string = 'gemini-models-openai-api'

@description('Name of the shared APIM instance this lab attaches to.')
param sharedApimName string = 'apim-shared-pdcibwky2f5ms'

@description('Resource group of the shared APIM instance.')
param sharedApimResourceGroupName string = 'rg-shared-apim-gateway-V2'

@description('Name of the existing azureMonitor logger on the shared APIM.')
param sharedApimLoggerName string = 'azuremonitor'

@description('Name of the existing, central Log Analytics workspace that the shared APIM already ships ALL gateway/LLM logs to (confirmed: it lives next to the shared APIM in the same resource group). We only read from it here — we never create a Log Analytics workspace, App Insights, or a diagnostic setting of our own for this lab.')
param sharedLogAnalyticsWorkspaceName string = 'workspace-pdcibwky2f5ms'

// ------------------
//    RESOURCES
// ------------------

// 1. Shared API Management instance (existing — NOT deployed by this lab)
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: sharedApimName
  scope: resourceGroup(sharedApimResourceGroupName)
}

// Existing azureMonitor logger on the shared APIM.
// ASSUMPTION: this logger already exists on the shared instance (created once
// when the shared APIM itself was provisioned). If it's named something else,
// override sharedApimLoggerName instead of trying to (re)create it here.
resource sharedApimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' existing = {
  parent: apim
  name: sharedApimLoggerName
}

// 2. The existing, central Log Analytics workspace next to the shared APIM.
// Read-only reference — used only to hand its customerId back to the
// notebook so the analytics cell can query it directly.
resource sharedLogAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: sharedLogAnalyticsWorkspaceName
  scope: resourceGroup(sharedApimResourceGroupName)
}

// 3. Everything that must be deployed INTO the shared APIM's resource group
// (the two gemini-models APIs, their backends/policies, a Product that scopes
// access to only these two APIs, and the subscriptions under that Product)
// lives in gemini-shared-resources.bicep — Bicep does not allow a deployable
// resource at file scope to target a different resource group directly (only
// "existing" reads and modules can cross scope).
module geminiSharedResourcesModule 'gemini-shared-resources.bicep' = {
  name: 'geminiSharedResourcesModule'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apiManagementName: sharedApimName
    apimLoggerId: sharedApimLogger.id
    geminiInferenceAPIPath: geminiInferenceAPIPath
    openAICompatibleAPIPath: openAICompatibleAPIPath
    geminiInferenceAPIName: geminiInferenceAPIName
    openAICompatibleAPIName: openAICompatibleAPIName
    geminiAPIURL: geminiAPIURL
    geminiAPIKey: geminiAPIKey
    apimSubscriptionsConfig: apimSubscriptionsConfig
    geminiApiKeysBySubscription: geminiApiKeysBySubscription
  }
}

// ------------------
//    OUTPUTS
// ------------------

// customerId (workspace GUID), not the ARM resource id — this is what
// `az monitor log-analytics query -w` expects, matching every other lab.
output logAnalyticsWorkspaceId string = sharedLogAnalyticsWorkspace.properties.customerId
output apimServiceId string = apim.id
output apimResourceGatewayURL string = apim.properties.gatewayUrl

#disable-next-line outputs-should-not-contain-secrets
output apimSubscriptions array = geminiSharedResourcesModule.outputs.apimSubscriptions
