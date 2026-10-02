# Demo Content Safety auditada

La aplicación presenta el análisis real de Azure AI Content Safety para el mensaje y el historial, la decisión de la aplicación, el paso por APIM, la ejecución del modelo cuando puede confirmarse y el análisis de la respuesta antes de entregarla.

## Comportamiento

- Umbral 4 en Hate, Violence, Sexual y SelfHarm; escala 0–7. Los valores 0–3 no bloquean por categoría.
- Prompt Shield y coincidencias de blocklist1 bloquean independientemente del umbral.
- La evaluación previa de la aplicación es obligatoria. Un error o resultado incompleto detiene la solicitud con ERROR, nunca ALLOWED.
- La evaluación de salida es obligatoria. Se retiene la respuesta cuando un control la bloquea o no se puede completar el análisis.
- Si la aplicación bloquea la entrada, APIM y el modelo no son invocados. Si APIM devuelve un error sin identificar su fase, la ejecución del modelo se muestra como desconocida.
- Los resultados de la consulta directa a Content Safety no son las puntuaciones internas de llm-content-safety. No se afirma que un filtro del backend sea un bloqueo de Content Safety.
- ALLOWED significa que se superaron los controles disponibles, no que el modelo aceptó la intención del usuario. Una negativa redactada como texto puede proceder del propio modelo. Se identifica MODEL_REFUSAL cuando el backend entrega el campo estructurado refusal.
- Hasta 10.000 caracteres de contexto completo; los mensajes bloqueados o fallidos no se incorporan al historial enviado. Las respuestas anteriores aceptadas sí forman parte del contexto. Nueva conversación permite pruebas independientes.

## Demostración

1. Nueva conversación → Mensaje normal. Debe poder responder a una explicación sobre Content Safety.
2. Nueva conversación → Prompt Shield. Mostrar la detección real y comprobar que no se invoca el modelo cuando el control detecta ataque.
3. Nueva conversación → Lista personalizada. LAB-CS-DEMO-BLOCK es un marcador inocuo añadido explícitamente a blocklist1. Permite demostrar una coincidencia incluso con categorías en cero.
4. Explorar categorías. El nombre del botón describe la intención; Azure decide la categoría y severidad reales. Pedir contenido dañino no garantiza alcanzar el umbral. No modificar el umbral global para forzar un ejemplo aislado.
5. Revisar la sección de respuesta. Una respuesta bloqueada no se publica en el chat. Un error de análisis se diferencia de un bloqueo.

## Configuración y despliegue

Python 3.12, biblioteca estándar, Azure CLI. app.py es el servidor WSGI; el navegador no recibe las claves.

Variables: SAFETY_ENDPOINT, SAFETY_API_KEY, SAFETY_MODEL, SAFETY_API_VERSION, SAFETY_CONTENT_ENDPOINT, SAFETY_CONTENT_KEY y SAFETY_THRESHOLD=4.

El despliegue con deploy.py verifica que la política inference-api tenga las cuatro categorías, umbral uniforme, Prompt Shield, completions y blocklist1. Lee su umbral real y lo pasa a App Service. La plantilla crea o actualiza un plan B1 y App Service; conserva los demás recursos del laboratorio.

```powershell
python -m unittest discover -s . -v
python deploy.py --resource-group lab-content-safety-V2 --model gpt-5.6-luna --validate-only
```

Las pruebas unitarias usan respuestas simuladas y verifican el flujo; las pruebas reales y el estado del despliegue se documentan por separado en el informe de auditoría. La lista de prueba está creada en el recurso Azure existente; deploy.py no crea sus elementos en otro recurso.

## Relación con el laboratorio

El [policy.xml publicado](https://github.com/Azure-Samples/AI-Gateway/blob/main/labs/content-safety/policy.xml) usa umbral 4, Prompt Shield y blocklist1. Esta demo añade App Service, interfaz, evaluación previa y validación de completions. La política desplegada mantiene enforce-on-completions=true como ampliación. No es una copia literal del notebook.

[Referencia llm-content-safety](https://learn.microsoft.com/en-us/azure/api-management/llm-content-safety-policy).
