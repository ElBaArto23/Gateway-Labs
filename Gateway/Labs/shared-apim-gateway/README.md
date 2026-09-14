---
name: Shared APIM Gateway
categories:
  - Platform Capabilities
services:
  - Azure API Management
  - Azure AI Foundry
  - Azure OpenAI
shortDescription: Provisiona una única instancia de APIM compartida como base para todos los labs de AI-Gateway.
detailedDescription: Este lab crea el único Azure API Management (junto con Log Analytics y Application Insights) que los demás labs reutilizan como `existing`, en vez de que cada lab aprovisione su propia instancia de APIM.
tags: []
authors:
  - Yackson josue cure rodriguez 
---

# APIM Compartido — Provisión única

## [Shared APIM Gateway lab](deploy-shared-apim.ipynb)

Este notebook crea el **único** Azure API Management que reutilizan los demás labs (por ejemplo, Model Gateway, Foundry IQ, Access Controlling, backend-pool-load-balancing, etc.).

**No crea Foundry, ni modelos, ni APIs de inferencia** — solo la instancia de APIM, Log Analytics y Application Insights. Cada lab, al correrse, registra después su propia API dentro de este APIM (con sus propios backends, policies, products y subscriptions), referenciándolo como `existing` en vez de desplegar un servicio de APIM nuevo.

### ¿Por qué existe este lab?

Por defecto, cada lab del repo `Azure-Samples/AI-Gateway` crea su propia instancia de APIM. Con el SKU Developer, cada una tarda 30-45 minutos en aprovisionar, y probar varios labs en paralelo o en secuencia significa pagar y esperar por una APIM distinta cada vez. Este lab consolida esa base en un solo despliegue, hecho una sola vez.

## SKU de APIM: Developer para pruebas, ¿y para producción?

**Para las pruebas de estos labs, el SKU queda fijo en `Developer`** (ver `main.bicep`, `params.json` y la celda de configuración del notebook). Es el más económico de los SKUs no-serverless, con capacidad fija en 1 unidad y **sin SLA** — perfecto para minimizar costos mientras se prueban labs, pero no apto para producción.

Cuando este APIM compartido deje de ser un entorno de pruebas y pase a soportar tráfico real, el SKU correcto depende de los requisitos de red y disponibilidad. Resumen de las opciones de producción (según la documentación vigente de Microsoft Learn):

| Tier | SLA | Networking | Escalado | Cuándo usarlo |
|---|---|---|---|---|
| **Basic v2** | Sí | Sin aislamiento de red | Hasta 10 unidades | Producción pequeña, sin necesidad de llegar a backends con endpoints privados |
| **Standard v2** | Sí | VNET integration (outbound) + private endpoints (inbound); mantiene IP pública | Hasta 10 unidades | Producción "lista para negocio" que necesita llegar a backends de OpenAI/AI Foundry con endpoints privados, sin exigir aislamiento total de la red |
| **Premium v2** | Sí | VNET injection completa (sin IP pública expuesta), corre en App Service Environment dedicado | Hasta 30 unidades, zonas de disponibilidad | Cargas empresariales críticas, alto volumen, aislamiento de red total y/o multi-región |

**Recomendación:** para un AI Gateway de producción que expone Azure OpenAI / AI Foundry a consumidores internos, **Standard v2** suele ser el punto de partida más razonable — trae SLA, autoscaling y la capacidad de conectarse a backends con endpoints privados sin el costo y la complejidad operativa de Premium. Sube a **Premium v2** solo si de verdad necesitas aislamiento de red completo (sin IP pública), zonas de disponibilidad, multi-región, o el volumen de tráfico lo justifica.

⚠️ **Nota importante sobre el módulo Bicep actual:** el módulo `modules/apim/v3/apim.bicep` que usa este lab solo acepta estos valores en `apimSku`:

```
Consumption | Developer | Basic | Basicv2 | Standard | Standardv2 | Premium
```

Es decir, hoy soporta el **Premium clásico** (v1), no **Premium v2** — son ofertas distintas (Premium v2 corre sobre App Service Environment dedicado). Si cuando llegue el momento de ir a producción se quiere usar Premium v2, hay que confirmar que el módulo lo soporte o actualizarlo; no asumir que `Premium` en este archivo ya es la variante v2. Antes de fijar el SKU de producción definitivo, vuelve a verificar el decorador `@allowed` del módulo real y la disponibilidad vigente de cada tier en tu región, ya que Azure ajusta este catálogo con cierta frecuencia.

## Qué crea este lab

1. **Log Analytics Workspace** — centralizado, compartido por todos los labs que corran sobre esta APIM.
2. **Application Insights** — también centralizado, conectado al Log Analytics de arriba.
3. **Azure API Management (único)** — la instancia compartida. Este es el único módulo del repo que debe crear el recurso `Microsoft.ApiManagement/service`; ningún lab adaptado a este esquema debe volver a llamarlo.

### Outputs

Al terminar el despliegue, el notebook imprime los valores que cada lab necesita para referenciar esta APIM como `existing`:

- `resourceGroupName` — resource group donde vive la APIM compartida
- `apimServiceName` — nombre de la instancia de APIM
- `apimResourceGatewayURL` — URL del gateway
- `apimPrincipalId` — identity de la APIM (para asignar roles RBAC a backends de Foundry/OpenAI)
- `apimLoggerId` — logger de Azure Monitor ya configurado
- `apimSubscriptions` — suscripción(es) base creada(s) sobre `/apis` (aplica a cualquier API que los labs registren después)

## Prerequisitos

- [Python 3.12 o superior](https://www.python.org/)
- [VS Code](https://code.visualstudio.com/) con la [extensión de Jupyter](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter)
- [uv](https://docs.astral.sh/uv/) — ejecuta `uv sync` desde la raíz del repo para instalar dependencias
- Una [suscripción de Azure](https://azure.microsoft.com/free/) con rol [Contributor](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles/privileged#contributor) + [RBAC Administrator](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles/privileged#role-based-access-control-administrator), u [Owner](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles/privileged#owner)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) instalado y con sesión iniciada (`az login`)
- [Bicep CLI](https://learn.microsoft.com/azure/azure-resource-manager/bicep/install)

## 🚀 Get started

Abre [deploy-shared-apim.ipynb](deploy-shared-apim.ipynb) y sigue los pasos.

**Este notebook se corre UNA sola vez**, antes de correr cualquiera de los labs que dependan de esta APIM compartida. Si lo vuelves a correr para recrear el APIM (por ejemplo, borrando y desplegando de nuevo el resource group), vas a romper todos los labs que ya la referencien, hasta que actualices sus parámetros con los nuevos outputs.

⏱️ Igual que con cualquier otra instancia de APIM (Developer o no), el aprovisionamiento toma 30-45 minutos y la celda del notebook no muestra progreso mientras corre. Verifica el avance desde otra terminal con `az deployment operation group list` o desde el portal — no asumas que quedó atascado solo porque no imprime nada.

**Observación empírica:** en pruebas repetidas, desplegar en `Developer` tomó consistentemente más tiempo que en `Basic`/`Basic v2`. Esto **no está documentado oficialmente** por Microsoft como una diferencia de diseño entre tiers — lo único que Microsoft ha reconocido públicamente es que Developer y Basic comparten el mismo pool de capacidad "económica" y en el pasado ambos sufrieron problemas puntuales de aprovisionamiento lento (throttling/colas regionales), no una jerarquía fija Developer > Basic. Si el tiempo de espera es crítico durante una sesión intensa de pruebas, considera desplegar temporalmente en `Basic v2` (SLA incluido, costo por hora no muy distinto) y volver a `Developer` para dejarlo corriendo sin uso activo.

### Al adaptar cada lab para usar esta APIM compartida

1. El lab **no debe** crear su propio recurso `Microsoft.ApiManagement/service` (Bicep) ni `azurerm_api_management` (Terraform) — debe referenciar la APIM existente usando los outputs de este lab.
2. El lab **sí debe** seguir creando su propia API, backends, backend pool, policies, products y subscriptions dentro de esta APIM compartida.
3. Todos los nombres de recursos que cree el lab (API, backends, pools, products, subscriptions, policies) deben ser **únicos por lab**, para evitar colisiones si varios labs conviven sobre la misma instancia (por ejemplo, prefijando con el nombre del lab: `bp-lb-bicep-*`, `bp-lb-tf-*`, etc.).

## 🗑️ Clean up resources

⚠️ Solo elimina estos recursos si ya no vas a correr ningún lab que dependa de esta APIM compartida — borrarla rompe a todos los labs que la referencien, hasta que se despliegue de nuevo y se actualicen sus referencias.

```bash
az group delete --name rg-shared-apim-gateway --yes --no-wait
```