// ============================================================================
//  content-safety / apim-product-api-link.bicep
// ============================================================================
//  Vincula el Product de este lab (creado en apim-shared-resources.bicep) con
//  la API de este lab (creada en inference-api.bicep vía inferenceAPIModule).
//  Va en su propio módulo por la misma razón que apim-shared-resources.bicep:
//  Bicep no permite declarar un recurso hijo de un `existing` que cruza de
//  resource group directamente en un archivo que se despliega en otro scope
//  (BCP165) — y este recurso necesita, a la vez, que el Product Y la API ya
//  existan, así que se separó de ambos módulos para poder depender de los 2
//  sin crear una dependencia circular.
// ============================================================================

param apimServiceName string
param productName string
param apiName string

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimServiceName
}

resource product 'Microsoft.ApiManagement/service/products@2024-06-01-preview' existing = {
  parent: apim
  name: productName
}

resource api 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' existing = {
  parent: apim
  name: apiName
}

resource productApiLink 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = {
  parent: product
  name: api.name
}
