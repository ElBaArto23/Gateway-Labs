# Backend Pool Dashboard — dónde va y cómo probarlo

## 1. Dónde ponerlo en tu repo

No metas esto dentro de `labs/backend-pool-load-balancing/` (esa carpeta es del lab en sí:
notebook + bicep + policy). Este dashboard es una herramienta aparte que **consume** ese lab
ya desplegado. Sugerido, al mismo nivel que `labs/`:

```
AI-gateway/
├── labs/
│   └── backend-pool-load-balancing/   <- el lab (ya lo tienes)
└── dashboards/
    └── backend-pool-dashboard/        <- esta carpeta completa
        ├── src/
        ├── server.js
        ├── package.json
        └── ...
```

## 2. Probarlo en local antes de subirlo (recomendado)

```bash
cd dashboards/backend-pool-dashboard
npm install
npm run dev
```

Abre `http://localhost:5173`, ve a **Configuración** y confirma que estos valores
(ya vienen precargados) coinciden con lo que te dio el notebook al desplegar:

- Gateway URL: `https://apim-shared-uzpevdi2gs3nc.azure-api.net`
- Path: `backend-pool-inference`
- Modelo: `gpt-5-mini`
- API key: el `shared subscription key` que imprime la celda 3️⃣ del notebook

Pega la key y dale **Ejecutar 20 llamadas**. Si ves datos en el log en vivo, ya funciona
la conexión. Si la primera llamada falla con un error de red/CORS, sigue el paso 3.

## 3. CORS — probablemente lo vas a necesitar

Un `fetch` desde el navegador a APIM solo funciona si la API tiene CORS habilitado para
el origin desde donde sirves el dashboard. Ya te dejé `policy.xml` actualizado con el
bloque `<cors>` agregado al inicio del `<inbound>`. Antes de volver a desplegar la política:

1. Reemplaza `https://TU-APP-SERVICE.azurewebsites.net` por la URL real que te va a dar
   el App Service (la sabrás después de crearlo, o resérvala con `az webapp create` antes).
2. Vuelve a aplicar esa política a la API `backend-pool-inference-api` en el APIM compartido
   (vía portal, `az apim api policy update`, o si tu bicep la administra, en el próximo
   `az deployment group create`).

Sin esto, verás el banner amarillo de "bloqueo CORS" en el dashboard.

## 4. Build y despliegue a App Service

```bash
npm run build          # genera dist/
```

El `server.js` incluido sirve `dist/` con Express y expone `/healthz`. Dos formas de subirlo:

**A) Zip deploy (rápido, para probar):**
```bash
az webapp up \
  --name <nombre-app-service> \
  --resource-group <tu-rg> \
  --runtime "NODE:20-lts" \
  --sku B1
```
Corriendo ese comando desde la carpeta del proyecto empaqueta y sube todo; Azure detecta
`package.json`, corre `npm install` y usa `npm start` (que apunta a `node server.js`).
**Importante:** corre `npm run build` localmente antes, o agrega un paso de build en el
pipeline — Oryx no sabe que necesitas `dist/` a menos que se lo digas.

**B) GitHub Actions (recomendado si ya versionas esto en el repo):**
Usa la acción estándar de "Deploy Node.js to Azure Web App", con un paso previo de
`npm ci && npm run build`, y que el `startup command` en App Service sea `node server.js`.

## 5. Qué probar una vez arriba, para confirmar que sirve

1. `https://<tu-app>.azurewebsites.net/healthz` → debe responder `ok`.
2. Abre la app, entra a Configuración, pega la key, ejecuta 5 llamadas.
3. Si todas caen en **East US** (priority 1), es correcto — el pool todavía tiene capacidad.
4. Para ver el failover real (que es el punto del lab), sube el número de llamadas a 20-30
   seguidas: la capacidad del modelo está fijada baja a propósito (`8` TPM) para que priority 1
   se sature y empieces a ver tráfico repartido en **Sweden Central / West US**.
