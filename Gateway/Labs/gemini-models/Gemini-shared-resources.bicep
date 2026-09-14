/**
 * @module
 * @description Everything the gemini-models lab needs to deploy INTO the
 * shared APIM's resource group: its own two APIs (native Gemini + OpenAI
 * compatible), their backends and policies, a Product that scopes access to
 * ONLY these two APIs, and the lab's subscriptions under that Product.
 * Deployed via a module with an explicit `scope` pointing at the shared
 * APIM's resource group — this file is never deployed on its own.
 *
 * No Log Analytics workspace, App Insights, or diagnostic setting is created
 * here: the shared APIM's resource group already has a single, central
 * Log Analytics workspace (workspace-pdcibwky2f5ms) wired up via ONE
 * existing 'apimDiagnosticSettings' resource on the APIM service — that
 * already ships every API's gateway/LLM logs there. Azure caps diagnostic
 * settings at 5 per resource, so adding a per-lab one here would both be
 * redundant and eat into a shared, scarce budget — see main.bicep, which
 * just reads that existing workspace's id back out for the notebook.
 *
 * Every resource name below is prefixed with "gemini-models-" so it can never
 * collide with another lab's resources on the same shared APIM instance,
 * following the same convention adopted after the finops-framework outage
 * (see that lab's main.bicep for the incident writeup).
 *
 * PER-SUBSCRIPTION GOOGLE API KEYS
 * ---------------------------------
 * Gemini's free-tier quota (20 requests/day) is capped per Google Cloud
 * project + model, NOT per Azure APIM subscription. Earlier this module gave
 * all 3 APIM subscriptions the same single Google API key as a backend
 * credential, so they all drained ONE shared 20/day quota together.
 *
 * Now each subscription's Google API key comes from `geminiApiKeysBySubscription`
 * (a separate object param, keyed by `apimSubscriptionsConfig[].name` — kept
 * apart from apimSubscriptionsConfig because Bicep's @secure() decorator only
 * supports string/object params, not array), ideally one key per DIFFERENT
 * Google Cloud project so each subscription gets its own independent 20/day
 * quota. Each key is stored as a secret Named Value on the shared APIM
 * (`gemini-models-key-<subscription.name>`), and the inbound policy on both
 * APIs picks the right one at request time via a `<choose>` on
 * `context.Subscription.Id` — so the backend resources below carry NO static
 * credential at all; the header is injected per-call.
 */

// ------------------
//    PARAMETERS
// ------------------

param apiManagementName string
param apimLoggerId string = ''

param geminiInferenceAPIPath string
param openAICompatibleAPIPath string
param geminiInferenceAPIName string
param openAICompatibleAPIName string

param geminiAPIURL string

@description('Fallback Google Gemini API key. Only used if an inbound request does not match any subscription in apimSubscriptionsConfig (defensive default — with subscriptionRequired: true, every real call should match one of the per-subscription branches instead).')
@secure()
param geminiAPIKey string

@description('One entry per Azure APIM subscription: { name, displayName }. No secrets in here — see geminiApiKeysBySubscription below (Bicep\'s @secure() decorator only supports string/object, not array).')
param apimSubscriptionsConfig array = []

@description('Maps each apimSubscriptionsConfig[].name to its own Google Gemini API key — ideally from a DIFFERENT Google Cloud project per entry, so free-tier 20-requests/day quotas stay independent instead of stacking on top of each other.')
@secure()
param geminiApiKeysBySubscription object = {}

// ------------------
//    RESOURCES
// ------------------

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apiManagementName
}

// ---- Per-subscription Google API keys, stored as secret Named Values ----

var defaultKeyName = 'gemini-models-key-default'

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/namedvalues
resource apimSubscriptionApiKeyNamedValues 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = [for subscription in apimSubscriptionsConfig: {
  parent: apim
  name: 'gemini-models-key-${subscription.name}'
  properties: {
    displayName: 'gemini-models-key-${subscription.name}'
    secret: true
    value: geminiApiKeysBySubscription[subscription.name]
  }
}]

resource defaultApiKeyNamedValue 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: defaultKeyName
  properties: {
    displayName: defaultKeyName
    secret: true
    value: geminiAPIKey
  }
}

// Builds one <when> branch per subscription. context.Subscription.Id is the
// subscription's short resource name (what we set via `name:` on the
// apimSubscription resource below, e.g. 'gemini-models-subscription1') — NOT
// context.Subscription.Name, which is a different, undocumented-for-this-use
// property; using the wrong one would make every branch silently miss and
// fall through to <otherwise>, quietly putting every subscription back on
// the same shared key with no error at all. XML attribute quoting uses
// &quot; (instead of switching to single quotes) purely to avoid fighting
// Bicep's own single-quoted string escaping.
// Each branch also stashes which Named Value it used into a context variable,
// echoed back as an `x-debug-key-source` response header in <outbound> below —
// so you can confirm, on any single test call, which Google key a given
// subscription actually routed to, instead of only finding out after a day
// of quota behavior.
var geminiChooseWhens = [for subscription in apimSubscriptionsConfig: '<when condition="@(context.Subscription != null &amp;&amp; context.Subscription.Id == &quot;gemini-models-${subscription.name}&quot;)"><set-header name="x-goog-api-key" exists-action="override"><value>{{gemini-models-key-${subscription.name}}}</value></set-header><set-variable name="geminiKeySource" value="gemini-models-key-${subscription.name}" /></when>']

var openAIChooseWhens = [for subscription in apimSubscriptionsConfig: '<when condition="@(context.Subscription != null &amp;&amp; context.Subscription.Id == &quot;gemini-models-${subscription.name}&quot;)"><set-header name="Authorization" exists-action="override"><value>Bearer {{gemini-models-key-${subscription.name}}}</value></set-header><set-variable name="geminiKeySource" value="gemini-models-key-${subscription.name}" /></when>']

// ---- Native Gemini API ----

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis
resource geminiAPI 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  name: geminiInferenceAPIName
  parent: apim
  properties: {
    apiType: 'http'
    description: 'Gemini Inference API (gemini-models lab)'
    displayName: 'Gemini Inference API (gemini-models lab)'
    format: 'openapi+json'
    path: geminiInferenceAPIPath
    protocols: [
      'https'
    ]
    subscriptionKeyParameterNames: {
      header: 'x-goog-api-key'
      query: 'x-goog-api-key'
    }
    subscriptionRequired: true
    type: 'http'
    value: string(loadJsonContent('../../modules/apim/v3/specs/PassThrough.json')
  )}
}

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/backends
resource backendGeminiAPI 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  name: 'gemini-models-backend'
  parent: apim
  properties: {
    description: 'Gemini backend (gemini-models lab)'
    url: geminiAPIURL
    protocol: 'http'
    // No static credentials here on purpose — see the module header comment.
    // The per-subscription Google API key is injected by the inbound policy
    // below (x-goog-api-key set from a Named Value keyed on the calling
    // subscription), so each Azure APIM subscription keeps its own Google
    // free-tier quota instead of sharing one.
    circuitBreaker: {
      rules: [
        {
          failureCondition: {
            count: 1
            errorReasons: [
              'Server errors'
            ]
            interval: 'PT5M'
            statusCodeRanges: [
              {
                min: 429
                max: 429
              }
            ]
          }
          name: 'geminiBreakerRule'
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

// APIM's <choose> policy element requires at least one <when> branch — an
// empty apimSubscriptionsConfig (the default []) must skip <choose> entirely
// rather than emit <choose><otherwise>...</otherwise></choose> with none.
var geminiInboundAuth = length(apimSubscriptionsConfig) > 0
  ? '<choose>${join(geminiChooseWhens, '')}<otherwise><set-header name="x-goog-api-key" exists-action="override"><value>{{${defaultKeyName}}}</value></set-header><set-variable name="geminiKeySource" value="${defaultKeyName}" /></otherwise></choose>'
  : '<set-header name="x-goog-api-key" exists-action="override"><value>{{${defaultKeyName}}}</value></set-header><set-variable name="geminiKeySource" value="${defaultKeyName}" />'

var debugOutbound = '<set-header name="x-debug-key-source" exists-action="override"><value>@((string)context.Variables.GetValueOrDefault("geminiKeySource","unknown"))</value></set-header>'

// CORS so a browser-based frontend (e.g. a static HTML demo page opened
// locally, origin "null" or file://) can call this API directly with
// fetch(). allow-credentials is false and every real call still requires a
// valid subscription key (Authorization: Bearer / x-goog-api-key) — CORS
// only controls which origins the BROWSER lets read the response, it adds
// no access on its own. allowed-origins "*" is intentionally wide open,
// fine for a demo/playground lab; tighten to specific origin(s) if this
// ever serves anything other than local demo pages. expose-headers "*" is
// what lets the frontend read the x-debug-key-source header via
// response.headers.get(...). APIM answers CORS preflight (OPTIONS)
// requests itself, before subscription-key validation, so preflight works
// even without a key.
var corsPolicy = '<cors allow-credentials="false"><allowed-origins><origin>*</origin></allowed-origins><allowed-methods preflight-result-max-age="300"><method>GET</method><method>POST</method><method>OPTIONS</method></allowed-methods><allowed-headers><header>*</header></allowed-headers><expose-headers><header>*</header></expose-headers></cors>'

var geminiAPIPolicyXml = '<policies><inbound><base />${corsPolicy}${geminiInboundAuth}<set-backend-service backend-id="${backendGeminiAPI.name}" /></inbound><backend><base /></backend><outbound><base />${debugOutbound}</outbound><on-error><base /></on-error></policies>'

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis/policies
resource geminiAPIPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  name: 'policy'
  parent: geminiAPI
  properties: {
    format: 'rawxml'
    value: geminiAPIPolicyXml
  }
  dependsOn: [
    apimSubscriptionApiKeyNamedValues
    defaultApiKeyNamedValue
  ]
}

resource geminiAPIDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = if (length(apimLoggerId) > 0) {
  parent: geminiAPI
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

// ---- OpenAI-compatible API ----

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis
resource openAIAPI 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  name: openAICompatibleAPIName
  parent: apim
  properties: {
    apiType: 'http'
    description: 'OpenAI-compatible Gemini Inference API (gemini-models lab)'
    displayName: 'OpenAI-compatible Gemini Inference API (gemini-models lab)'
    format: 'openapi+json'
    path: openAICompatibleAPIPath
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
    value: string(loadJsonContent('../../modules/apim/v3/specs/PassThrough.json')
  )}
}

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/backends
resource backendGeminiWithOpenAICompatibility 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  name: 'gemini-models-backend-openai'
  parent: apim
  properties: {
    description: 'Gemini backend, OpenAI-compatible (gemini-models lab)'
    url: geminiAPIURL
    protocol: 'http'
    // No static credentials here — see backendGeminiAPI above and the
    // module header comment. The Authorization: Bearer header is injected
    // per-request by the inbound policy below.
    circuitBreaker: {
      rules: [
        {
          failureCondition: {
            count: 1
            errorReasons: [
              'Server errors'
            ]
            interval: 'PT5M'
            statusCodeRanges: [
              {
                min: 429
                max: 429
              }
            ]
          }
          name: 'geminiBreakerRule'
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

var openAIInboundAuth = length(apimSubscriptionsConfig) > 0
  ? '<choose>${join(openAIChooseWhens, '')}<otherwise><set-header name="Authorization" exists-action="override"><value>Bearer {{${defaultKeyName}}}</value></set-header><set-variable name="geminiKeySource" value="${defaultKeyName}" /></otherwise></choose>'
  : '<set-header name="Authorization" exists-action="override"><value>Bearer {{${defaultKeyName}}}</value></set-header><set-variable name="geminiKeySource" value="${defaultKeyName}" />'

var openAIAPIPolicyXml = '<policies><inbound><base />${corsPolicy}${openAIInboundAuth}<set-backend-service backend-id="${backendGeminiWithOpenAICompatibility.name}" /></inbound><backend><base /></backend><outbound><base />${debugOutbound}</outbound><on-error><base /></on-error></policies>'

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis/policies
resource openAIAPIPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  name: 'policy'
  parent: openAIAPI
  properties: {
    format: 'rawxml'
    value: openAIAPIPolicyXml
  }
  dependsOn: [
    apimSubscriptionApiKeyNamedValues
    defaultApiKeyNamedValue
  ]
}

resource openAIAPIDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = if (length(apimLoggerId) > 0) {
  parent: openAIAPI
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

// ---- Isolation: a Product scoped to ONLY this lab's two APIs ----
// Subscriptions below are scoped to this Product (not '/apis', which would
// grant access to every API on the shared instance, including other labs').

resource product 'Microsoft.ApiManagement/service/products@2024-06-01-preview' = {
  parent: apim
  name: 'gemini-models-product'
  properties: {
    displayName: 'Gemini Models (lab)'
    description: 'Scopes access to only the gemini-models lab APIs on the shared APIM instance.'
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}

resource productGeminiLink 'Microsoft.ApiManagement/service/products/apiLinks@2024-06-01-preview' = {
  parent: product
  name: 'gemini-models-gemini-api-link'
  properties: {
    apiId: geminiAPI.id
  }
}

resource productOpenAILink 'Microsoft.ApiManagement/service/products/apiLinks@2024-06-01-preview' = {
  parent: product
  name: 'gemini-models-openai-api-link'
  properties: {
    apiId: openAIAPI.id
  }
}

// https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/subscriptions
resource apimSubscription 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = [for subscription in apimSubscriptionsConfig: if (length(apimSubscriptionsConfig) > 0) {
  name: 'gemini-models-${subscription.name}'
  parent: apim
  properties: {
    allowTracing: true
    displayName: subscription.displayName
    scope: product.id // scoped to THIS lab's product only — not '/apis' (all APIs on the shared instance)
    state: 'active'
  }
}]

// ------------------
//    OUTPUTS
// ------------------

output geminiAPIId string = geminiAPI.id
output openAIAPIId string = openAIAPI.id

#disable-next-line outputs-should-not-contain-secrets
output apimSubscriptions array = [for (subscription, i) in apimSubscriptionsConfig: {
  name: subscription.name
  displayName: subscription.displayName
  key: apimSubscription[i].listSecrets().primaryKey
}]
