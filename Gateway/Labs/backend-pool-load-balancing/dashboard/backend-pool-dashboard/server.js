import express from "express";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const distPath = path.join(__dirname, "dist");

app.get("/healthz", (_req, res) => res.status(200).send("ok"));

app.use(express.static(distPath));

// SPA fallback: cualquier ruta no encontrada devuelve index.html
app.get("*", (_req, res) => {
  res.sendFile(path.join(distPath, "index.html"));
});

const port = process.env.PORT || 8080;
app.listen(port, () => {
  console.log(`Dashboard sirviendo en el puerto ${port}`);
});
