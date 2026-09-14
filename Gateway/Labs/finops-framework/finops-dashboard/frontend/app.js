// ---------------------------------------------------------------------------
// FinOps Framework Dashboard - Frontend
// Consume la API FastAPI (mismo origen, rutas relativas /api/...)
// Si el backend no responde, cae automáticamente a datos de ejemplo
// (modo demo) para poder revisar la interfaz sin desplegar nada.
// ---------------------------------------------------------------------------

const API_BASE = "";

const rangeSelect = document.getElementById("range-select");
const refreshBtn = document.getElementById("refresh-btn");
const retryBtn = document.getElementById("retry-btn");
const connectionLabel = document.getElementById("connection-label");
const statusDot = document.querySelector(".status-dot");
const demoBanner = document.getElementById("demo-banner");
const viewTitle = document.getElementById("view-title");
const viewSubtitle = document.getElementById("view-subtitle");

let DEMO_MODE = false;
// El chat tiene su propio flag, independiente de DEMO_MODE (que solo lo usan
// los gráficos). Antes compartían la misma variable, así que un error de
// Chart.js apagaba el chat aunque el gateway estuviera perfecto.
let CHAT_DEMO_MODE = false;
let costChart, donutChart, timeseriesChart, productsCostChart, productsTokensChart;

const VIEW_META = {
  overview: { title: "Panel Operativo", subtitle: "workspace · finops-framework" },
  subscriptions: { title: "Unidades de Negocio", subtitle: "detalle por unidad de negocio" },
  products: { title: "Productos & Cuotas", subtitle: "platinum / gold / silver" },
  alerts: { title: "Alertas & Suspensiones", subtitle: "unidades de negocio sobre cuota" },
  chat: { title: "Probar Gateway", subtitle: "inference endpoint · APIM compartido" },
};

// ---------------------------------------------------------------------------
// Navegación entre vistas del sidebar
// ---------------------------------------------------------------------------
document.querySelectorAll(".nav-item").forEach((item) => {
  item.addEventListener("click", () => {
    const view = item.dataset.view;
    document.querySelectorAll(".nav-item").forEach((i) => i.classList.remove("active"));
    item.classList.add("active");
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    document.getElementById(`view-${view}`).classList.add("active");
    viewTitle.textContent = VIEW_META[view].title;
    viewSubtitle.textContent = VIEW_META[view].subtitle;
    if (view === "chat") loadChatConfig();
  });
});

// ---------------------------------------------------------------------------
// Fetch con fallback a modo demo
// ---------------------------------------------------------------------------
async function fetchJSON(path, extraParams = {}) {
  if (DEMO_MODE) return mockFor(path);
  const params = new URLSearchParams({ hours: rangeSelect.value, ...extraParams });
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`${API_BASE}${path}${sep}${params.toString()}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

// Igual que fetchJSON, pero fuerza `month_to_date=true` sin importar el
// selector de horas del dashboard. CostQuota es una cuota MENSUAL, así que
// cualquier vista que decida o muestre "excedida"/"OK" (el panel "Estado de
// unidades de negocio", la tabla "Unidades de Negocio" y "Alertas &
// Suspensiones") tiene que usar siempre esta función, no fetchJSON directo.
// El selector de horas sigue afectando SOLO a los gráficos de tendencia
// (dona de tokens, línea de tokens en el tiempo, barras de costo del
// Panel Operativo) — eso está bien, es exploratorio. Mezclar ambas cosas
// fue la causa de que una unidad de negocio apareciera excedida con el
// selector en 12h y "normal" con el selector en 1h (bug detectado el
// 21/08/2026, ver informe de sesión).
async function fetchMonthToDateJSON(path) {
  return fetchJSON(path, { month_to_date: "true" });
}

function fmtMoney(n) {
  return `$${(n ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}
function fmtNumber(n) {
  return (n ?? 0).toLocaleString("en-US");
}

const PALETTE = ["#2f6fed", "#d99a2b", "#8a94a6", "#7b4fd9", "#17a673", "#d64545"];

// ---------------------------------------------------------------------------
// Etiquetas de negocio para las suscripciones
// ---------------------------------------------------------------------------
// Las claves internas (subscription1..4) NO cambian: son las que usa el
// backend, APIM y el chat para identificar la suscripción real. Esto es
// solo la traducción a lo que ve el usuario en pantalla.
const SUBSCRIPTION_LABELS = {
  subscription1: "Unidad de Negocio A",
  subscription2: "Unidad de Negocio B",
  subscription3: "Unidad de Negocio C",
  subscription4: "Unidad de Negocio D",
};

function subLabel(key) {
  return SUBSCRIPTION_LABELS[key] || key;
}

// Traduce cualquier mención de "subscriptionN" dentro de un texto libre
// (por ejemplo, mensajes de quota_exceeded armados por el backend) a la
// etiqueta de negocio correspondiente, sin tocar el resto del texto.
function translateSubText(text) {
  if (!text) return text;
  return text.replace(/subscription(\d)/gi, (match, n) => SUBSCRIPTION_LABELS[`subscription${n}`] || match);
}

function productBadgeClass(product) {
  const p = (product || "").toLowerCase();
  if (p.includes("platin")) return "platinum";
  if (p.includes("gold")) return "gold";
  if (p.includes("silver")) return "silver";
  return "default";
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
async function loadSummary() {
  const s = await fetchJSON("/api/summary");
  document.getElementById("kpi-total-cost").textContent = fmtMoney(s.total_cost);
  document.getElementById("kpi-total-cost-sub").textContent = `${fmtNumber(s.total_calls)} llamadas registradas`;
  document.getElementById("kpi-total-tokens").textContent = fmtNumber(s.total_tokens);
  document.getElementById("kpi-total-tokens-sub").textContent = "en el rango seleccionado";
  document.getElementById("kpi-calls").textContent = fmtNumber(s.total_calls);
  document.getElementById("kpi-calls-sub").textContent = fmtMoney(s.total_cost) + " gastados";
}

async function loadCostQuota() {
  // Esta llamada SÍ respeta el selector de horas a propósito — es la que
  // dibuja el gráfico de tendencia "Cuota vs Costo". El color de excedida/no
  // excedida acá es solo referencial para el gráfico de esa ventana; el
  // estado real "OK"/"Cuota excedida" que se muestra en texto vive en
  // loadQuotaStatus(), que siempre usa mes calendario.
  const { data } = await fetchJSON("/api/costs-by-subscription");
  const labels = data.map((d) => subLabel(d.subscription));
  const quota = data.map((d) => d.cost_quota);
  const cost = data.map((d) => d.total_cost);
  const colors = data.map((d) => (d.exceeded ? "#d64545" : "#2f6fed"));

  if (costChart) costChart.destroy();
  costChart = new Chart(document.getElementById("chart-cost-quota"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "CostQuota", data: quota, backgroundColor: "#cdd8f5", borderRadius: 6 },
        { label: "TotalCost", data: cost, backgroundColor: colors, borderRadius: 6 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } },
      scales: { y: { beginAtZero: true, grid: { color: "#eef1f6" } }, x: { grid: { display: false } } },
    },
  });
  return data;
}

// Estado real "OK" / "Cuota excedida" — SIEMPRE sobre mes calendario, nunca
// sobre el selector de horas. Alimenta el panel "Estado de unidades de
// negocio" (renderStatusList) y la tarjeta "Salud del gateway".
async function loadQuotaStatus() {
  const { data } = await fetchMonthToDateJSON("/api/costs-by-subscription");
  return data;
}

async function loadTokensDonut() {
  const { data } = await fetchJSON("/api/tokens-by-subscription");
  const labels = data.map((d) => `${subLabel(d.subscription)} (${d.percentage}%)`);
  const values = data.map((d) => d.total_tokens);

  if (donutChart) donutChart.destroy();
  donutChart = new Chart(document.getElementById("chart-tokens-donut"), {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: PALETTE, borderWidth: 2, borderColor: "#fff" }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10.5 } } } },
    },
  });
}

async function loadTimeseries() {
  const { data } = await fetchJSON("/api/tokens-timeseries");
  const series = {};
  data.forEach((row) => {
    // Filas sin suscripción (logs huérfanos del join en el backend) no
    // aportan nada a la vista — se descartan en vez de crear una categoría
    // fantasma en la leyenda.
    if (!row.subscription) return;
    // Antes la leyenda mostraba "producto, Unidad de Negocio" (ej.
    // "finops-framework-platinum, Unidad de Negocio A"). Con el filtro por
    // ApiId ya solo aparecen las 4 unidades de negocio de este lab, así que
    // el prefijo de producto sobra — la cuota (visible en "Estado de
    // unidades de negocio" y en las otras vistas) ya distingue cuál es
    // cuál. Se deja solo el nombre de la unidad de negocio (decisión
    // 07/09/2026).
    const key = subLabel(row.subscription);
    if (!series[key]) series[key] = [];
    // `parsing: false` más abajo significa que Chart.js NO convierte
    // automáticamente strings a fechas — hay que pasar un Date ya armado,
    // si no la escala de tiempo descarta el punto en silencio (esto era el
    // bug: el gráfico calculaba el eje pero no dibujaba ninguna línea).
    series[key].push({ x: new Date(row.time), y: row.total_tokens });
  });
  const datasets = Object.entries(series).map(([key, points], i) => ({
    label: key,
    data: points,
    borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length],
    tension: 0.35,
    pointRadius: 2,
    borderWidth: 2,
  }));

  if (timeseriesChart) timeseriesChart.destroy();
  timeseriesChart = new Chart(document.getElementById("chart-tokens-timeseries"), {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      parsing: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10.5 } } } },
      scales: {
        x: { type: "time", time: { unit: "hour" }, grid: { color: "#eef1f6" } },
        y: { beginAtZero: true, grid: { color: "#eef1f6" } },
      },
    },
  });
}

function renderStatusList(costData) {
  const container = document.getElementById("subscription-status-list");
  if (!costData.length) {
    container.innerHTML = `<div class="empty-state">Sin datos en este rango.</div>`;
    return;
  }
  container.innerHTML = costData
    .map(
      (d) => `
      <div class="status-row">
        <span class="sub-name">${subLabel(d.subscription)}</span>
        <span>${fmtMoney(d.total_cost)} / ${fmtMoney(d.cost_quota)}</span>
        <span class="status-pill ${d.exceeded ? "exceeded" : "ok"}">${d.exceeded ? "Cuota excedida" : "OK"}</span>
      </div>`
    )
    .join("");

  const exceeded = costData.filter((d) => d.exceeded).length;
  const healthyPct = Math.round((100 * (costData.length - exceeded)) / costData.length);
  document.getElementById("kpi-health").textContent = `${healthyPct}%`;
  document.getElementById("kpi-health-sub").textContent =
    exceeded > 0 ? `${exceeded} unidad(es) de negocio sobre cuota` : "Todas dentro de cuota";
}

// ---------------------------------------------------------------------------
// Vista: Suscripciones
// ---------------------------------------------------------------------------
async function loadSubscriptionsView() {
  // Mes calendario a propósito: esta vista muestra la columna "Excedida" y
  // alimenta "Alertas & Suspensiones", que documenta el mecanismo REAL de
  // suspensión en APIM — ese mecanismo compara contra CostQuota mensual,
  // así que esta tabla tiene que reflejar exactamente lo mismo, sin
  // importar el selector de horas del Panel Operativo.
  const { data } = await fetchMonthToDateJSON("/api/subscriptions/detail");
  const body = document.getElementById("subscriptions-table-body");
  if (!data.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty-state">Sin datos en este rango.</td></tr>`;
    return data;
  }
  body.innerHTML = data
    .map((d) => {
      const pct = d.cost_quota ? Math.round((100 * d.total_cost) / d.cost_quota) : 0;
      return `
      <tr>
        <td><strong>${subLabel(d.subscription)}</strong></td>
        <td><span class="product-badge ${productBadgeClass(d.product)}">${d.product ?? "—"}</span></td>
        <td>${fmtMoney(d.total_cost)}</td>
        <td>${fmtMoney(d.cost_quota)}</td>
        <td>${pct}%</td>
        <td>${fmtNumber(d.total_tokens)}</td>
        <td>${fmtNumber(d.calls)}</td>
        <td><span class="status-pill ${d.exceeded ? "exceeded" : "ok"}">${d.exceeded ? "Excedida" : "OK"}</span></td>
      </tr>`;
    })
    .join("");
  return data;
}

// ---------------------------------------------------------------------------
// Vista: Productos & Cuotas
// ---------------------------------------------------------------------------
function groupByProduct(detailData) {
  const groups = {};
  detailData.forEach((d) => {
    const key = d.product || "sin producto";
    if (!groups[key]) groups[key] = { product: key, subs: 0, cost: 0, quota: 0, tokens: 0, calls: 0 };
    groups[key].subs += 1;
    groups[key].cost += d.total_cost || 0;
    groups[key].quota += d.cost_quota || 0;
    groups[key].tokens += d.total_tokens || 0;
    groups[key].calls += d.calls || 0;
  });
  return Object.values(groups).sort((a, b) => b.cost - a.cost);
}

async function loadProductsView(detailData) {
  const groups = groupByProduct(detailData);
  const body = document.getElementById("products-table-body");
  if (!groups.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty-state">Sin datos en este rango.</td></tr>`;
  } else {
    body.innerHTML = groups
      .map(
        (g) => `
        <tr>
          <td><span class="product-badge ${productBadgeClass(g.product)}">${g.product}</span></td>
          <td>${g.subs}</td>
          <td>${fmtMoney(g.cost)}</td>
          <td>${fmtMoney(g.quota)}</td>
          <td>${fmtNumber(g.tokens)}</td>
          <td>${fmtNumber(g.calls)}</td>
        </tr>`
      )
      .join("");
  }

  if (productsCostChart) productsCostChart.destroy();
  productsCostChart = new Chart(document.getElementById("chart-products-cost"), {
    type: "bar",
    data: {
      labels: groups.map((g) => g.product),
      datasets: [
        { label: "Costo total", data: groups.map((g) => g.cost), backgroundColor: PALETTE[0], borderRadius: 6 },
        { label: "Cuota total", data: groups.map((g) => g.quota), backgroundColor: "#cdd8f5", borderRadius: 6 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } },
      scales: { y: { beginAtZero: true, grid: { color: "#eef1f6" } }, x: { grid: { display: false } } },
    },
  });

  if (productsTokensChart) productsTokensChart.destroy();
  productsTokensChart = new Chart(document.getElementById("chart-products-tokens"), {
    type: "doughnut",
    data: {
      labels: groups.map((g) => g.product),
      datasets: [{ data: groups.map((g) => g.tokens), backgroundColor: PALETTE, borderWidth: 2, borderColor: "#fff" }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 10.5 } } } },
    },
  });
}

// ---------------------------------------------------------------------------
// Vista: Alertas & Suspensiones
// ---------------------------------------------------------------------------
function loadAlertsView(detailData) {
  const exceeded = detailData.filter((d) => d.exceeded);
  const body = document.getElementById("alerts-table-body");
  if (!exceeded.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty-state">Ninguna unidad de negocio excede su cuota. ✅</td></tr>`;
    return;
  }
  body.innerHTML = exceeded
    .map(
      (d) => `
      <tr>
        <td><strong>${subLabel(d.subscription)}</strong></td>
        <td><span class="product-badge ${productBadgeClass(d.product)}">${d.product ?? "—"}</span></td>
        <td>${fmtMoney(d.total_cost)}</td>
        <td>${fmtMoney(d.cost_quota)}</td>
        <td style="color:#d64545; font-weight:600;">+${fmtMoney(d.total_cost - d.cost_quota)}</td>
        <td>Suspensión vía Logic App (ruleSuspendSub)</td>
      </tr>`
    )
    .join("");
}

// ---------------------------------------------------------------------------
// Vista: Probar Gateway (chat + trazabilidad)
// ---------------------------------------------------------------------------
const chatSubSelect = document.getElementById("chat-subscription");
const chatModelSelect = document.getElementById("chat-model");
const chatMessages = document.getElementById("chat-messages");
const chatInput = document.getElementById("chat-input");
const chatSendBtn = document.getElementById("chat-send-btn");
const traceBody = document.getElementById("trace-body");
const chatGatewayStatus = document.getElementById("chat-gateway-status");

let chatConfigLoaded = false;

async function loadChatConfig() {
  if (chatConfigLoaded) return;
  try {
    // El chat siempre intenta hablar con el backend real primero, sin
    // importar el estado de DEMO_MODE de los gráficos.
    const res = await fetch(`${API_BASE}/api/chat/config`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const cfg = await res.json();

    chatSubSelect.innerHTML =
      `<option value="">Unidad de negocio…</option>` + cfg.subscriptions.map((s) => `<option value="${s}">${subLabel(s)}</option>`).join("");
    chatModelSelect.innerHTML =
      `<option value="">Modelo…</option>` + cfg.models.map((m) => `<option value="${m}">${m}</option>`).join("");

    if (cfg.gateway_configured) {
      CHAT_DEMO_MODE = false;
      chatGatewayStatus.textContent = "gateway conectado";
    } else {
      CHAT_DEMO_MODE = true;
      chatGatewayStatus.textContent = "⚠ faltan APIM_GATEWAY_URL / APIM_SUBSCRIPTIONS_JSON en el App Service";
    }
    chatConfigLoaded = true;
  } catch (e) {
    // Solo si la llamada al backend realmente falla, caemos a modo demo
    // del chat (no por errores de gráficos).
    CHAT_DEMO_MODE = true;
    chatSubSelect.innerHTML =
      `<option value="">Unidad de negocio…</option>` + ["subscription1", "subscription2", "subscription3", "subscription4"].map((s) => `<option value="${s}">${subLabel(s)}</option>`).join("");
    chatModelSelect.innerHTML =
      `<option value="">Modelo…</option>` + ["gpt-5.4-mini", "gpt-5.4", "DeepSeek-V3.2"].map((m) => `<option value="${m}">${m}</option>`).join("");
    chatGatewayStatus.textContent = "⚠ no se pudo cargar la configuración del gateway";
  }
}

function appendChatMessage(role, text) {
  if (chatMessages.querySelector(".empty-state")) chatMessages.innerHTML = "";
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}`;
  bubble.textContent = text;
  chatMessages.appendChild(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function renderTrace(trace) {
  const failoverBanner = trace.failed_over
    ? `<div style="background:#fff4e0;border:1px solid #f5b342;border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:13px;line-height:1.4;">
         ⚠ Failover de cuota: <strong>${subLabel(trace.requested_subscription)}</strong> sin cuota disponible →
         se usó <strong>${subLabel(trace.subscription)}</strong> en su lugar.
       </div>`
    : "";

  traceBody.innerHTML = `
    ${failoverBanner}
    <div class="trace-row"><span>Unidad de negocio usada</span><strong>${subLabel(trace.subscription)}</strong></div>
    ${
      trace.failed_over
        ? `<div class="trace-row"><span>Unidad de negocio pedida</span><strong>${subLabel(trace.requested_subscription)}</strong></div>`
        : ""
    }
    <div class="trace-row"><span>Modelo</span><strong>${trace.model}</strong></div>
    <div class="trace-row"><span>Latencia</span><strong>${trace.latency_ms} ms</strong></div>
    <div class="trace-row"><span>Prompt tokens</span><strong>${fmtNumber(trace.prompt_tokens)}</strong></div>
    <div class="trace-row"><span>Completion tokens</span><strong>${fmtNumber(trace.completion_tokens)}</strong></div>
    <div class="trace-row"><span>Total tokens</span><strong>${fmtNumber(trace.total_tokens)}</strong></div>
  `;
}

async function sendChatMessage() {
  const subscription = chatSubSelect.value;
  const model = chatModelSelect.value;
  const message = chatInput.value.trim();

  if (!subscription || !model) {
    appendChatMessage("system", "Elige una unidad de negocio y un modelo antes de enviar.");
    return;
  }
  if (!message) return;

  appendChatMessage("user", message);
  chatInput.value = "";
  chatSendBtn.disabled = true;

  try {
    if (CHAT_DEMO_MODE) {
      await new Promise((r) => setTimeout(r, 400));
      const fake = {
        reply: "(demo) Esta es una respuesta simulada — conecta el backend real para hablar con el gateway.",
        subscription,
        model,
        latency_ms: Math.round(80 + Math.random() * 200),
        prompt_tokens: 12,
        completion_tokens: 24,
        total_tokens: 36,
      };
      appendChatMessage("assistant", fake.reply);
      renderTrace(fake);
    } else {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription, model, message }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      if (data.quota_exceeded) {
        // Ya no hay failover automático: se para acá, se avisa, y se
        // sugiere una alternativa — el usuario decide si cambiar y
        // reenviar. Por eso se restaura el mensaje al input (se había
        // limpiado antes de mandar la request) en vez de perderlo.
        // data.message viene armado por el backend con "subscriptionN"
        // adentro del texto libre — se traduce con translateSubText en
        // vez de re-armar el mensaje entero acá.
        appendChatMessage("system", `⛔ ${translateSubText(data.message)}`);
        chatInput.value = message;
        if (data.suggested_subscription) {
          chatSubSelect.value = data.suggested_subscription;
          appendChatMessage(
            "system",
            `Cambiá a "${subLabel(data.suggested_subscription)}" arriba y volvé a apretar Enviar para reintentar con esa unidad de negocio.`
          );
        }
        return;
      }

      if (data.failed_over) {
        appendChatMessage(
          "system",
          `⚠ ${subLabel(data.requested_subscription)} sin cuota disponible — se usó ${subLabel(data.subscription)} automáticamente.`
        );
      }
      appendChatMessage("assistant", data.reply);
      renderTrace(data);
    }
  } catch (e) {
    appendChatMessage("system", `Error: ${e.message}`);
  } finally {
    chatSendBtn.disabled = false;
  }
}

chatSendBtn.addEventListener("click", sendChatMessage);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
});

// ---------------------------------------------------------------------------
// Orquestación
// ---------------------------------------------------------------------------
function setConnected() {
  statusDot.classList.remove("error");
  connectionLabel.textContent = DEMO_MODE ? "Modo demostración" : "Conectado a Log Analytics";
}
function setError() {
  statusDot.classList.add("error");
  connectionLabel.textContent = "Error consultando Log Analytics";
}

async function loadAll() {
  try {
    await loadSummary();
    await loadCostQuota(); // gráfico de tendencia — respeta el selector de horas
    const statusData = await loadQuotaStatus(); // estado real — siempre mes calendario
    await loadTokensDonut();
    await loadTimeseries();
    renderStatusList(statusData);

    const detailData = await loadSubscriptionsView(); // ya viene en mes calendario
    await loadProductsView(detailData);
    loadAlertsView(detailData);

    setConnected();
    demoBanner.classList.toggle("hidden", !DEMO_MODE);
  } catch (e) {
    console.warn("No se pudo conectar al backend, activando modo demo:", e.message);
    if (!DEMO_MODE) {
      DEMO_MODE = true;
      demoBanner.classList.remove("hidden");
      return loadAll();
    }
    setError();
  }
}

refreshBtn.addEventListener("click", loadAll);
rangeSelect.addEventListener("change", loadAll);
retryBtn.addEventListener("click", () => {
  DEMO_MODE = false;
  loadAll();
});

loadAll();
loadChatConfig();
setInterval(loadAll, 5 * 60 * 1000);

// ---------------------------------------------------------------------------
// Datos de ejemplo (modo demo) — mismas formas que la API real
// ---------------------------------------------------------------------------
function mockDetail() {
  return [
    { subscription: "subscription1", product: "platinum", cost_quota: 15, total_cost: 6.2, total_tokens: 412000, calls: 84, exceeded: false },
    { subscription: "subscription2", product: "gold", cost_quota: 10, total_cost: 11.4, total_tokens: 512000, calls: 97, exceeded: true },
    { subscription: "subscription3", product: "silver", cost_quota: 5, total_cost: 3.1, total_tokens: 213000, calls: 41, exceeded: false },
    { subscription: "subscription4", product: "silver", cost_quota: 5, total_cost: 5.9, total_tokens: 289000, calls: 53, exceeded: true },
  ];
}

function mockFor(path) {
  const detail = mockDetail();
  if (path.startsWith("/api/summary")) {
    return {
      total_cost: detail.reduce((s, d) => s + d.total_cost, 0),
      total_tokens: detail.reduce((s, d) => s + d.total_tokens, 0),
      total_calls: detail.reduce((s, d) => s + d.calls, 0),
    };
  }
  if (path.startsWith("/api/costs-by-subscription")) {
    return { data: detail.map((d) => ({ subscription: d.subscription, cost_quota: d.cost_quota, total_cost: d.total_cost, exceeded: d.exceeded })) };
  }
  if (path.startsWith("/api/tokens-by-subscription")) {
    const total = detail.reduce((s, d) => s + d.total_tokens, 0);
    return { data: detail.map((d) => ({ subscription: d.subscription, total_tokens: d.total_tokens, percentage: Math.round((1000 * d.total_tokens) / total) / 10 })) };
  }
  if (path.startsWith("/api/tokens-timeseries")) {
    const now = Date.now();
    const rows = [];
    detail.forEach((d) => {
      for (let i = 6; i >= 0; i--) {
        rows.push({
          time: new Date(now - i * 3600 * 1000).toISOString(),
          product: d.product,
          subscription: d.subscription,
          total_tokens: Math.round((d.total_tokens / 7) * (0.5 + Math.random())),
        });
      }
    });
    return { data: rows };
  }
  if (path.startsWith("/api/subscriptions/detail")) {
    return { data: detail };
  }
  return { data: [] };
}