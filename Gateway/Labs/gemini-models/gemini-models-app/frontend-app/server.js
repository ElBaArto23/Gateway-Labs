// Gemini Models — AI Gateway frontend backend.
//
// Keeps the 3 Azure APIM subscription keys server-side (App Settings) and
// proxies chat requests to the shared APIM gateway. The browser never sees
// any key — it only talks to this server's own /api/* endpoints, same
// origin, so no CORS policy is needed for this app (the earlier CORS change
// on the shared APIM's gemini-models APIs is only needed if you still want
// the old browser-direct version to keep working; this backend doesn't
// depend on it).
//
// Required App Settings (see app-service.bicep):
//   GATEWAY_URL, OPENAI_API_PATH, API_VERSION, MODEL_NAME, SYSTEM_PROMPT
//   SUBSCRIPTION_1_KEY, SUBSCRIPTION_2_KEY, SUBSCRIPTION_3_KEY
//     — these are the AZURE APIM subscription keys (the ones printed by
//     geminimodels.ipynb's "3️⃣ Get the deployment outputs" cell), NOT the
//     Google Gemini API keys. The Google keys stay where they already are,
//     inside APIM's Named Values — this server never touches them directly.

const express = require('express');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

const GATEWAY_URL = (process.env.GATEWAY_URL || 'https://apim-shared-pdcibwky2f5ms.azure-api.net').replace(/\/$/, '');
const OPENAI_API_PATH = process.env.OPENAI_API_PATH || 'gemini-models-openaicompatible';
const API_VERSION = process.env.API_VERSION || 'v1beta';
const DEFAULT_MODEL = process.env.MODEL_NAME || 'gemini-3-flash-preview';
const DEFAULT_SYSTEM_PROMPT = process.env.SYSTEM_PROMPT || 'Eres un asistente útil y profesional.';

const SUBSCRIPTION_KEYS = {
  '1': process.env.SUBSCRIPTION_1_KEY || '',
  '2': process.env.SUBSCRIPTION_2_KEY || '',
  '3': process.env.SUBSCRIPTION_3_KEY || '',
};

// Non-secret info the frontend needs to render itself (which subscriptions
// are actually usable, and the defaults to show in the settings form).
app.get('/api/config', (req, res) => {
  res.json({
    model: DEFAULT_MODEL,
    systemPrompt: DEFAULT_SYSTEM_PROMPT,
    subscriptionsConfigured: {
      '1': Boolean(SUBSCRIPTION_KEYS['1']),
      '2': Boolean(SUBSCRIPTION_KEYS['2']),
      '3': Boolean(SUBSCRIPTION_KEYS['3']),
    },
  });
});

app.post('/api/chat', async (req, res) => {
  const { subscription, message, model, systemPrompt } = req.body || {};

  if (!['1', '2', '3'].includes(subscription)) {
    return res.status(400).json({ error: 'subscription debe ser "1", "2" o "3"' });
  }
  if (!message || typeof message !== 'string') {
    return res.status(400).json({ error: 'message es requerido' });
  }
  const key = SUBSCRIPTION_KEYS[subscription];
  if (!key) {
    return res.status(500).json({
      error: `No hay key configurada en el servidor para Subscription ${subscription} — falta el App Setting SUBSCRIPTION_${subscription}_KEY.`,
    });
  }

  const url = `${GATEWAY_URL}/${OPENAI_API_PATH}/${API_VERSION}/openai/chat/completions`;
  const messages = [
    { role: 'system', content: systemPrompt || DEFAULT_SYSTEM_PROMPT },
    { role: 'user', content: message },
  ];

  const started = Date.now();
  try {
    const upstream = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${key}`,
        'x-user-id': 'app-service-frontend',
      },
      body: JSON.stringify({ model: model || DEFAULT_MODEL, messages, stream: false }),
    });

    const elapsedMs = Date.now() - started;
    const keySource = upstream.headers.get('x-debug-key-source');
    const raw = await upstream.text();
    let data = null;
    try { data = JSON.parse(raw); } catch (_) { /* respuesta no-JSON */ }

    if (!upstream.ok) {
      return res.status(upstream.status).json({
        error: (data && data.error && data.error.message) || raw.slice(0, 500),
        keySource,
        elapsedMs,
      });
    }

    const content = data && data.choices && data.choices[0] && data.choices[0].message
      ? data.choices[0].message.content
      : raw;
    const usage = (data && data.usage) || null;

    res.json({ content, usage, keySource, elapsedMs });
  } catch (err) {
    res.status(502).json({ error: `No se pudo contactar el gateway: ${err.message}` });
  }
});

app.get('/api/health', (req, res) => res.json({ ok: true }));

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Gemini Models frontend escuchando en puerto ${port}`));
