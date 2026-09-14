/**
 * @module access-inference-api
 * @description Registers an access-controlled Inference API (/access-inference) in APIM for Lab 3
 * (Access Controlling). Unlike inference-api.bicep, this module does NOT create or modify any
 * backend resource — it only references, by name, a backend that already exists in the shared
 * APIM (e.g. the one registered by Lab 1 for gpt-5.4-mini). This avoids re-declaring the AI
 * services backend and risking drift/overwrite of what another lab already deployed.
 * This is version 3 (v3) of the APIM Bicep module family.
 */

// ------------------
//    PARAMETERS
// ------------------

@description('The name of the API Management instance. No default on purpose: must always be passed explicitly to avoid accidentally targeting a "apim-<uniqueString>" instance that does not exist.')
param apiManagementName string

@description('Id of the APIM Logger')
param apimLoggerId string = ''

@description('The instrumentation key for Application Insights')
@secure()
param appInsightsInstrumentationKey string = ''

@description('The resource ID for Application Insights')
param appInsightsId string = ''

@description('The XML content for the API policy. Must contain a {backend-id} placeholder.')
param policyXml string

@description('The name of an EXISTING APIM backend to route requests to (e.g. the one registered by Lab 1 for gpt-5.4-mini). This module does not create, update, or own this resource — it only references it.')
param backendId string

@description('The name of the Access Inference API in API Management.')
param accessInferenceAPIName string = 'access-inference-api'

@description('The description of the Access Inference API in API Management.')
param accessInferenceAPIDescription string = 'Access-controlled Inferencing API (OAuth2/JWT via Microsoft Entra ID)'

@description('The display name of the Access Inference API in API Management.')
param accessInferenceAPIDisplayName string = 'Access Inference API'

@description('The path to the access inference API in the APIM service. Must be different from any path already registered by another lab (e.g. Lab 1 uses "inference").')
param accessInferenceAPIPath string = 'access-inference'

@description('The inference API type — must match the shape of the underlying backend so requests/specs line up correctly.')
@allowed([
  'AzureOpenAIV1' // Azure OpenAI v1 (/openai/v1)
  'AzureOpenAI'   // Azure OpenAI (/openai) - needs the api-version query param
  'AzureAI'       // Azure AI (/models)
  'OpenAI'        // OpenAI (chat completions)
  'PassThrough'   // passthrough API (using wildcard path)
])
param inferenceAPIType string = 'AzureOpenAI'

// ------------------
//    VARIABLES
// ------------------

var logSettings = {
  headers: [ 'Content-type', 'User-agent', 'x-ms-region', 'x-ratelimit-remaining-tokens', 'x-ratelimit-remaining-requests' ]
  body: { bytes: 8192 }
}

var updatedPolicyXml = replace(policyXml, '{backend-id}', backendId)

var endpointPath = (inferenceAPIType == 'AzureOpenAIV1') ? 'openai/v1' : (inferenceAPIType == 'AzureOpenAI') ? 'openai' : (inferenceAPIType == 'AzureAI') ? 'models' : ''

// ------------------
//    RESOURCES
// ------------------

resource apimService 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apiManagementName
}

// Reference-only: validates the backend already exists and fails fast with a clear
// ResourceNotFound error at deploy time if 'backendId' is wrong, instead of a confusing
// 404 from the policy engine at request time. This resource is never created or written to.
resource existingBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' existing = {
  name: backendId
  parent: apimService
}

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis
resource api 'Microsoft.ApiManagement/service/apis@2025-03-01-preview' = {
  name: accessInferenceAPIName
  parent: apimService
  properties: {
    apiType: 'http'
    description: accessInferenceAPIDescription
    displayName: accessInferenceAPIDisplayName
    format: 'openapi+json'
    path: '${accessInferenceAPIPath}/${endpointPath}'
    protocols: [
      'https'
    ]
    subscriptionKeyParameterNames: {
      header: 'api-key'
      query: 'api-key'
      #disable-next-line BCP037
      bearer: 'enabled'
    }
    subscriptionRequired: true
    type: 'http'
    value: string((inferenceAPIType == 'AzureOpenAIV1') ? loadJsonContent('./specs/AIFoundryOpenAIV1.json') : (inferenceAPIType == 'AzureOpenAI') ? loadJsonContent('./specs/AIFoundryOpenAI.json') : (inferenceAPIType == 'AzureAI') ? loadJsonContent('./specs/AIFoundryAzureAI.json') : (inferenceAPIType == 'OpenAI') ? loadJsonContent('./specs/LLMOpenAI.json') : loadJsonContent('./specs/PassThrough.json'))
  }
}

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis/policies
resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  name: 'policy'
  parent: api
  properties: {
    format: 'rawxml'
    value: updatedPolicyXml
  }
  dependsOn: [
    existingBackend
  ]
}

resource apiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = if(length(apimLoggerId) > 0) {
  parent: api
  name: 'azuremonitor'
  properties: {
    alwaysLog: 'allErrors'
    verbosity: 'verbose'
    logClientIp: true
    loggerId: apimLoggerId
    sampling: {
      samplingType: 'fixed'
      percentage: json('100')
    }
    frontend: {
      request: {
        headers: []
        body: { bytes: 0 }
      }
      response: {
        headers: []
        body: { bytes: 0 }
      }
    }
    backend: {
      request: {
        headers: []
        body: { bytes: 0 }
      }
      response: {
        headers: []
        body: { bytes: 0 }
      }
    }
    largeLanguageModel: {
      logs: 'enabled'
      requests: {
        messages: 'all'
        maxSizeInBytes: 262144
      }
      responses: {
        messages: 'all'
        maxSizeInBytes: 262144
      }
    }
  }
}

resource apiDiagnosticsAppInsights 'Microsoft.ApiManagement/service/apis/diagnostics@2022-08-01' = if (!empty(appInsightsId) && !empty(appInsightsInstrumentationKey)) {
  name: 'applicationinsights'
  parent: api
  properties: {
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    logClientIp: true
    loggerId: resourceId(resourceGroup().name, 'Microsoft.ApiManagement/service/loggers', apiManagementName, 'appinsights-logger')
    metrics: true
    verbosity: 'verbose'
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    frontend: {
      request: logSettings
      response: logSettings
    }
    backend: {
      request: logSettings
      response: logSettings
    }
  }
}

// ------------------
//    OUTPUTS
// ------------------

output apiId string = api.id
output accessInferenceAPIPath string = accessInferenceAPIPath
