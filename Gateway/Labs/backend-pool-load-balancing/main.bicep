// ------------------
//    PARAMETERS
// ------------------

@description('Name of the shared APIM instance. Fixed value, but kept as a param (not hardcoded inline) so it stays visible/overridable at deploy time.')
param sharedApimName string = 'apim-shared-pdcibwky2f5ms'

@description('Resource group of the shared APIM instance. Fixed value.')
param sharedApimResourceGroupName string = 'rg-shared-apim-gateway-V2'

param aiServicesConfig array = []
param modelsConfig array = []
param inferenceAPIType string = 'AzureOpenAI'

@description('Path to the backend-pool inference API in the shared APIM. Must NOT collide with another lab (Lab 1 already uses "inference").')
param inferenceAPIPath string = 'backend-pool-inference'

@description('Name of the API resource in APIM. Must NOT collide with another lab (Lab 1 already uses "inference-api").')
param inferenceAPIName string = 'backend-pool-inference-api'

@description('Display name of the API in APIM. APIM enforces this to be UNIQUE across the whole instance (not just path/name) — Lab 1 already uses the default "Inference API", so this must differ.')
param inferenceAPIDisplayName string = 'Backend Pool Inference API'

@description('Name of the APIM backend pool resource. Must NOT collide with another lab.')
param inferenceBackendPoolName string = 'backend-pool-lb-pool'

param foundryProjectName string = 'default'

// ------------------
//    RESOURCES
// ------------------

// 0. Reference to the shared APIM instance. Never creates or redeploys it — read-only lookup.
resource sharedApim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: sharedApimName
  scope: resourceGroup(sharedApimResourceGroupName)
}

// 1. AI Foundry — this lab needs its OWN Foundry accounts (foundry1..4 across regions,
//    with priority/weight) because the test scenario requires controlled TPM exhaustion
//    and failover across backends this lab fully owns. These are deployed in THIS lab's
//    own resource group (default scope, no override) — only the API *registration* in
//    step 2 needs to target the shared APIM's resource group.
module foundryModule '../../modules/cognitive-services/v3/foundry.bicep' = {
  name: 'foundryModule'
  params: {
    aiServicesConfig: aiServicesConfig
    modelsConfig: modelsConfig
    apimPrincipalId: sharedApim.identity.principalId // grant the SHARED APIM's managed identity access, not a lab-owned one
    foundryProjectName: foundryProjectName
  }
}

// 2. APIM Inference API (backend pool with priority/weight) registered on the SHARED APIM.
//    v3/inference-api.bicep already builds a Pool-type backend automatically when
//    aiServicesConfig has more than one entry, so no new module was needed for this lab.
module inferenceAPIModule '../../modules/apim/v3/inference-api.bicep' = {
  name: 'inferenceAPIModule'
  scope: resourceGroup(sharedApimResourceGroupName) // <- required: this module writes into the shared APIM's RG
  params: {
    apiManagementName: sharedApimName // <- required explicit, never rely on the module's implicit default
    policyXml: loadTextContent('policy.xml')
    aiServicesConfig: foundryModule.outputs.extendedAIServicesConfig
    inferenceAPIType: inferenceAPIType
    inferenceAPIPath: inferenceAPIPath
    inferenceAPIName: inferenceAPIName
    inferenceAPIDisplayName: inferenceAPIDisplayName
    inferenceBackendPoolName: inferenceBackendPoolName
    configureCircuitBreaker: true
  }
}

// ------------------
//    OUTPUTS
// ------------------

output apimServiceId string = sharedApim.id
output apimResourceGatewayURL string = sharedApim.properties.gatewayUrl
