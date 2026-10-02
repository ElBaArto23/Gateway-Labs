// // ============================================================================
//  content-safety / main.bicep  —  ADAPTADO PARA USAR EL APIM COMPARTIDO
// ============================================================================
//  Cambios respecto al original:
//   1. Ya NO se crea una instancia propia de APIM (se elimina `apimModule`).
//      En su lugar se usa la APIM del lab "Shared APIM Gateway", que puede
//      vivir en OTRO resource group.
//   2. Los recursos que viven DENTRO del APIM compartido (backend, product,
//      subscriptions) se crearon en un módulo aparte —
//      `apim-shared-resources.bicep` — porque Bicep no permite declarar un
//      recurso hijo (`parent: apim`) de un `existing` que apunta a otro
//      resource group directamente en este archivo (error BCP165: "You must
//      use modules to deploy resources to a different scope"). Ese módulo
//      se invoca aquí abajo con `scope: resourceGroup(sharedApimResourceGroupName)`.
//   3. Esos recursos se prefijan con `labPrefix` para no chocar con otros
//      labs que corran sobre la misma instancia (regla del README del lab
//      compartido).
//   4. Content Safety puede mandar sus logs al Log Analytics Workspace
//      compartido en vez de crear uno nuevo — ver `diagnosticSettings` abajo.
//   5. policy.xml usa un segundo token propio, {content-safety-endpoint},
//      que se reemplaza aqui abajo (ver policyXml del inferenceAPIModule)
//      con el endpoint real de Content Safety, porque el bloque on-error
//      necesita llamar a Content Safety directamente con una URL absoluta
//      (send-request no admite set-backend-service como hijo).
// ============================================================================

// ------------------
//    PARAMETERS
// ------------------

param aiServicesConfig array = []
param modelsConfig array = []
param apimSubscriptionsConfig array = []
param inferenceAPIType string = 'AzureOpenAI'
param foundryProjectName string = 'default'

@description('Nombre del resource group donde vive la APIM COMPARTIDA (output "resourceGroupName" del lab Shared APIM Gateway).')
param sharedApimResourceGroupName string

@description('Nombre de la instancia de APIM COMPARTIDA (output "apimServiceName" del lab Shared APIM Gateway).')
param sharedApimServiceName string

@description('Resource ID del Log Analytics Workspace compartido (mismo que usa el Application Insights del lab Shared APIM Gateway). Déjalo vacío si todavía no expones este output ahí — ver MIGRATION-NOTES.md.')
param sharedLogAnalyticsWorkspaceId string = ''

@description('Resource ID del Logger de Azure Monitor ya configurado en la APIM compartida (output "apimLoggerId" del lab Shared APIM Gateway). Conecta esta API al mismo Application Insights que usan los demás labs. Déjalo vacío si no lo tienes a mano.')
param sharedApimLoggerId string = ''

@description('Nombre único de este lab dentro del APIM compartido — por convención, el mismo nombre de la carpeta del lab (p.ej. "content-safety"). Se usa para nombrar su API, backend, product y subscriptions, de forma que esta API viva de forma completamente independiente de las de otros labs sobre la misma APIM (sin valor por defecto a propósito: cada lab debe elegir el suyo explícitamente).')
param labPrefix string

// ------------------
//    VARIABLES
// ------------------

var resourceSuffix = uniqueString(subscription().id, resourceGroup().id)

// ------------------
//    RESOURCES
// ------------------

// 1. Content Safety
resource contentSafetyResource 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: 'contentsafety-${resourceSuffix}'
  location: resourceGroup().location
  sku: {
    name: 'S0'
  }
  kind: 'ContentSafety'
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: toLower('contentsafety-${resourceSuffix}')
  }
}

resource raiBlocklist 'Microsoft.CognitiveServices/accounts/raiBlocklists@2025-06-01' = {
  parent: contentSafetyResource
  name: 'blocklist1' // este nombre está hard-codeado en policy.xml — no cambiar sin actualizar policy.xml
  properties: {
    description: 'Forbidden inputs blocklist'
  }
}

// Manda los logs de recursos de Content Safety al Log Analytics COMPARTIDO,
// para que quede todo el telemetry de todos los labs en un solo workspace
// (esto es lo que resuelve "que conecte su... el insight").
resource contentSafetyDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(sharedLogAnalyticsWorkspaceId)) {
  name: 'diag-to-shared-loganalytics'
  scope: contentSafetyResource
  properties: {
    workspaceId: sharedLogAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
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

// 2. APIM compartida — backend, product y subscriptions de este lab.
//    Módulo scoped al resource group de la APIM compartida (puede ser
//    distinto del resource group de este lab). Ver apim-shared-resources.bicep.
module apimLabResources 'apim-shared-resources.bicep' = {
  name: 'apimLabResources'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apimServiceName: sharedApimServiceName
    labPrefix: labPrefix
    contentSafetyEndpoint: contentSafetyResource.properties.endpoint
    apimSubscriptionsConfig: apimSubscriptionsConfig
  }
}

// 3. AI Foundry
module foundryModule '../../modules/cognitive-services/v3/foundry.bicep' = {
  name: 'foundryModule'
  params: {
    aiServicesConfig: aiServicesConfig
    modelsConfig: modelsConfig
    apimPrincipalId: apimLabResources.outputs.apimPrincipalId // antes: apimModule.outputs.principalId
    foundryProjectName: foundryProjectName
  }
}

var cognitiveServicesReaderDefinitionID = resourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
// sharedApimServiceName/sharedApimResourceGroupName (parámetros, no un
// output de módulo) entran en el guid a propósito: si algún día este lab
// apunta a OTRA APIM compartida, esto genera una asignación de rol NUEVA en
// vez de intentar "actualizar" el principalId de una ya existente — eso es
// lo que Azure rechaza con RoleAssignmentUpdateNotPermitted (nos pasó en
// foundryModule al redesplegar sobre un resource group viejo que tenía la
// identidad de la APIM anterior). No se puede usar directamente
// `apimLabResources.outputs.apimPrincipalId` aquí — Bicep lo rechaza con
// BCP120 porque el `name` de un roleAssignment debe poder calcularse ANTES
// de que corra el deployment, y un output de módulo solo se conoce después.
resource contentSafetyRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: contentSafetyResource
  name: guid(subscription().id, resourceGroup().id, contentSafetyResource.name, cognitiveServicesReaderDefinitionID, sharedApimResourceGroupName, sharedApimServiceName)
  properties: {
    roleDefinitionId: cognitiveServicesReaderDefinitionID
    principalId: apimLabResources.outputs.apimPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource contentSafetyRoleAssignmentToDeployer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: contentSafetyResource
  name: guid(subscription().id, resourceGroup().id, contentSafetyResource.name, cognitiveServicesReaderDefinitionID, deployer().objectId)
  properties: {
    roleDefinitionId: cognitiveServicesReaderDefinitionID
    principalId: deployer().objectId
  }
}

// 4. APIM Inference API
//    El error BCP037 de tu deployment reveló el interfaz REAL de este
//    módulo (la lista de "Permissible properties" del propio error):
//    apiManagementName, apimLoggerId, appInsightsId,
//    appInsightsInstrumentationKey, appInsightsLoggerId,
//    inferenceAPIDescription, inferenceAPIDisplayName, inferenceAPIName,
//    inferenceBackendPoolName, resourceSuffix — además de los 5 que ya
//    usábamos (policyXml, aiServicesConfig, inferenceAPIType,
//    inferenceAPIPath, configureCircuitBreaker), que SÍ son válidos porque
//    no salieron marcados como error.
//    ✅ Confirmado con ese error: el nombre correcto del parámetro es
//    `apiManagementName`, no `apimServiceName`.
//    policyXml aquí hace un replace() del token {content-safety-endpoint}
//    (que policy.xml trae literal) por el endpoint real de este deployment,
//    para que el bloque on-error pueda llamar a Content Safety con una URL
//    absoluta desde send-request (que no admite set-backend-service).
//
//    (22 de sept: se quitó el paso "LLM-as-judge" que se había agregado a
//    policy.xml -- se alejaba del flujo estándar del lab, umbral +
//    Prompt Shield + blocklist + bloqueo 403 -- y con él el segundo
//    replace()/token {model-gateway-endpoint} que traía. Ya no aplica.)
module inferenceAPIModule '../../modules/apim/v2/inference-api.bicep' = {
  name: 'inferenceAPIModule'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apiManagementName: sharedApimServiceName
    apimLoggerId: sharedApimLoggerId
    policyXml: replace(loadTextContent('policy.xml'), '{content-safety-endpoint}', contentSafetyResource.properties.endpoint)
    aiServicesConfig: foundryModule.outputs.extendedAIServicesConfig
    inferenceAPIType: inferenceAPIType
    inferenceAPIPath: labPrefix // la API queda con el path propio del lab (p.ej. "content-safety"), separada de la de cualquier otro lab
    inferenceAPIName: labPrefix // fijamos el NOMBRE del recurso API nosotros mismos — así sabemos con certeza qué usar para el link product↔api de abajo
    inferenceAPIDisplayName: 'Content Safety Lab (${labPrefix})'
    inferenceAPIDescription: 'Content Safety inference API — lab "${labPrefix}" sobre el APIM compartido'
    inferenceBackendPoolName: '${labPrefix}-backend-pool' // prefijado, para no chocar con el backend pool de otro lab
    configureCircuitBreaker: true
  }
  dependsOn: [
    apimLabResources
  ]
}

// 5. Vincular el Product de este lab con la API que acabamos de crear.
//    Ahora que fijamos `inferenceAPIName: labPrefix` arriba, ya sabemos con
//    certeza el nombre del recurso API — ya no depende de adivinar qué
//    nombre usa inference-api.bicep internamente. Va en su propio módulo
//    (mismo motivo que apim-shared-resources.bicep: cruza de resource
//    group) y depende de los 2 anteriores para evitar una dependencia
//    circular.
module apimProductApiLink 'apim-product-api-link.bicep' = {
  name: 'apimProductApiLink'
  scope: resourceGroup(sharedApimResourceGroupName)
  params: {
    apimServiceName: sharedApimServiceName
    productName: '${labPrefix}-product'
    apiName: labPrefix
  }
  dependsOn: [
    apimLabResources
    inferenceAPIModule
  ]
}

// ------------------
//    OUTPUTS
// ------------------

output apimServiceId string = apimLabResources.outputs.apimId
output apimResourceGatewayURL string = apimLabResources.outputs.apimGatewayUrl
output apimSubscriptions array = apimLabResources.outputs.apimSubscriptions

// Para que el notebook pueda llamar DIRECTO a Content Safety (no a través
// del gateway) y obtener el puntaje real de severidad — necesario para
// decidir qué modelo usar según el nivel de riesgo del mensaje.
output contentSafetyEndpoint string = contentSafetyResource.properties.endpoint
output contentSafetyResourceName string = contentSafetyResource.name
