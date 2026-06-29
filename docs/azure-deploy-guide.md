# Azure Deployment — manual `az` CLI

If you prefer the command line over the "Deploy to Azure" button.

## Prerequisites

- An Azure subscription with at least Contributor rights on a resource group
- `az` CLI installed and logged in (`az login`)

## One-shot deployment

```bash
RG=mysecureprint-rg
LOC=westeurope
APP=mysecureprint-yourname    # must be globally unique

az group create --name $RG --location $LOC

az deployment group create \
  --resource-group $RG \
  --template-uri https://raw.githubusercontent.com/mnimtz/mysecureprint-server/main/deploy/azure/azuredeploy.json \
  --parameters appName=$APP sku=B1
```

After ~5 minutes the deployment returns the App Service URL.

## Updating to a new container image

```bash
az webapp restart --resource-group $RG --name $APP
```

(The image is pulled fresh on every container restart.)

## Tier selection guidance

| SKU | Monthly EUR | RAM | Always-on | Use case |
|---|---|---|---|---|
| F1 | 0 | 1 GB | No | Apple-Review demo only |
| B1 | ~10 | 1.75 GB | Yes | Default — handles print conversion comfortably |
| B2 | ~20 | 3.5 GB | Yes | Frequent large Office-doc conversion |
| S1 | ~50 | 1.75 GB + scaling | Yes | Higher SLA, staging slots |

## Custom domain

```bash
az webapp config hostname add --webapp-name $APP --resource-group $RG --hostname print.example.com
# DNS: CNAME print -> $APP.azurewebsites.net
az webapp config ssl create --resource-group $RG --name $APP --hostname print.example.com
az webapp config ssl bind --resource-group $RG --name $APP --hostname print.example.com --certificate-type SNI
```

Managed certs are free on B1+.

## Tear-down

```bash
az group delete --name $RG --yes --no-wait
```
