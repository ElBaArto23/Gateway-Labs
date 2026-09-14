// ------------------
//    PARAMETERS
// ------------------

@description('Name of the App Service Plan')
param planName string

@description('Name of the Web App (must be globally unique)')
param webAppName string

@description('Location for the App Service Plan and Web App')
param location string = resourceGroup().location

@description('App Service Plan SKU name, e.g. B1, S1, F1')
param skuName string = 'B1'

@description('Python runtime version, e.g. PYTHON|3.12')
param linuxFxVersion string = 'PYTHON|3.12'

@description('Gunicorn/startup command for the Linux Python Web App')
param startupCommand string = ''

@description('App Settings as an array of {name, value} objects. Pass secrets via @secure params from the caller.')
param appSettings array = []

@description('Application Insights connection string (optional)')
param appInsightsConnectionString string = ''

@description('Enable system-assigned managed identity on the Web App')
param enableManagedIdentity bool = true

// ------------------
//    VARIABLES
// ------------------

var baseAppSettings = [
  {
    name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
    value: 'true'
  }
  {
    name: 'WEBSITES_PORT'
    value: '8080'
  }
]

var appInsightsSettings = !empty(appInsightsConnectionString) ? [
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsightsConnectionString
  }
] : []

var allAppSettings = concat(baseAppSettings, appInsightsSettings, appSettings)

// ------------------
//    RESOURCES
// ------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: skuName
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  kind: 'app,linux'
  identity: enableManagedIdentity ? {
    type: 'SystemAssigned'
  } : null
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      appCommandLine: startupCommand
      appSettings: allAppSettings
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
    }
  }
}

// ------------------
//    OUTPUTS
// ------------------

output id string = webApp.id
output name string = webApp.name
output defaultHostName string = webApp.properties.defaultHostName
output principalId string = enableManagedIdentity ? webApp.identity.principalId : ''
