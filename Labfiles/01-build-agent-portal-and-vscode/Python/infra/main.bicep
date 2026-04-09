// Azure infrastructure for IT Support Chatbot
// Deploys: App Service Plan + Web App (Python) + Application Insights + Log Analytics

@description('Location for all resources')
param location string = resourceGroup().location

@description('Unique resource token (5 chars)')
param resourceToken string = uniqueString(resourceGroup().id)

@description('Azure AI Foundry project endpoint')
param projectEndpoint string

@description('Agent name configured in Foundry portal')
param agentName string = 'it-support-agent'

// --- Log Analytics Workspace ---
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law${resourceToken}'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// --- Application Insights ---
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'ai${resourceToken}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// --- App Service Plan (Linux, Python) ---
resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'asp${resourceToken}'
  location: location
  kind: 'linux'
  sku: {
    name: 'S1'
    tier: 'Standard'
  }
  properties: {
    reserved: true // required for Linux
  }
}

// --- Web App ---
resource webApp 'Microsoft.Web/sites@2024-04-01' = {
  name: 'chatbot${resourceToken}'
  location: location
  identity: {
    type: 'SystemAssigned' // Enables Managed Identity for DefaultAzureCredential
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn --bind=0.0.0.0 --timeout 600 web_chatbot:app'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      alwaysOn: true
      appSettings: [
        {
          name: 'PROJECT_ENDPOINT'
          value: projectEndpoint
        }
        {
          name: 'AGENT_NAME'
          value: agentName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true' // Installs pip packages on deploy
        }
      ]
    }
  }
}

// --- Outputs ---
output webAppName string = webApp.name
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
output principalId string = webApp.identity.principalId
