// ============================================================================
//  content-safety / apim-shared-resources.bicep
// ============================================================================
//  Por qué existe este archivo: Bicep NO permite declarar un recurso hijo
//  (`parent: apim`) de un `existing` cuyo `scope:` apunta a OTRO resource
//  group, directamente en un archivo que se despliega en un scope distinto
//  (error BCP165: "You must use modules to deploy resources to a different
//  scope"). Por eso el backend, el product y las subscriptions de este lab
//  — que viven DENTRO de la APIM compartida, en su propio resource group —
//  se movieron a este módulo aparte, invocado desde main.bicep con
//  `scope: resourceGroup(sharedApimResourceGroupName)`.
//
//  Dentro de este módulo, `apim` SÍ se puede declarar `existing` sin
//  `scope:` extra, porque el módulo entero ya se despliega en el resource
//  group de la APIM compartida.
// ============================================================================

param apimServiceName string
param labPrefix string
param contentSafetyEndpoint string
param apimSubscriptionsConfig array = []

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimServiceName
}

// Nombre PROPIO de este lab (nunca "content-safety-backend" genérico) para
// que no choque con el backend de content-safety de ningún otro lab sobre el
// mismo APIM compartido. ⚠️ Debe coincidir EXACTO con el `backend-id` usado
// en policy.xml.
resource contentSafetyBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  name: '${labPrefix}-backend'
  parent: apim
  properties: {
    description: 'Content Safety Backend (${labPrefix})'
    url: contentSafetyEndpoint
    protocol: 'http'
    credentials: {
      #disable-next-line BCP037
      managedIdentity: {
        resource: 'https://cognitiveservices.azure.com'
      }
    }
  }
}

// Product + Subscriptions propias de este lab sobre el APIM compartido
// (antes las creaba `apimModule`; al quitarlo, este lab debe crear las
// suyas — con nombres prefijados — igual que indica el README del lab
// compartido: "todo lab sigue creando su propia API/product/subscription").
resource inferenceProduct 'Microsoft.ApiManagement/service/products@2024-06-01-preview' = {
  parent: apim
  name: '${labPrefix}-product'
  properties: {
    displayName: 'Content Safety Lab'
    description: 'Product para el lab de Content Safety sobre el APIM compartido'
    subscriptionRequired: true
    state: 'published'
  }
}

// RESUELTO: el link de este product con la API del lab (recurso
// `Microsoft.ApiManagement/service/products/apis`) ya NO va aquí — vive en
// `apim-product-api-link.bicep`, invocado desde main.bicep DESPUÉS de que
// exista tanto este product como la API (`inferenceAPIModule`, que ahora fija
// `inferenceAPIName: labPrefix` explícitamente). Ponerlo en este archivo
// hubiera creado una dependencia circular (este módulo se necesita ANTES que
// la API, para el backend; el link se necesita DESPUÉS de la API).

resource inferenceSubscriptions 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = [for sub in apimSubscriptionsConfig: {
  parent: apim
  name: '${labPrefix}-${sub.name}'
  properties: {
    displayName: sub.displayName
    scope: inferenceProduct.id
    state: 'active'
  }
}]

output apimPrincipalId string = apim.identity.principalId
output apimId string = apim.id
output apimGatewayUrl string = apim.properties.gatewayUrl
output apimSubscriptions array = [for (sub, i) in apimSubscriptionsConfig: {
  name: sub.name
  key: inferenceSubscriptions[i].listSecrets().primaryKey
}]
