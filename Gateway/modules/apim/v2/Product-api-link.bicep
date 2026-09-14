/**
 * @module apim-product-api-link
 * @description Links an existing API to an existing Product on an API Management instance.
 * Generic and reusable: any lab attaching to a shared APIM can use this to grant its own
 * product (and therefore its own subscription) access to an API created by another module
 * or a different deployment step, without needing a resource symbol for that API.
 */

@description('The name of the API Management instance.')
param apiManagementName string

@description('The name (resource id, not display name) of the existing Product.')
param productName string

@description('The name (resource id, not display name) of the existing API to link into the product.')
param apiName string

resource apimService 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apiManagementName
}

resource product 'Microsoft.ApiManagement/service/products@2024-06-01-preview' existing = {
  name: productName
  parent: apimService
}

resource productApiLink 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = {
  name: apiName
  parent: product
}
