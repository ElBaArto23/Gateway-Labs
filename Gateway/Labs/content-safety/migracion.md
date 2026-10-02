# Migrar `content-safety` al APIM compartido — notas y checklist

Trabajé solo con los 4 archivos que subiste (`README.md` del lab compartido, `content-safety.ipynb`, `main.bicep`, `policy.xml`). No tengo acceso a los módulos reales del repo (`modules/apim/v2/apim.bicep`, `modules/apim/v2/inference-api.bicep`, `modules/cognitive-services/v3/foundry.bicep`, `shared/utils.py`), así que hay 2 puntos que **debes verificar tú antes de desplegar** — están marcados con `⚠️ VERIFICAR` directamente en `main.bicep`. El resto son cambios completos y listos para usar.

## Qué cambié y por qué

### 1. Ya no crea su propia APIM (`main.bicep`)
Se eliminó el `module apimModule ...` que desplegaba una instancia de APIM dedicada a este lab. En su lugar:

```bicep
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: sharedApimServiceName
  scope: resourceGroup(sharedApimResourceGroupName)
}
```

Esto referencia la APIM del lab `shared-apim-gateway`, que puede vivir en **otro** resource group — `scope: resourceGroup(...)` permite eso dentro de la misma suscripción, y los recursos hijos (`contentSafetyBackend`, `inferenceProduct`, `inferenceSubscriptions`, todos con `parent: apim`) heredan ese scope automáticamente, sin que tengas que mover este lab entero al resource group compartido.

Se agregaron 2 parámetros nuevos que debes llenar con los outputs del lab compartido:
- `sharedApimResourceGroupName` ← output `resourceGroupName`
- `sharedApimServiceName` ← output `apimServiceName`

### 2. La API queda con el nombre propio del lab (`labPrefix`), totalmente independiente
El README del lab compartido pide que cada lab nombre sus recursos de forma única para no chocar con otros labs sobre la misma APIM. `labPrefix` ya **no tiene default** — es obligatorio, y en el notebook se calcula solo como `lab_prefix = deployment_name` (el nombre de la carpeta del lab, es decir `content-safety`). Con eso:
- La API queda con **path propio**: `/content-safety/models` (antes era un genérico `/inference/...` que, si dos labs lo usaban tal cual, sí hubiera chocado).
- El backend de Content Safety: `content-safety-backend`
- El product: `content-safety-product`
- Las subscriptions: `content-safety-subscription1`, etc.

Así esta API no comparte nombre con la de ningún otro lab sobre la misma APIM compartida — funciona de forma independiente, sin "molestar" ni pisar la de nadie más.

⚠️ **`policy.xml` tiene el `backend-id="content-safety-backend"` hard-codeado** (no se puede parametrizar porque `loadTextContent()` no hace templating). Si alguna vez cambias `labPrefix` a otro valor en el bicep, edita también esa línea en `policy.xml` para que coincidan.

### 3. Conectar el workspace/insight compartido
- Application Insights: si el lab `shared-apim-gateway` ya configuró un **logger a nivel de servicio de APIM** (probable, dado que expone `apimLoggerId`), cualquier API nueva registrada en esa misma instancia queda logueada ahí automáticamente — no necesitas hacer nada extra para eso.
- Log Analytics para los recursos propios de este lab (Content Safety): agregué un `Microsoft.Insights/diagnosticSettings` sobre `contentSafetyResource` que manda sus logs al workspace compartido, controlado por el nuevo parámetro `sharedLogAnalyticsWorkspaceId`. Si el lab compartido todavía no expone el resource ID del Log Analytics Workspace como output, añádelo ahí (es el mismo recurso que ya crea para Application Insights) y pégalo en la celda `0️⃣` del notebook.

### 4. Los 2 `⚠️ VERIFICAR` que quedan pendientes
1. **`inferenceAPIModule`** ahora se llama con `scope: resourceGroup(sharedApimResourceGroupName)` y un parámetro nuevo `apimServiceName: sharedApimServiceName`. Necesitas confirmar que `modules/apim/v2/inference-api.bicep` acepta ese parámetro — si no lo tiene (porque antes resolvía el nombre de la APIM recalculando el mismo `uniqueString` internamente, lo cual deja de funcionar al cruzar de resource group), hay que agregárselo al módulo.
2. **Vincular el Product con la API** (`Microsoft.ApiManagement/service/products/apis`): dejé el bloque comentado en `main.bicep` porque necesito el `name:` real que usa `inference-api.bicep` para el recurso `Microsoft.ApiManagement/service/apis`. Ábrelo, cópiame ese nombre (o agrégale `output apiName string = api.name` si no lo tiene) y completo esa parte en un momento.

### 5. Los "controles de seguridad" — bloqueo con explicación elegante (`policy.xml`)
La policy sigue igual en `<inbound>` (Content Safety con `shield-prompt` + `enforce-on-completions`, umbrales en severidad 4 para SelfHarm/Hate/Violence/Sexual — igual que antes). Lo nuevo está en `<on-error>`:

- Si el error viene de `llm-content-safety`, la respuesta 403 ya no es el error crudo de Azure — devuelve un JSON con: categoría que disparó el bloqueo, severidad detectada (parseadas del mensaje de error con una expresión regular), la escala completa de severidad, los umbrales configurados, y el mensaje original de Azure como respaldo.
- **Aplica igual sin importar cuál de los 2 modelos respondió** — `enforce-on-completions="true"` corre para cualquier backend, y el bloque `on-error` no distingue por modelo.

⚠️ El texto exacto de `context.LastError.Message` para `llm-content-safety` no tiene un esquema 100% documentado campo-por-campo por Microsoft; el regex que extrae categoría/severidad es un best-effort. **Antes de confiar en esto en producción**: dispara un bloqueo real, mira el `context.LastError.Message` con la [tracing tool](../../tools/tracing.ipynb) del repo, y ajusta el regex en `policy.xml` si el texto real no calza exactamente.

### 6. Notebook — bug real que encontré y arreglé
La celda de pruebas original tenía 2 problemas:
1. `client.complete(..., max_completion_tokens=2048, ...)` falla porque ese parámetro debe ir dentro de `model_extras={...}`, no como argumento directo — por eso veías el error `Session.request() got an unexpected keyword argument 'max_completion_tokens'` en tu output.
2. El chat interactivo **solo usaba `models_config[0]['name']`** (`gpt-5.6-luna`) — `Phi-4` nunca se probaba. Reescribí la celda de pruebas para llamar a los 2 modelos con el mismo prompt bloqueado, y el chat interactivo ahora deja cambiar de modelo en cualquier momento con `modelo:gpt-5.6-luna` / `modelo:Phi-4`, imprimiendo la explicación elegante del bloqueo (categoría, severidad, umbrales) para cualquiera de los 2.

## ¿Esto daña lo que ya hay desplegado en la APIM compartida?

No — con una salvedad. Este lab **crea recursos nuevos** (API, backend, product, subscriptions) dentro de la APIM compartida; nunca vuelve a crear ni a tocar la APIM en sí (se referencia como `existing`, es decir, de solo lectura para este template). Los deployments de `az deployment group create` corren en modo **incremental** por default: Azure Resource Manager solo agrega o actualiza lo que está declarado en la plantilla — jamás borra recursos que ya existan y que no aparezcan ahí. Los backends/products/APIs de otros labs quedan intactos.

El único riesgo real es una **colisión de nombres**: si tu `lab_prefix` coincide con el que ya usa otro lab, el `PUT` de este deployment actualizaría/sobreescribiría ESE recurso en vez de crear uno separado. Por eso agregué una celda de **pre-flight** en el notebook (antes de la celda de deployment) que revisa, vía `az resource show`, si ya existe un backend/product/subscription con los nombres que este lab va a usar, y te avisa ANTES de desplegar si hay algo que revisar. Esa celda no puede validar todavía el nombre del recurso `api` (uno de los 2 puntos `⚠️ VERIFICAR`), así que si tienes dudas sobre esa parte, revisa manualmente con `az apim api list` antes de desplegar.

## Checklist antes de desplegar

- [ ] Correr (una sola vez) el lab `shared-apim-gateway` si todavía no está desplegado.
- [ ] Copiar sus outputs `resourceGroupName` y `apimServiceName` a la celda `0️⃣` de `content-safety.ipynb`.
- [ ] (Opcional) Exponer/copiar el resource ID del Log Analytics Workspace compartido para `shared_log_analytics_workspace_id`.
- [ ] Abrir `modules/apim/v2/inference-api.bicep` y confirmar/agregar el parámetro `apimServiceName` (punto 4.1 arriba).
- [ ] Confirmar el nombre real del recurso `apis` dentro de ese módulo y completar el link `products/apis` que dejé comentado (punto 4.2).
- [ ] Verificar que `labPrefix` no choque con el de otro lab que ya esté corriendo sobre la misma APIM compartida.
- [ ] Desplegar y probar con la celda de "🧪 Probar el Content Safety — con LOS 2 MODELOS".
- [ ] Disparar un bloqueo real y revisar `context.LastError.Message` con la tracing tool para afinar el regex de `policy.xml` si hace falta.