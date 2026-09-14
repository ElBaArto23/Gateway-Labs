---
name: AI Foundry Model Gateway
architectureDiagram: images/foundry-model-gateway.gif
categories:
  - Platform Capabilities
  - Models Usage
  - Gateway Pattern
services:
  - Azure AI Foundry
  - Azure API Management
shortDescription: Configure Azure API Management as a Model Gateway for Azure AI Foundry Agents Service.
detailedDescription: Learn how to deploy Azure AI Foundry and configure Azure API Management (APIM) as a Model Gateway. This lab demonstrates the complete setup including deploying AI Services, creating model deployments, establishing the gateway connection, and leveraging APIM's enterprise features like rate limiting, caching, monitoring, and security policies for AI model inference requests through Foundry Agents Service.
tags:
  - Model Gateway
  - AI Foundry
  - APIM Gateway
  - Enterprise AI
authors:
  - nourshaker-msft
---

# APIM ❤️ Microsoft Foundry

## AI Foundry with APIM Model Gateway lab

![flow](../../images/foundry-model-gateway.gif)

This lab demonstrates how to configure **Azure API Management (APIM)** as a **Model Gateway** for **Azure AI Foundry**. By establishing this connection, you can leverage APIM's enterprise-grade capabilities including:

- 🔒 **Security & Authentication** - API keys, OAuth, JWT validation
- 📊 **Monitoring & Analytics** - Detailed metrics, logs, and distributed tracing  
- ⚡ **Performance** - Semantic caching, response compression
- 🛡️ **Governance** - Rate limiting, throttling, quotas
- 🔄 **Reliability** - Load balancing, circuit breakers, retries

### Architecture

The Model Gateway pattern enables Foundry Agents Service to route all model inference requests through APIM:

```
Foundry Agent → APIM Gateway (Model Gateway) → AI Services → OpenAI Models
                      ↓
              Policies, Monitoring,
              Rate Limiting, Caching
```

### What you'll deploy

- **Azure AI Foundry** - Hub and project with Azure AI Services
- **Shared APIM instance** - this lab connects to a pre-existing, shared API Management instance instead of deploying its own dedicated one (see [Shared vs. dedicated APIM](#-shared-vs-dedicated-apim) below)
- **OpenAI Model Deployments** - GPT-4o-mini model
- **Model Gateway Connection** - Connection from Foundry to APIM
- **Monitoring Stack** - Application Insights and Log Analytics

### 🔀 Shared vs. dedicated APIM

As of August 2026, this lab was migrated to consume a **shared** APIM instance (`apim-shared-pdcibwky2f5ms`, resource group `rg-shared-apim-gateway-V2`) rather than deploying a dedicated instance of its own on every run. `main.bicep` references it as `existing` via the `sharedApimName` / `sharedApimResourceGroupName` parameters (set in the notebook's initialization cell), and registers its own namespaced API, backends and policy inside that shared instance — it no longer provisions an `Microsoft.ApiManagement/service` resource directly. The AI Foundry account and its model deployments are still deployed and owned by this lab (see `modules/cognitive-services/v3/foundry.bicep`); only the APIM layer is shared.

**SKU/tier guidance:**

- **Developer** — no SLA, lower throughput, but the cheapest option. Fine for this lab, demos, and other non-production evaluation scenarios. The shared instance backing this lab today may or may not be on Developer — check with `az apim show --name <name> -g <rg> --query sku` if it matters for your scenario.
- **Production** — do **not** use Developer SKU for production traffic. Use **Standard**, **Premium**, or the newer **v2 SKU family** (`StandardV2` / `PremiumV2`), which carry an SLA, support autoscaling and higher throughput, and are what Microsoft recommends for production AI Gateway deployments. See [Azure API Management pricing tiers](https://learn.microsoft.com/azure/api-management/api-management-features) for the full comparison before choosing one for a production rollout.

### Key Features

✅ **Model Gateway Connection** - Foundry uses APIM as the inference gateway  
✅ **Centralized Management** - Single control plane for all AI traffic  
✅ **Enterprise Policies** - Apply security, rate limiting, caching at the gateway  
✅ **Cost Optimization** - Track and control AI service consumption  
✅ **Multi-Backend Support** - Route to multiple AI Services with load balancing  

### Prerequisites

- [Python 3.12 or later version](https://www.python.org/) installed
- [VS Code](https://code.visualstudio.com/) installed with the [Jupyter notebook extension](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter) enabled
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) installed
- [An Azure Subscription](https://azure.microsoft.com/free/) with Contributor and RBAC Administrator (or Owner) permissions
- [Sign in to Azure with Azure CLI](https://learn.microsoft.com/cli/azure/authenticate-azure-cli-interactively)

### 🚀 Get started

Proceed by opening the [Jupyter notebook](foundry-ai-gateway.ipynb), and follow the steps provided.

### 📖 Learn more

- [Azure AI Foundry Documentation](https://learn.microsoft.com/azure/ai-studio/)
- [APIM as AI Gateway](https://learn.microsoft.com/azure/api-management/api-management-using-with-internal-vnet)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Foundry Samples - Model Gateway Setup](https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep/01-connections/model-gateway)

### 🗑️ Clean up resources

When you're finished with the lab, you should remove all your deployed resources from Azure to avoid extra charges and keep your Azure subscription uncluttered.
Use the [clean-up-resources notebook](clean-up-resources.ipynb) for that.