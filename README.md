# Gateway-Labs
Aqui veran mis laboratorios usando Ai gateway en un entorno azure y usando api management
# Cualquier actualización de estos lobs ir a 
https://github.com/Azure-Samples/AI-Gateway/tree/main/labs
# Guía: un APIM compartido para varios labs de AI Gateway

Oct 1, 2026 · @Yackson

## La idea central

En lugar de que cada lab cree su propio Azure API Management (APIM), se crea **un solo APIM** una vez y todos los labs se conectan a él. Cada lab agrega dentro de ese APIM solo lo suyo: su API, sus backends, sus políticas, sus productos y sus suscripciones.

El motivo es práctico: un APIM de SKU Developer tarda de 30 a 45 minutos en aprovisionarse. Con un APIM por lab se espera y se paga una instancia distinta cada vez. Con uno compartido, la espera ocurre una sola vez.

Lo que se comparte es la base: el APIM, su identidad administrada, el Log Analytics Workspace y Application Insights. Lo que no se comparte es todo lo que define el comportamiento de cada lab. Esa frontera, y los errores que aparecen cuando se cruza, es lo que explica esta guía.

El entorno de referencia es el APIM `apim-shared-pdcibwky2f5ms`, en el grupo de recursos `rg-shared-apim-gateway-V2`.

## Arquitectura

[embedded content: APIM compartido · 4 labs y un lab nuevo, con monitoreo y modelos]

Todas las APIs entran por el mismo APIM. Los registros y la telemetría van a un Log Analytics y a un Application Insights únicos, y las llamadas salen hacia los modelos. El recuadro punteado es lo que debe crear cualquier lab nuevo: su propia API, con nombres propios.

## Qué es de todos y qué es de cada lab

La regla es simple: lo que proporciona la plataforma lo crea el lab base una vez; lo que define el comportamiento de un lab lo crea ese lab, con nombres que no se repitan.

| Recurso                                           | Quién lo crea                                                                | Compartido o propio                                         |
| ------------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Servicio APIM (`Microsoft.ApiManagement/service`) | Solo el lab base                                                             | Compartido                                                  |
| URL del gateway y suscripción base sobre `/apis ` | Lab base                                                                     | Compartido                                                  |
| Identidad administrada del APIM                   | Se crea con el APIM                                                          | Compartida: un permiso dado a ella vale para todos los labs |
| Log Analytics Workspace                           | Lab base                                                                     | Compartido                                                  |
| Application Insights y logger de Azure Monitor    | Lab base                                                                     | Compartido                                                  |
| API de inferencia y su ruta (`path`)              | Cada lab                                                                     | Propio, con nombre único                                    |
| Backends y backend pool                           | Cada lab                                                                     | Propio                                                      |
| Políticas (límites de tokens, CORS, reintentos)   | Cada lab, sobre su API                                                       | Propio                                                      |
| Productos y suscripciones                         | Cada lab                                                                     | Propio, con prefijo del lab                                 |
| Cuenta de AI Foundry y modelos                    | Depende del lab; el de Model Gateway la crea, el de FinOps usa una existente | Propio o prestado                                           |
| Dashboard o demo de cada lab                      | Cada lab                                                                     | Propio                                                      |

Regla de nombres: todo recurso que cree un lab lleva el nombre del lab como prefijo (por ejemplo `finops-framework-platinum` o `backend-pool-inference-api`). Es lo que evita las colisiones silenciosas descritas más abajo.

## Los labs del entorno

Cada lab es un cuaderno de Jupyter con un README, y la mayoría trae una demo visual. Todos registran su API dentro del mismo APIM.

| Lab                             | API y ruta                                                           | Qué demuestra                                                                                                             | Qué crea en el APIM compartido                                                          | Demo                                                                                                               |
| ------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Shared APIM Gateway** (base)  | Ninguna                                                              | Provisiona la plataforma una sola vez                                                                                     | APIM, Log Analytics, Application Insights; imprime outputs para los demás labs          | Ninguna                                                                                                            |
| **FinOps Framework**            | `finops-framework-inference-api`, ruta `finops-framework-inference ` | Control de costos de IA: límite de tokens por producto y alertas que desactivan suscripciones que pasan su cuota de costo | Productos `finops-framework-platinum`, `-gold`, `-silver` y sus suscripciones           | `finops-dashboard`: FastAPI en App Service que consulta el Log Analytics del lab y llama al APIM desde el servidor |
| **Backend Pool Load Balancing** | `backend-pool-inference-api`, ruta `backend-pool-inference `         | Balanceo con prioridades: el backend de prioridad 1 se agota antes de pasar a dos de prioridad 2                          | API, backends y pool                                                                    | `dashboard-pool-balancing`: React que llama a la API directo desde el navegador y muestra qué región responde      |
| **AI Foundry Model Gateway**    | API propia con nombre de lab                                         | APIM como Model Gateway de AI Foundry: seguridad, límites, caché y monitoreo sobre las llamadas del agente                | API, backends y política; el lab sí despliega su propio Foundry y el modelo GPT-4o-mini | Conexión de Foundry al gateway                                                                                     |
| **Gemini Models**               | Dos APIs: una nativa de Gemini y otra compatible con OpenAI          | Servir modelos de terceros por el mismo gateway                                                                           | Las dos APIs; requiere una clave de Gemini de Google                                    | Cuaderno de pruebas                                                                                                |

La idea común: el APIM es el único punto de entrada. Cada lab prueba una capacidad del gateway (costos, balanceo, modelos de terceros, seguridad) sin tener que montar su propia infraestructura de base.

## Cómo se implementó

**Requisitos:** Python 3.12 o superior, VS Code con la extensión de Jupyter, `uv`, Azure CLI con sesión iniciada, y una suscripción con rol Contributor más RBAC Administrator, o Owner.

1. **Crear la base una sola vez.** Se ejecuta el cuaderno del lab base. Crea Log Analytics, Application Insights y el APIM. Tarda de 30 a 45 minutos y la celda no muestra progreso; para verlo, usa otra terminal con `az deployment operation group list`.
2. **Anotar los outputs.** El cuaderno imprime lo que cada lab necesita: grupo de recursos, nombre del APIM, URL del gateway, `apimPrincipalId` (la identidad administrada), el logger y la suscripción base.
3. **Referenciar el APIM como existing.** En el Bicep de cada lab se declaran `sharedApimName` y `sharedApimResourceGroupName` y se usa el APIM existente mediante un módulo con `scope` explícito. El lab no vuelve a crear `Microsoft.ApiManagement/service`.
4. **Nombrar todo con el prefijo del lab.** API, ruta, backends, pools, productos, suscripciones y políticas.
5. **Crear lo propio del lab.** API, backends, pool, políticas, productos y suscripciones, dentro del APIM compartido.
6. **Dar permisos a la identidad del APIM.** Si el backend es una cuenta de Foundry u OpenAI que se llama sin clave, hay que asignar el rol a la identidad del APIM:

```
az role assignment create \
  --assignee
