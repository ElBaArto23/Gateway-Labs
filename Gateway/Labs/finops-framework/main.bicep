// ------------------
//    PARAMETERS
// ------------------

// Backend(s) to register in the shared APIM for this lab's API.
// Points at the AI Services / Foundry account that already has gpt-5.4 and
// gpt-5.4-mini deployed — no model deployment happens from this file anymore.
param aiServicesConfig array = []
param apimSubscriptionsConfig array = []
param apimProductsConfig array = []
param apimUsersConfig array = []
param inferenceAPIType string = 'AzureOpenAI'
param inferenceAPIPath string = 'finops-framework-inference' // Path to the inference API in the APIM service
param inferenceAPIName string = 'finops-framework-inference-api' // Unique API resource id in the shared APIM - MUST be unique across all labs, or it will overwrite another lab's API (this was the root cause of the earlier outage)
param inferenceAPIDisplayName string = 'FinOps Framework Inference API'

@description('Name of the shared APIM instance this lab attaches to.')
param sharedApimName string = 'apim-shared-pdcibwky2f5ms'

@description('Resource group of the shared APIM instance.')
param sharedApimResourceGroupName string = 'rg-shared-apim-gateway-V2'

@description('Name of the shared Log Analytics Workspace this lab attaches to (instead of deploying its own).')
param sharedLogAnalyticsWorkspaceName string = 'workspace-pdcibwky2f5ms'

@description('Name of the shared Application Insights this lab attaches to (instead of deploying its own).')
param sharedApplicationInsightsName string = 'insights-pdcibwky2f5ms'

// ------------------
//    VARIABLES
// ------------------
var resourceSuffix = uniqueString(subscription().id, resourceGroup().id)


// ------------------
//    RESOURCES
// ------------------

// 1 & 2. Shared Log Analytics Workspace + Application Insights (existing —
// not deployed by this lab). Same reasoning as the shared APIM below: this
// lab attaches to the environment's shared observability stack instead of
// provisioning its own per-lab workspace/App Insights. Besides avoiding
// duplicate resources, this also stops this lab from consuming one of the
// shared APIM's diagnostic-settings slots (Azure caps that at 5 per
// resource — see apim-shared-resources.bicep, which used to add one here).
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: sharedLogAnalyticsWorkspaceName
  scope: resourceGroup(sharedApimResourceGroupName)
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: sharedApplicationInsightsName
  scope: resourceGroup(sharedApimResourceGroupName)
}

// 3. Shared API Management (existing — not deployed by this lab)
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: sharedApimName
  scope: resourceGroup(sharedApimResourceGroupName)
}

// Built-in "azuremonitor" logger on the shared APIM, used for LLM/gateway
// diagnostics (ApiManagementGatewayLlmLog / ApiManagementGatewayLogs).
// ASSUMPTION: this logger already exists on the shared APIM (apim.bicep
// creates it as "azuremonitor" whenever a Log Analytics workspace is wired
// up). Verify the name in the portal before first deploy — if it's called
// something else, update sharedApimLoggerName below instead of recreating it.
@description('Name of the existing azureMonitor logger on the shared APIM.')
param sharedApimLoggerName string = 'azuremonitor'

resource sharedApimLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' existing = {
  parent: apim
  name: sharedApimLoggerName
}

// NOTE: gateway/LLM logs (ApiManagementGatewayLogs / ApiManagementGatewayLlmLog)
// already land in the shared workspace above via the shared APIM's own
// base diagnostic setting (confirmed in the portal: "apimDiagnosticSettings"
// → workspace-pdcibwky2f5ms). This lab used to add its own extra diagnostic
// setting pointing at a lab-only workspace (in apim-shared-resources.bicep) —
// that has been removed since it's now redundant and was one of only 5
// diagnostic-settings slots available on the shared APIM.

// 4. APIM Inference API — creates a NEW API on the shared APIM for this lab
var productTokenLimitsXml = join(map(apimProductsConfig, p => '<when condition=\'@((string)context.Product?.Id == "${p.name}")\'><llm-token-limit counter-key="@(context.Subscription.Id)" tokens-per-minute="${p.tpm}" token-quota="${p.tokenQuota}" token-quota-period="${p.tokenQuotaPeriod}" estimate-prompt-tokens="false" remaining-tokens-variable-name="remainingTokens" remaining-quota-tokens-variable-name="remainingQuotaTokens" tokens-consumed-variable-name="consumedTokens" /></when>'), '')

module inferenceAPIModule '../../modules/apim/v2/inference-api.bicep' = {
  name: 'inferenceAPIModule'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apiManagementName: sharedApimName
    policyXml: replace(loadTextContent('policy.xml'), '{product-token-limits}', productTokenLimitsXml)
    apimLoggerId: sharedApimLogger.id
    aiServicesConfig: aiServicesConfig
    inferenceAPIType: inferenceAPIType
    inferenceAPIPath: inferenceAPIPath
    inferenceAPIName: inferenceAPIName
    inferenceAPIDisplayName: inferenceAPIDisplayName
  }
}

// pricingTable is now created inside sharedApimResourcesModule (it must be a
// true child of the shared workspace, and Bicep requires any *deployable*
// (non-existing) child resource to live in a module scoped to that same
// resource group — see the comment on that module below). This name is the
// single source of truth for both that module and the DCR wiring here.
var pricingTableName = 'PRICING_CL'

resource pricingDCR 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-pricing-${resourceSuffix}'
  location: resourceGroup().location
  kind: 'Direct'
  // Explicit — pricingTableName is now just a string var (no symbolic
  // reference to the actual table resource, which lives in
  // sharedApimResourcesModule), so Bicep can no longer infer this
  // dependency automatically like it used to when both were in this file.
  dependsOn: [
    sharedApimResourcesModule
  ]
  properties: {
    streamDeclarations: {
      'Custom-Json-${pricingTableName}': {
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
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalytics.id
          name: logAnalytics.name
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Custom-Json-${pricingTableName}'
        ]
        destinations: [
          logAnalytics.name
        ]
        transformKql: 'source'
        outputStream: 'Custom-${pricingTableName}'
      }
    ]
  }
}

var monitoringMetricsPublisherRoleDefinitionID = resourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
resource pricingDCRRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: pricingDCR
  name: guid(subscription().id, resourceGroup().id, pricingDCR.name, monitoringMetricsPublisherRoleDefinitionID)
    properties: {
        roleDefinitionId: monitoringMetricsPublisherRoleDefinitionID
        principalId: deployer().objectId
        principalType: 'User'
    }
}

// Same story as pricingTableName above — subscriptionQuotaTable is now
// created inside sharedApimResourcesModule.
var subscriptionQuotaTableName = 'SUBSCRIPTION_QUOTA_CL'

resource subscriptionQuotaDCR 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'dcr-quota-${resourceSuffix}'
  location: resourceGroup().location
  kind: 'Direct'
  dependsOn: [
    sharedApimResourcesModule
  ]
  properties: {
    streamDeclarations: {
      'Custom-Json-${subscriptionQuotaTableName}': {
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
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalytics.id
          name: logAnalytics.name
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Custom-Json-${subscriptionQuotaTableName}'
        ]
        destinations: [
          logAnalytics.name
        ]
        transformKql: 'source'
        outputStream: 'Custom-${subscriptionQuotaTableName}'
      }
    ]
  }
}

resource subscriptionQuotaDCRRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: subscriptionQuotaDCR
  name: guid(subscription().id, resourceGroup().id, subscriptionQuotaDCR.name, monitoringMetricsPublisherRoleDefinitionID)
    properties: {
        roleDefinitionId: monitoringMetricsPublisherRoleDefinitionID
        principalId: deployer().objectId
        principalType: 'User'
    }
}


resource alertsWorkbook 'Microsoft.Insights/workbooks@2022-04-01' = {
  name: guid(resourceGroup().id, resourceSuffix, 'alertsWorkbook')
  location: resourceGroup().location
  kind: 'shared'
  properties: {
    displayName: 'Alerts Workbook'
    serializedData: loadTextContent('workbooks/alerts.json')
    sourceId: logAnalytics.id
    category: 'workbook'
  }
}

resource azureOpenAIInsightsWorkbook 'Microsoft.Insights/workbooks@2022-04-01' = {
  name: guid(resourceGroup().id, resourceSuffix, 'azureOpenAIInsights')
  location: resourceGroup().location
  kind: 'shared'
  properties: {
    displayName: 'Azure OpenAI Insights'
    serializedData: string(loadJsonContent('workbooks/azure-openai-insights.json'))
    sourceId: logAnalytics.id
    category: 'workbook'
  }
}

resource openAIUsageWorkbook 'Microsoft.Insights/workbooks@2022-04-01' = {
  name: guid(resourceGroup().id, resourceSuffix, 'costAnalysis')
  location: resourceGroup().location
  kind: 'shared'
  properties: {
    displayName: 'Cost Analysis'
    serializedData: replace(loadTextContent('workbooks/cost-analysis.json'), '{workspace-id}', logAnalytics.id)
    sourceId: logAnalytics.id
    category: 'workbook'
  }
}

resource updateSubscriptionWorkflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'la-update-sub-${resourceSuffix}'
  location: resourceGroup().location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          defaultValue: {}
          type: 'Object'
        }
      }
      triggers: {
        When_an_Alert_is_Received: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {
              type: 'object'
              properties: {
                schemaId: {
                  type: 'string'
                }
                data: {
                  type: 'object'
                  properties: {
                    essentials: {
                      type: 'object'
                      properties: {
                        alertId: {
                          type: 'string'
                        }
                        alertRule: {
                          type: 'string'
                        }
                        targetResourceType: {
                          type: 'string'
                        }
                        alertRuleID: {
                          type: 'string'
                        }
                        severity: {
                          type: 'string'
                        }
                        signalType: {
                          type: 'string'
                        }
                        monitorCondition: {
                          type: 'string'
                        }
                        targetResourceGroup: {
                          type: 'string'
                        }
                        monitoringService: {
                          type: 'string'
                        }
                        alertTargetIDs: {
                          type: 'array'
                          items: {
                            type: 'string'
                          }
                        }
                        configurationItems: {
                          type: 'array'
                          items: {
                            type: 'string'
                          }
                        }
                        originAlertId: {
                          type: 'string'
                        }
                        firedDateTime: {
                          type: 'string'
                        }
                        description: {
                          type: 'string'
                        }
                        essentialsVersion: {
                          type: 'string'
                        }
                        alertContextVersion: {
                          type: 'string'
                        }
                        investigationLink: {
                          type: 'string'
                        }
                      }
                    }
                    alertContext: {
                      type: 'object'
                      properties: {
                        properties: {
                          type: 'object'
                          properties: {}
                        }
                        conditionType: {
                          type: 'string'
                        }
                        condition: {
                          type: 'object'
                          properties: {
                            windowSize: {
                              type: 'string'
                            }
                            allOf: {
                              type: 'array'
                              items: {
                                type: 'object'
                                properties: {
                                  searchQuery: {
                                    type: 'string'
                                  }
                                  metricMeasureColumn: {}
                                  targetResourceTypes: {
                                    type: 'string'
                                  }
                                  operator: {
                                    type: 'string'
                                  }
                                  threshold: {
                                    type: 'string'
                                  }
                                  timeAggregation: {
                                    type: 'string'
                                  }
                                  dimensions: {
                                    type: 'array'
                                    items: {
                                      type: 'object'
                                      properties: {
                                        name: {
                                          type: 'string'
                                        }
                                        value: {
                                          type: 'string'
                                        }
                                      }
                                      required: [
                                        'name'
                                        'value'
                                      ]
                                    }
                                  }
                                  metricValue: {
                                    type: 'integer'
                                  }
                                  failingPeriods: {
                                    type: 'object'
                                    properties: {
                                      numberOfEvaluationPeriods: {
                                        type: 'integer'
                                      }
                                      minFailingPeriodsToAlert: {
                                        type: 'integer'
                                      }
                                    }
                                  }
                                  linkToSearchResultsUI: {
                                    type: 'string'
                                  }
                                  linkToFilteredSearchResultsUI: {
                                    type: 'string'
                                  }
                                  linkToSearchResultsAPI: {
                                    type: 'string'
                                  }
                                  linkToFilteredSearchResultsAPI: {
                                    type: 'string'
                                  }
                                  event: {}
                                }
                                required: [
                                  'searchQuery'
                                  'metricMeasureColumn'
                                  'targetResourceTypes'
                                  'operator'
                                  'threshold'
                                  'timeAggregation'
                                  'dimensions'
                                  'metricValue'
                                  'failingPeriods'
                                  'linkToSearchResultsUI'
                                  'linkToFilteredSearchResultsUI'
                                  'linkToSearchResultsAPI'
                                  'linkToFilteredSearchResultsAPI'
                                  'event'
                                ]
                              }
                            }
                            windowStartTime: {
                              type: 'string'
                            }
                            windowEndTime: {
                              type: 'string'
                            }
                          }
                        }
                      }
                    }
                    customProperties: {
                      type: 'object'
                      properties: {}
                    }
                  }
                }
              }
            }
          }
        }
      }
      actions: {
        Update_APIM_Subscription_Status: {
          runAfter: {}
          type: 'Http'
          inputs: {
            uri: 'https://management.azure.com/subscriptions/${subscription().subscriptionId}/resourceGroups/${sharedApimResourceGroupName}/providers/Microsoft.ApiManagement/service/${apim.name}/subscriptions/@{triggerBody()?[\'data\']?[\'alertContext\']?[\'condition\']?[\'allOf\']?[0]?[\'dimensions\']?[0]?[\'value\']}?api-version=2024-06-01-preview'
            method: 'PATCH'
            headers: {
              'Content-Type': 'application/json'
            }
            body: {
              properties: {
                state: '@if(contains(triggerBody()?[\'data\']?[\'essentials\']?[\'alertRule\'],\'suspend\'),\'suspended\',\'active\')'
              }
            }
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: 'https://management.azure.com/'
            }
          }
          runtimeConfiguration: {
            contentTransfer: {
              transferMode: 'Chunked'
            }
          }
        }
      }
      outputs: {}
    }
    parameters: {
      '$connections': {
        value: {}
      }
    }
  }
}

resource workflowDiagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: updateSubscriptionWorkflow
  name: 'workflowDiagnosticSettings'
  properties: {
    workspaceId: logAnalytics.id
    logs: [
      {
        categoryGroup: 'AllLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// Everything that must be deployed INTO the shared APIM's resource group
// (products, product policies, product<->API links, users, subscriptions,
// this lab's pricing/quota custom tables on the shared workspace, and the
// role assignment that lets updateSubscriptionWorkflow suspend/activate
// subscriptions) lives in this module — Bicep requires a module (with an
// explicit scope) for any deployable resource targeting a different
// resource group.
module sharedApimResourcesModule 'apim-shared-resources.bicep' = {
  name: 'sharedApimResourcesModule'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apiManagementName: sharedApimName
    apimProductsConfig: apimProductsConfig
    apimUsersConfig: apimUsersConfig
    apimSubscriptionsConfig: apimSubscriptionsConfig
    inferenceAPIId: inferenceAPIModule.outputs.apiId
    productsPolicyXml: loadTextContent('products-policy.xml')
    sharedLogAnalyticsWorkspaceName: sharedLogAnalyticsWorkspaceName
    pricingTableName: pricingTableName
    subscriptionQuotaTableName: subscriptionQuotaTableName
    logicAppPrincipalId: updateSubscriptionWorkflow.identity.principalId
    logicAppName: updateSubscriptionWorkflow.name
  }
}

resource actionGroupUpdateSub 'microsoft.insights/actionGroups@2024-10-01-preview' = {
  name: 'actiongroup-update-sub-${resourceSuffix}'
  location: 'Global'
  properties: {
    groupShortName: 'Update Sub'
    enabled: true
    emailReceivers: []
    smsReceivers: []
    webhookReceivers: []
    eventHubReceivers: []
    itsmReceivers: []
    azureAppPushReceivers: []
    automationRunbookReceivers: []
    voiceReceivers: []
    logicAppReceivers: [
      {
        name: 'update-subscription-state'
        resourceId: updateSubscriptionWorkflow.id
        callbackUrl: '${updateSubscriptionWorkflow.listCallbackUrl().basePath}/triggers/When_an_Alert_is_Received/paths/invoke?api-version=${updateSubscriptionWorkflow.listCallbackUrl().queries['api-version']}&sp=${updateSubscriptionWorkflow.listCallbackUrl().queries.sp}&sv=${updateSubscriptionWorkflow.listCallbackUrl().queries.sv}&sig=${updateSubscriptionWorkflow.listCallbackUrl().queries.sig}'
        useCommonAlertSchema: true
      }
    ]
    azureFunctionReceivers: []
    armRoleReceivers: []
  }
}

resource ruleSuspendSub 'microsoft.insights/scheduledqueryrules@2025-01-01-preview' = {
  name: 'alert-suspend-sub-${resourceSuffix}'
  location: 'westeurope'
  kind: 'LogAlert'
  dependsOn: [
    sharedApimResourcesModule
  ]
  properties: {
    displayName: 'alert-suspend-subscriptions'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT5M'
    scopes: [
      logAnalytics.id
    ]
    targetResourceTypes: [
      'Microsoft.OperationalInsights/workspaces'
    ]
    windowSize: 'PT5M'
    overrideQueryTimeRange: 'P2D'
    criteria: {
      allOf: [
        {
          query: 'let llmHeaderLogs = ApiManagementGatewayLlmLog\n    | where TimeGenerated >= startofmonth(now()) and TimeGenerated <= endofmonth(now())\n    | where DeploymentName != \'\';\nlet llmLogsWithSubscriptionId = llmHeaderLogs\n    | join kind=leftouter ApiManagementGatewayLogs on CorrelationId\n    | project\n        SubscriptionName = ApimSubscriptionId,\n        DeploymentName,\n        PromptTokens,\n        CompletionTokens,\n        TotalTokens;\nllmLogsWithSubscriptionId\n| join kind=inner (\n    PRICING_CL\n    | summarize arg_max(TimeGenerated, *) by Model\n    | project Model, InputTokensPrice, OutputTokensPrice\n    )\n    on $left.DeploymentName == $right.Model\n| extend InputCost = PromptTokens * InputTokensPrice\n| extend OutputCost = CompletionTokens * OutputTokensPrice\n| summarize\n    InputCost = sum(InputCost),\n    OutputCost = sum(OutputCost)\n    by SubscriptionName\n| extend TotalCost = (InputCost + OutputCost) / 1000000\n| join kind=inner (\n    SUBSCRIPTION_QUOTA_CL\n    | summarize arg_max(TimeGenerated, *) by Subscription\n    | project Subscription, CostQuota\n    )\n    on $left.SubscriptionName == $right.Subscription\n| project SubscriptionName, CostQuota, TotalCost\n| where TotalCost > CostQuota\n'
          timeAggregation: 'Count'
          dimensions: [
            {
              name: 'SubscriptionName'
              operator: 'Exclude'
              values: [
                'null'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: json('0')
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: false
    actions: {
      actionGroups: [
        actionGroupUpdateSub.id
      ]
      customProperties: {}
      actionProperties: {}
    }
  }
}

resource ruleActivateSub 'microsoft.insights/scheduledqueryrules@2025-01-01-preview' = {
  name: 'alert-activate-sub-${resourceSuffix}'
  location: 'westeurope'
  kind: 'LogAlert'
  dependsOn: [
    sharedApimResourcesModule
  ]
  properties: {
    displayName: 'alert-activate-subscriptions'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT5M'
    scopes: [
      logAnalytics.id
    ]
    targetResourceTypes: [
      'Microsoft.OperationalInsights/workspaces'
    ]
    windowSize: 'PT5M'
    overrideQueryTimeRange: 'P2D'
    criteria: {
      allOf: [
        {
          query: 'let llmHeaderLogs = ApiManagementGatewayLlmLog\n    | where TimeGenerated >= startofmonth(now()) and TimeGenerated <= endofmonth(now())\n    | where DeploymentName != \'\';\nlet llmLogsWithSubscriptionId = llmHeaderLogs\n    | join kind=leftouter ApiManagementGatewayLogs on CorrelationId\n    | project\n        SubscriptionName = ApimSubscriptionId,\n        DeploymentName,\n        PromptTokens,\n        CompletionTokens,\n        TotalTokens;\nllmLogsWithSubscriptionId\n| join kind=inner (\n    PRICING_CL\n    | summarize arg_max(TimeGenerated, *) by Model\n    | project Model, InputTokensPrice, OutputTokensPrice\n    )\n    on $left.DeploymentName == $right.Model\n| extend InputCost = PromptTokens * InputTokensPrice\n| extend OutputCost = CompletionTokens * OutputTokensPrice\n| summarize\n    InputCost = sum(InputCost),\n    OutputCost = sum(OutputCost)\n    by SubscriptionName\n| extend TotalCost = (InputCost + OutputCost) / 1000000\n| join kind=inner (\n    SUBSCRIPTION_QUOTA_CL\n    | summarize arg_max(TimeGenerated, *) by Subscription\n    | project Subscription, CostQuota\n    )\n    on $left.SubscriptionName == $right.Subscription\n| project SubscriptionName, CostQuota, TotalCost\n| where TotalCost <= CostQuota\n'
          timeAggregation: 'Count'
          dimensions: [
            {
              name: 'SubscriptionName'
              operator: 'Exclude'
              values: [
                'null'
              ]
            }
          ]
          operator: 'GreaterThan'
          threshold: json('0')
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: false
    actions: {
      actionGroups: [
        actionGroupUpdateSub.id
      ]
      customProperties: {}
      actionProperties: {}
    }
  }
}

module finOpsDashboardModule 'dashboard.bicep' = {
  name: 'finOpsDashboardModule'
  params: {
      resourceSuffix: resourceSuffix
      workspaceName: logAnalytics.name
      workspaceId: logAnalytics.id
      workbookCostAnalysisId: openAIUsageWorkbook.id
      workbookAzureOpenAIInsightsId: azureOpenAIInsightsWorkbook.id
      appInsightsId: applicationInsights.id
      appInsightsName: applicationInsights.name
    }
}


// ------------------
//    OUTPUTS
// ------------------

output logAnalyticsWorkspaceId string = logAnalytics.properties.customerId
output apimServiceId string = apim.id
output apimResourceGatewayURL string = apim.properties.gatewayUrl

#disable-next-line outputs-should-not-contain-secrets
output apimSubscriptions array = sharedApimResourcesModule.outputs.apimSubscriptions

output pricingDCREndpoint string = pricingDCR.properties.endpoints.logsIngestion
output pricingDCRImmutableId string = pricingDCR.properties.immutableId
output pricingDCRStream string = pricingDCR.properties.dataFlows[0].streams[0]
output subscriptionQuotaDCREndpoint string = subscriptionQuotaDCR.properties.endpoints.logsIngestion
output subscriptionQuotaDCRImmutableId string = subscriptionQuotaDCR.properties.immutableId
output subscriptionQuotaDCRStream string = subscriptionQuotaDCR.properties.dataFlows[0].streams[0]
