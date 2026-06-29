// Bicep equivalent of azuredeploy.json — for users who prefer Bicep.
// Compile to ARM with: az bicep build --file main.bicep

@description('Unique app name. Reachable at https://<appName>.azurewebsites.net')
param appName string

@description('Azure region')
param location string = resourceGroup().location

@allowed([ 'F1', 'B1', 'B2', 'B3', 'S1', 'P1V3' ])
@description('App Service Plan SKU. B1 recommended (~10 EUR/month, always-on).')
param sku string = 'B1'

@description('Container image to pull')
param containerImage string = 'ghcr.io/mnimtz/mysecureprint-server:latest'

@description('Public HTTPS URL the server reports. Leave empty to default to azurewebsites.net.')
param publicUrl string = ''

@description('Container timezone (IANA)')
param tz string = 'Europe/Berlin'

@allowed([ 'debug', 'info', 'warning', 'error' ])
param logLevel string = 'info'

var planName = '${appName}-plan'
var storageName = 'mysprt${uniqueString(resourceGroup().id)}'
var fileShareName = 'printix-data'
var effectivePublicUrl = empty(publicUrl) ? 'https://${appName}.azurewebsites.net' : publicUrl

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  name: '${storage.name}/default/${fileShareName}'
  properties: { shareQuota: 5 }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: { name: sku }
  kind: 'linux'
  properties: { reserved: true }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux,container'
  dependsOn: [ share ]
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${containerImage}'
      alwaysOn: sku != 'F1'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'WEBSITES_PORT', value: '8080' }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
        { name: 'DOCKER_REGISTRY_SERVER_URL', value: 'https://ghcr.io' }
        { name: 'MCP_PUBLIC_URL', value: effectivePublicUrl }
        { name: 'MCP_LOG_LEVEL', value: logLevel }
        { name: 'WEB_PORT', value: '8080' }
        { name: 'TZ', value: tz }
      ]
      azureStorageAccounts: {
        data: {
          type: 'AzureFiles'
          accountName: storageName
          shareName: fileShareName
          mountPath: '/data'
          accessKey: listKeys(storage.id, '2023-05-01').keys[0].value
        }
      }
    }
  }
}

output appUrl string = 'https://${appName}.azurewebsites.net'
output storageAccount string = storageName
