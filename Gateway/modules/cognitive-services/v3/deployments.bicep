

@description('Configuration array for the model deployments')
param modelsConfig array = []

param cognitiveServiceName string

resource cognitiveService 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: cognitiveServiceName
}

// Filtra de antemano solo los modelos que corresponden a ESTA cuenta,
// para que el recurso y el output usen exactamente la misma lista (sin condicional, sin desajuste de índices)
var accountModels = filter(modelsConfig, model => contains(cognitiveService.name, model.?aiservice ?? ''))

@batchSize(1)
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for model in accountModels: {
  name: model.name
  parent: cognitiveService
  sku: {
    name: model.sku
    capacity: model.capacity
  }
  properties: {
    model: {
      format: model.?publisher ?? model.?format
      name: model.name
      version: model.version
    }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}]

output modelDeployments array = [for (model, i) in accountModels: {
  name: modelDeployment[i].name
  resourceId: modelDeployment[i].id
  modelName: modelDeployment[i].properties.model.name
  modelVersion: modelDeployment[i].properties.model.version
  modelFormat: modelDeployment[i].properties.model.format
  description: model.?description ?? null
  supportedEndpoints: concat(
    (modelDeployment[i].properties.?capabilities.?chatCompletion ?? 'false') == 'true' ? ['/openai/v1/chat/completions'] : [],
    (modelDeployment[i].properties.?capabilities.?responses ?? 'false') == 'true' ? ['/openai/v1/responses'] : []
  )
  policies: model.?policies ?? []
}]
