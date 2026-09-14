/**
 * @module apim-shared-resources
 * @description Resources that must be deployed INTO the shared APIM's own
 * resource group (products, subscriptions, users, product policies, and
 * the role assignment that lets the lab's Logic App suspend/activate
 * subscriptions). Bicep requires any deployable (non-"existing") resource
 * targeting a different resource group to go through a module with an
 * explicit `scope` — this file is that module. Invoked from main.bicep with
 * `scope: resourceGroup(sharedApimResourceGroupName)`.
 *
 * NOTE: this module used to also create an extra diagnostic setting sending
 * this API's gateway/LLM logs to a lab-only Log Analytics workspace. That
 * was removed — the shared APIM already has a base diagnostic setting
 * ("apimDiagnosticSettings") routing AllLogs/AllMetrics to the shared
 * workspace (workspace-pdcibwky2f5ms), confirmed via
 * `az monitor diagnostic-settings list`. main.bicep now reads
 * ApiManagementGatewayLogs / ApiManagementGatewayLlmLog straight from that
 * shared workspace. Keeping the extra per-lab setting would have both
 * duplicated every row and used up one more of the 5 diagnostic-settings
 * slots Azure allows per resource — already 4/5 used across labs sharing
 * this APIM.
 */

// ------------------
//    PARAMETERS
// ------------------

param apiManagementName string
param apimProductsConfig array = []
param apimUsersConfig array = []
param apimSubscriptionsConfig array = []
param inferenceAPIId string
param productsPolicyXml string
param logicAppPrincipalId string = ''
param logicAppName string = ''

@description('Name of the shared Log Analytics Workspace (same one main.bicep reads from) that this lab\'s custom tables get created in.')
param sharedLogAnalyticsWorkspaceName string

@description('Name for this lab\'s pricing custom table — must match pricingTableName in main.bicep.')
param pricingTableName string

@description('Name for this lab\'s subscription-quota custom table — must match subscriptionQuotaTableName in main.bicep.')
param subscriptionQuotaTableName string

// ------------------
//    RESOURCES
// ------------------

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apiManagementName
}

// This module already deploys into the shared APIM's own resource group
// (scope: resourceGroup(sharedApimResourceGroupName), set by main.bicep), so
// this "existing" reference resolves to the same shared workspace main.bicep
// reads from — no explicit `scope:` needed here since it matches the
// module's own deployment scope.
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: sharedLogAnalyticsWorkspaceName
}

// pricingTable / subscriptionQuotaTable are true ARM child resources of the
// workspace (parent:), so they MUST be deployed at the same scope as their
// parent. Since the shared workspace lives in this module's resource group
// (not the lab's own), they have to be declared here rather than in
// main.bicep — Bicep rejects a deployable child resource whose computed
// scope doesn't match its containing file/module's scope.
resource pricingTable 'Microsoft.OperationalInsights/workspaces/tables@2023-09-01' = {
  parent: logAnalytics
  name: pricingTableName
  properties: {
    totalRetentionInDays: 4383
    plan: 'Analytics'
    schema: {
      name: pricingTableName
      description: 'OpenAI models pricing table for ${logAnalytics.properties.customerId}'
      columns: [
        {
          name: 'TimeGenerated'
          type: 'datetime'
        }
        {
          name: 'Model'
          type: 'string'
        }
        {
          name: 'InputTokensPrice'
          type: 'real'
        }
        {
          name: 'OutputTokensPrice'
          type: 'real'
        }
      ]
    }
    retentionInDays: 730
  }
}

resource subscriptionQuotaTable 'Microsoft.OperationalInsights/workspaces/tables@2023-09-01' = {
  parent: logAnalytics
  name: subscriptionQuotaTableName
  properties: {
    totalRetentionInDays: 4383
    plan: 'Analytics'
    schema: {
      name: subscriptionQuotaTableName
      description: 'APIM subscriptions quota table for ${logAnalytics.properties.customerId}'
      columns: [
        {
          name: 'TimeGenerated'
          type: 'datetime'
        }
        {
          name: 'Subscription'
          type: 'string'
        }
        {
          name: 'CostQuota'
          type: 'real'
        }
      ]
    }
    retentionInDays: 730
  }
}

@batchSize(1)
resource apimProduct 'Microsoft.ApiManagement/service/products@2024-06-01-preview' = [for product in apimProductsConfig: if(length(apimProductsConfig) > 0) {
  name: product.name
  parent: apim
  properties: {
    approvalRequired: true
    description: product.displayName
    displayName: product.displayName
    subscriptionRequired: true
    state: 'published'
  }
}]

@batchSize(1)
resource apimProductInferenceAPI 'Microsoft.ApiManagement/service/products/apiLinks@2024-06-01-preview' = [for (product, i) in apimProductsConfig: if(length(apimProductsConfig) > 0) {
  parent: apimProduct[i]
  name: 'openai-${apimProduct[i].name}'
  properties: {
    apiId: inferenceAPIId
  }
}]

@batchSize(1)
resource productPolicy 'Microsoft.ApiManagement/service/products/policies@2024-06-01-preview' = [for (product, i) in apimProductsConfig: if(length(apimProductsConfig) > 0) {
  name: 'policy'
  parent: apimProduct[i]
  properties: {
    format: 'rawxml'
    value: replace(replace(replace(productsPolicyXml, '{tokens-per-minute}', '${product.tpm}'), '{token-quota}', '${product.tokenQuota}'), '{token-quota-period}', '${product.tokenQuotaPeriod}')
  }
}]

@batchSize(1)
resource apimUser 'Microsoft.ApiManagement/service/users@2024-06-01-preview' = [for (user, i) in apimUsersConfig: if(length(apimUsersConfig) > 0) {
  parent: apim
  name: user.name
  properties: {
    firstName: user.firstName
    lastName: user.lastName
    email: user.email
    state: 'active'
    identities: [
      {
        provider: 'Basic'
        id: user.email
      }
    ]
  }
}]

@batchSize(1)
resource apimSubscriptions 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = [for subscription in apimSubscriptionsConfig: if(length(apimSubscriptionsConfig) > 0) {
  name: subscription.name
  parent: apim
  properties: {
    allowTracing: true
    displayName: '${subscription.displayName}'
    scope: '/products/${subscription.product}'
    state: 'active'
  }
  dependsOn: [
    apimProduct
    productPolicy
  ]
}]

var apimServiceContributorRoleDefinitionID = resourceId('Microsoft.Authorization/roleDefinitions', '312a565d-c81f-4fd8-895a-4e21e48d571c')
resource apimRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (length(logicAppPrincipalId) > 0) {
  scope: apim
  name: guid(subscription().id, resourceGroup().id, logicAppName, apimServiceContributorRoleDefinitionID)
  properties: {
    roleDefinitionId: apimServiceContributorRoleDefinitionID
    principalId: logicAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------
//    OUTPUTS
// ------------------

output apimId string = apim.id
output apimGatewayUrl string = apim.properties.gatewayUrl

#disable-next-line outputs-should-not-contain-secrets
output apimSubscriptions array = [for (subscription, i) in apimSubscriptionsConfig: {
  name: subscription.name
  displayName: subscription.displayName
  key: apimSubscriptions[i].listSecrets().primaryKey
}]
