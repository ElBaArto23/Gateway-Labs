/* ============================================================
   AI Foundry Model Gateway · Panel Operativo JS
   ============================================================ */

const MODEL_PALETTE = ['#2F6FED', '#5B8DEF', '#16C784', '#F2A93B', '#8B5CF6', '#EF4444', '#0EA5E9', '#F97316'];

const state = {
  totalCost: 0,
  budgetLimit: 5.0,
  totalPromptTokens: 0,
  totalRespTokens: 0,
  calls: 0,
  errors: 0,
  modelUsage: {},
  history: []
};

// Variables globales para elementos y gráficas
let costChart, tokensChart, modelsChart, latencyChart;
let mHourlyChart;
let chatBody, chatInput, sendBtn, trackerEl, trackFill, trackMsg, steps;

const STEP_MESSAGES = [
  '🔍 Verificando presupuesto (check_budget)...',
  '🔐 Autenticando y recuperando el agente en Azure AI Foundry...',
  '🧠 Generando respuesta vía Responses API...',
  '💰 Calculando costo y registrando logs (EventLogger)...'
];

/* ============================================================
   Inicialización al cargar el DOM
   ============================================================ */
document.addEventListener("DOMContentLoaded", () => {
  // Captura de elementos HTML
  chatBody = document.getElementById('chatBody');
  chatInput = document.getElementById('chatInput');
  sendBtn = document.getElementById('sendBtn');
  trackerEl = document.getElementById('tracker');
  trackFill = document.getElementById('trackFill');
  trackMsg = document.getElementById('trackMsg');
  steps = document.querySelectorAll('.track-step');

  // Inicializar Gráficos de ApexCharts
  initCharts();

  // Asignar Event Listeners
  if (sendBtn) {
    sendBtn.addEventListener('click', handleSend);
  }
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleSend();
      }
    });
  }

  // Navegación entre módulos del sidebar
  initSidebarNav();

  // El Panel Operativo es la vista activa por defecto (no se llega a él
  // por click de sidebar al cargar la página), así que se carga a mano
  // aquí y se arranca su polling explícitamente.
  updateKPIs(); // valores en $0.00 / 0 mientras responde el backend
  loadPanelOperativo();
  startPollingFor('panel-operativo');
});

/* ============================================================
   Navegación del Sidebar (módulos)
   ============================================================ */
// Módulos cuyo contenido no cambia entre solicitudes de chat: se cargan
// una sola vez (lazy) y no se refrescan en cada click.
const STATIC_MODULES = new Set(['pipeline', 'arquitectura-azure']);
const _loadedModules = new Set();

const MODULE_LOADERS = {
  'panel-operativo': loadPanelOperativo,
  'flujo-sistema': loadFlujoSistema,
  'arquitectura-azure': loadArquitecturaAzure,
  'pipeline': loadPipeline,
  'metricas': loadMetricas,
  'problemas': loadProblemas,
  'estado-actual': loadEstadoActual,
  'consumo-interno': loadConsumoInterno,
};

// Cada cuánto se auto-refresca un módulo mientras está visible (ms).
// Los módulos que no cambian con el tiempo (Pipeline, Arquitectura) no
// aparecen aquí, así que nunca hacen polling. Métricas/Problemas usan
// intervalos más largos porque reconstruyen gráficas o tablas grandes.
const POLL_INTERVALS = {
  'panel-operativo': 6000,
  'flujo-sistema': 5000,
  'estado-actual': 5000,
  'consumo-interno': 6000,
  'problemas': 8000,
  'metricas': 15000,
};

let _pollIntervalId = null;

function stopPolling() {
  if (_pollIntervalId) {
    clearInterval(_pollIntervalId);
    _pollIntervalId = null;
  }
}

// Permite "monitoreo en vivo": mientras el administrador solo observa un
// módulo (sin hacer clic en nada), este vuelve a pedir sus datos cada
// POLL_INTERVALS[modulo] ms, para reflejar solicitudes hechas desde otras
// pestañas/usuarios.
function startPollingFor(moduleKey) {
  stopPolling();
  const interval = POLL_INTERVALS[moduleKey];
  if (!interval) return;

  _pollIntervalId = setInterval(() => {
    const section = document.querySelector(`.module-view[data-module="${moduleKey}"]`);
    if (!section || section.style.display === 'none') {
      stopPolling();
      return;
    }
    const loader = MODULE_LOADERS[moduleKey];
    if (loader) {
      loader(moduleKey === 'consumo-interno' ? _consumoSort : undefined);
    }
  }, interval);
}

function initSidebarNav() {
  const navItems = document.querySelectorAll('.nav-item[data-module]');
  const modules = document.querySelectorAll('.module-view');

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const target = item.getAttribute('data-module');

      // Actualiza estado activo en el menú
      navItems.forEach(i => i.classList.remove('active'));
      item.classList.add('active');

      // Muestra el módulo correspondiente, oculta los demás
      modules.forEach(m => {
        m.style.display = (m.getAttribute('data-module') === target) ? '' : 'none';
      });

      // Carga los datos del módulo (lazy). Los módulos estáticos solo
      // se cargan la primera vez que se visitan.
      const loader = MODULE_LOADERS[target];
      if (loader && !(STATIC_MODULES.has(target) && _loadedModules.has(target))) {
        _loadedModules.add(target);
        loader();
      }

      // Arranca (o detiene) el auto-refresco según el módulo al que se navegó.
      startPollingFor(target);
    });
  });
}

/* ============================================================
   Utilidades compartidas por los módulos de datos
   ============================================================ */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str === null || str === undefined ? '' : String(str);
  return div.innerHTML;
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Error ${res.status} al consultar ${url}`);
  }
  return res.json();
}

function renderLoadError(container, err) {
  container.innerHTML = `<div class="empty-state">⚠️ No se pudo cargar: ${escapeHtml(err.message || err)}</div>`;
}

function fmtMoney(v) {
  return '$' + (Number(v) || 0).toFixed(4);
}

function fmtMs(v) {
  if (v === null || v === undefined) return '—';
  return Math.round(Number(v)) + ' ms';
}

function liveBadge() {
  const t = new Date();
  const hhmmss = t.getHours().toString().padStart(2, '0') + ':' + t.getMinutes().toString().padStart(2, '0') + ':' + t.getSeconds().toString().padStart(2, '0');
  return `<span class="meta-chip"><span class="badge-dot" style="color:var(--green-500);display:inline-block;margin-right:4px;"></span>en vivo · actualizado ${hhmmss}</span>`;
}

/* ============================================================
   Módulo: Panel Operativo (vista principal)  →  GET /api/consumption
                                                   GET /api/status
                                                   GET /api/metrics
   ============================================================ */
async function loadPanelOperativo() {
  try {
    const midnightLocal = new Date();
    midnightLocal.setHours(0, 0, 0, 0);
    const sinceToday = midnightLocal.toISOString();

    const [summary, status, charts, todaySummary] = await Promise.all([
      fetchJSON('/api/consumption'),
      fetchJSON('/api/status'),
      fetchJSON('/api/metrics'),
      fetchJSON(`/api/consumption?since=${encodeURIComponent(sinceToday)}`)
    ]);
    renderPanelOperativoKPIs(summary, status, charts, todaySummary);
    renderPanelOperativoCharts(charts, summary);
  } catch (err) {
    console.error('No se pudo actualizar el Panel Operativo:', err);
  }
}

function renderPanelOperativoKPIs(summary, status, charts, todaySummary) {
  const gw = status.gateway || {};
  const ts = (charts && charts.time_series) || {};
  const totalCost = Number(summary.total_cost) || 0;
  const totalTokens = Number(summary.total_tokens) || 0;
  const callCount = Number(summary.call_count) || 0;
  const budgetLimit = Number(status.budget && status.budget.max_usd) || state.budgetLimit;
  state.budgetLimit = budgetLimit;

  const promptTotal = (ts.input_tokens || []).reduce((a, b) => a + (Number(b) || 0), 0);
  const respTotal = (ts.output_tokens || []).reduce((a, b) => a + (Number(b) || 0), 0);

  document.getElementById('kpiCost').textContent = '$' + totalCost.toFixed(4);
  document.getElementById('kpiCostSub').textContent = callCount + ' llamadas registradas';
  document.getElementById('kpiCostTrend').textContent = callCount
    ? '+' + ((totalCost / budgetLimit) * 100).toFixed(1) + '%'
    : '+0%';

  document.getElementById('kpiTokens').textContent = totalTokens.toLocaleString('es-CO');
  document.getElementById('kpiTokensSub').textContent =
    `prompt ${promptTotal.toLocaleString('es-CO')} · respuesta ${respTotal.toLocaleString('es-CO')}`;
  document.getElementById('kpiTokTrend').textContent = callCount ? '+' + callCount : '+0';

  const successRate = gw.success_rate_percentage ?? 100;
  document.getElementById('kpiHealth').textContent = Math.round(successRate) + '%';
  document.getElementById('kpiHealthSub').textContent = `${gw.error_count || 0} errores · 0 bloqueos`;
  document.getElementById('kpiHealthTrend').textContent = successRate >= 95 ? 'estable' : 'atención';

  const today = todaySummary || {};
  const todayCalls = Number(today.call_count) || 0;
  const todayCost = Number(today.total_cost) || 0;
  const todayTokens = Number(today.total_tokens) || 0;
  document.getElementById('kpiToday').textContent = todayCalls.toLocaleString('es-CO');
  document.getElementById('kpiTodaySub').textContent =
    `$${todayCost.toFixed(4)} · ${todayTokens.toLocaleString('es-CO')} tokens`;
  document.getElementById('kpiTodayTrend').textContent = todayCalls ? '+' + todayCalls : '+0';

  document.getElementById('budgetSpentTxt').textContent = '$' + totalCost.toFixed(4);
  document.getElementById('budgetFill').style.width =
    Math.min(100, (totalCost / budgetLimit) * 100) + '%';

  const statusEl = document.getElementById('connStatus');
  if (statusEl) {
    if (callCount > 0 && successRate >= 95) {
      statusEl.textContent = 'Conectado a Azure AI Foundry';
    } else if (callCount > 0 && successRate < 95) {
      statusEl.textContent = 'Gateway degradado';
    } else {
      statusEl.textContent = 'Backend listo · esperando actividad';
    }
  }
}

function renderPanelOperativoCharts(charts, summary) {
  const ts = (charts && charts.time_series) || { labels: [], cost_series: [], input_tokens: [], output_tokens: [] };
  const lat = (charts && charts.latency_series) || { labels: [], gateway_latency: [], backend_latency: [] };
  const byModel = Object.entries((summary && summary.by_model) || {});

  const RECENT_WINDOW = 6;
  const shortTime = (ts) => {
    if (!ts) return '';
    const parts = String(ts).trim().split(' ');
    return parts.length > 1 ? parts[1] : parts[0];
  };

  const labels = (ts.labels || []).slice(-RECENT_WINDOW).map(shortTime);
  costChart.updateOptions({ xaxis: { categories: labels } });
  costChart.updateSeries([{ name: 'Costo (USD)', data: (ts.cost_series || []).slice(-RECENT_WINDOW) }]);

  tokensChart.updateOptions({ xaxis: { categories: labels } });
  tokensChart.updateSeries([
    { name: 'Prompt', data: (ts.input_tokens || []).slice(-RECENT_WINDOW) },
    { name: 'Respuesta', data: (ts.output_tokens || []).slice(-RECENT_WINDOW) }
  ]);

  const modelLabels = byModel.map(([m]) => m);
  const modelCounts = byModel.map(([, m]) => m.call_count || 0);
  const totalCalls = modelCounts.reduce((a, b) => a + b, 0);
  modelsChart.updateOptions({
    labels: modelLabels,
    colors: modelLabels.map((_, i) => MODEL_PALETTE[i % MODEL_PALETTE.length])
  });
  modelsChart.updateSeries(modelCounts);
  const captionEl = document.getElementById('chartModelsCaption');
  if (captionEl) {
    captionEl.textContent = totalCalls === 1 ? '1 llamada registrada' : `${totalCalls} llamadas registradas`;
  }

  const latLabels = (lat.labels || []).slice(-RECENT_WINDOW).map(shortTime);
  latencyChart.updateOptions({ xaxis: { categories: latLabels } });
  latencyChart.updateSeries([
    { name: 'Proceso interno (Flask)', data: (lat.gateway_latency || []).slice(-RECENT_WINDOW) },
    { name: 'Modelo (Azure AI Foundry)', data: (lat.backend_latency || []).slice(-RECENT_WINDOW) }
  ]);
}

/* ============================================================
   Módulo: Consumo interno  →  GET /api/consumption + /api/consumption/calls
   ============================================================ */
let _consumoSort = 'cost';

async function loadConsumoInterno(sortBy) {
  sortBy = sortBy || _consumoSort || 'cost';
  _consumoSort = sortBy;
  const el = document.getElementById('viewConsumoInterno');
  if (!el.querySelector('.kpi-mini-grid')) {
    el.innerHTML = '<div class="loading-state">Cargando consumo…</div>';
  }
  try {
    const [data, callsData] = await Promise.all([
      fetchJSON('/api/consumption'),
      fetchJSON(`/api/consumption/calls?sort=${sortBy}&limit=20`)
    ]);
    const models = Object.entries(data.by_model || {});

    const rows = models.map(([model, m]) => `
      <tr${model === data.top_model ? ' style="background:#F5F9FF;"' : ''}>
        <td>${escapeHtml(model)}${model === data.top_model ? ' <span class="badge badge-ok">top</span>' : ''}</td>
        <td class="mono">${m.call_count}</td>
        <td class="mono">${(m.total_tokens || 0).toLocaleString('es-CO')}</td>
        <td class="mono">${fmtMoney(m.total_cost)}</td>
        <td class="mono">${fmtMoney(m.average_cost_per_call)}</td>
      </tr>
    `).join('');

    const calls = callsData.calls || [];
    const maxCost = calls.length ? Math.max(...calls.map(c => c.total_cost || 0)) : 0;
    const maxTokens = calls.length ? Math.max(...calls.map(c => c.total_tokens || 0)) : 0;

    const callRows = calls.map(c => `
      <tr>
        <td class="mono">${escapeHtml((c.timestamp || '').replace('T', ' ').slice(0, 19))}</td>
        <td>${escapeHtml(c.model_normalized || c.model)}</td>
        <td class="mono">${c.input_tokens ?? 0}</td>
        <td class="mono">${c.output_tokens ?? 0}</td>
        <td class="mono">${(c.total_tokens || 0) === maxTokens && maxTokens > 0 ? '<span class="badge badge-warning">' + c.total_tokens + '</span>' : (c.total_tokens ?? 0)}</td>
        <td class="mono">${(c.total_cost || 0) === maxCost && maxCost > 0 ? '<span class="badge badge-critical">' + fmtMoney(c.total_cost) + '</span>' : fmtMoney(c.total_cost)}</td>
      </tr>
    `).join('');

    el.innerHTML = `
      <div class="module-meta-row">${liveBadge()}</div>
      <div class="kpi-mini-grid">
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Costo total</span>
          <span class="kpi-mini-value">${fmtMoney(data.total_cost)}</span>
          <span class="kpi-mini-sub">${data.call_count || 0} llamadas</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Tokens totales</span>
          <span class="kpi-mini-value">${(data.total_tokens || 0).toLocaleString('es-CO')}</span>
          <span class="kpi-mini-sub">avg ${Math.round(data.average_tokens_per_call || 0)}/llamada</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Costo promedio/llamada</span>
          <span class="kpi-mini-value">${fmtMoney(data.average_cost_per_call)}</span>
          <span class="kpi-mini-sub">agente: ${escapeHtml(data.agent_name || 'todos')}</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Modelo más usado</span>
          <span class="kpi-mini-value" style="font-size:14px;">${escapeHtml(data.top_model || '—')}</span>
          <span class="kpi-mini-sub">por número de llamadas</span>
        </div>
      </div>

      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Modelo</th><th>Llamadas</th><th>Tokens</th><th>Costo total</th><th>Costo prom.</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5" class="empty-state">Aún no hay consumo registrado.</td></tr>'}</tbody>
        </table>
      </div>

      <div class="module-meta-row" style="align-items:center;justify-content:space-between;">
        <span class="kpi-mini-label">Solicitudes individuales (top 20)</span>
        <select id="consumoCallsSort" class="meta-chip" style="cursor:pointer;">
          <option value="cost" ${sortBy === 'cost' ? 'selected' : ''}>Ordenar por costo</option>
          <option value="tokens" ${sortBy === 'tokens' ? 'selected' : ''}>Ordenar por tokens</option>
          <option value="time" ${sortBy === 'time' ? 'selected' : ''}>Más recientes</option>
        </select>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Hora</th><th>Modelo</th><th>Tok. entrada</th><th>Tok. salida</th><th>Tok. total</th><th>Costo</th></tr></thead>
          <tbody>${callRows || '<tr><td colspan="6" class="empty-state">Aún no hay solicitudes registradas.</td></tr>'}</tbody>
        </table>
      </div>`;

    const sortSelect = document.getElementById('consumoCallsSort');
    if (sortSelect) {
      sortSelect.addEventListener('change', () => loadConsumoInterno(sortSelect.value));
    }
  } catch (err) {
    renderLoadError(el, err);
  }
}

/* ============================================================
   Módulo: Pipeline  →  GET /api/pipeline/stages
   ============================================================ */
async function loadPipeline() {
  const el = document.getElementById('viewPipeline');
  el.innerHTML = '<div class="loading-state">Cargando pipeline…</div>';
  try {
    const data = await fetchJSON('/api/pipeline/stages');
    const items = (data.stages || []).map((s, i, arr) => `
      <div class="timeline-item">
        <div class="timeline-marker">
          <div class="timeline-dot">${s.order}</div>
          ${i < arr.length - 1 ? '<div class="timeline-line"></div>' : ''}
        </div>
        <div class="timeline-content">
          <div class="timeline-label">${escapeHtml(s.label)}</div>
          <div class="timeline-detail mono">${escapeHtml(s.key)}</div>
        </div>
      </div>`).join('');
    el.innerHTML = `<div class="timeline">${items}</div>`;
  } catch (err) {
    renderLoadError(el, err);
  }
}

/* ============================================================
   Módulo: Flujo del Sistema (Auditoría)  →  GET /api/requests
                                              GET /api/requests/<id>
   ============================================================ */
let _flujoRequestsCache = [];
let _flujoSelectedId = null;
let _flujoSearchQuery = '';
let _flujoTodayOnly = false;

function statusBadgeFor(status) {
  if (status === 'ok') return '<span class="badge badge-ok"><span class="badge-dot"></span>OK</span>';
  if (status === 'blocked') return '<span class="badge badge-warning"><span class="badge-dot"></span>Bloqueada</span>';
  return '<span class="badge badge-error"><span class="badge-dot"></span>Error</span>';
}

function isToday(tsStr) {
  if (!tsStr) return false;
  const d = new Date(tsStr);
  if (isNaN(d.getTime())) return false;
  const now = new Date();
  return d.getFullYear() === now.getFullYear() &&
         d.getMonth() === now.getMonth() &&
         d.getDate() === now.getDate();
}

function applyFlujoFilter(list, q) {
  q = (q || '').trim().toLowerCase();
  if (!q) return list;
  return list.filter(r =>
    (r.timestamp || '').toLowerCase().includes(q) || (r.request_id || '').toLowerCase().includes(q)
  );
}

function getFilteredFlujoRequests() {
  let list = applyFlujoFilter(_flujoRequestsCache, _flujoSearchQuery);
  if (_flujoTodayOnly) {
    list = list.filter(r => isToday(r.timestamp));
  }
  return list;
}

async function loadFlujoSistema() {
  const el = document.getElementById('viewFlujoSistema');
  let searchInput = document.getElementById('flujoSearchInput');

  if (!searchInput) {
    el.innerHTML = `
      <div class="module-meta-row" style="justify-content:space-between;align-items:center;">
        <span class="kpi-mini-label" id="flujoCountLabel">Cargando solicitudes…</span>
        <div style="display:flex;align-items:center;gap:12px;">
          <label class="meta-chip" style="display:flex;align-items:center;gap:5px;cursor:pointer;">
            <input type="checkbox" id="flujoTodayToggle">Solo hoy
          </label>
          <input type="text" id="flujoSearchInput" class="search-input" placeholder="Buscar por hora (ej. 10:32) o request_id">
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Hora</th><th>Modelo</th><th>Tokens</th><th>Latencia</th><th>Costo</th><th>Estado</th></tr></thead>
          <tbody id="flujoRequestsRows"></tbody>
        </table>
      </div>
      <div id="flujoDetailPanel" style="margin-top:6px;"><div class="loading-state">Cargando detalle…</div></div>`;
    searchInput = document.getElementById('flujoSearchInput');
    searchInput.value = _flujoSearchQuery;
    searchInput.addEventListener('input', () => {
      _flujoSearchQuery = searchInput.value;
      renderFlujoRequestsRows(getFilteredFlujoRequests());
    });

    const todayToggle = document.getElementById('flujoTodayToggle');
    todayToggle.checked = _flujoTodayOnly;
    todayToggle.addEventListener('change', () => {
      _flujoTodayOnly = todayToggle.checked;
      renderFlujoRequestsRows(getFilteredFlujoRequests());
    });
  }

  try {
    const data = await fetchJSON('/api/requests?limit=50');
    _flujoRequestsCache = data.requests || [];
    const shownCount = getFilteredFlujoRequests().length;
    document.getElementById('flujoCountLabel').innerHTML = _flujoTodayOnly
      ? `Solicitudes de hoy (${shownCount} de ${_flujoRequestsCache.length} recientes) &nbsp;·&nbsp; ${liveBadge()}`
      : `Solicitudes recientes (${_flujoRequestsCache.length}) &nbsp;·&nbsp; ${liveBadge()}`;

    renderFlujoRequestsRows(getFilteredFlujoRequests());

    const stillExists = _flujoSelectedId && _flujoRequestsCache.some(r => r.request_id === _flujoSelectedId);
    if (stillExists) {
      loadRequestDetail(_flujoSelectedId, true);
    } else if (_flujoRequestsCache.length > 0) {
      loadRequestDetail(_flujoRequestsCache[0].request_id);
    } else {
      document.getElementById('flujoDetailPanel').innerHTML = '<div class="empty-state">Aún no se ha procesado ninguna solicitud.</div>';
    }
  } catch (err) {
    renderLoadError(el, err);
  }
}

function renderFlujoRequestsRows(list) {
  const tbody = document.getElementById('flujoRequestsRows');
  if (!tbody) return;
  tbody.innerHTML = list.map(r => `
    <tr class="request-row${r.request_id === _flujoSelectedId ? ' selected' : ''}" data-request-id="${escapeHtml(r.request_id)}">
      <td class="mono">${escapeHtml((r.timestamp || '').replace('T', ' ').slice(0, 19))}</td>
      <td>${escapeHtml(r.model || '—')}</td>
      <td class="mono">${r.total_tokens ?? '—'}</td>
      <td class="mono">${fmtMs(r.total_latency_ms)}</td>
      <td class="mono">${fmtMoney(r.cost_total)}</td>
      <td>${statusBadgeFor(r.status)}</td>
    </tr>
  `).join('') || '<tr><td colspan="6" class="empty-state">Sin resultados para esa búsqueda.</td></tr>';

  tbody.querySelectorAll('.request-row').forEach(row => {
    row.addEventListener('click', () => loadRequestDetail(row.getAttribute('data-request-id')));
  });
}

async function loadRequestDetail(requestId) {
  _flujoSelectedId = requestId;
  renderFlujoRequestsRows(applyFlujoFilter(_flujoRequestsCache, _flujoSearchQuery));

  const panel = document.getElementById('flujoDetailPanel');
  if (!panel) return;
  if (!panel.dataset.loadedFor || panel.dataset.loadedFor !== requestId) {
    panel.innerHTML = '<div class="loading-state">Cargando detalle…</div>';
  }
  try {
    const data = await fetchJSON(`/api/requests/${encodeURIComponent(requestId)}`);
    panel.dataset.loadedFor = requestId;
    const s = data.summary || {};
    const events = data.events || [];

    const items = events.map((ev, i, arr) => `
      <div class="timeline-item">
        <div class="timeline-marker">
          <div class="timeline-dot ${ev.status === 'error' ? 'error' : 'done'}">${ev.status === 'error' ? '!' : '✓'}</div>
          ${i < arr.length - 1 ? '<div class="timeline-line"></div>' : ''}
        </div>
        <div class="timeline-content">
          <div class="timeline-label">${escapeHtml(ev.label)}</div>
          ${ev.detail ? `<div class="timeline-detail">${escapeHtml(ev.detail)}</div>` : ''}
          <div class="timeline-duration">${fmtMs(ev.duration_ms)}</div>
        </div>
      </div>`).join('');

    panel.innerHTML = `
      <div class="kpi-mini-grid">
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Modelo</span>
          <span class="kpi-mini-value" style="font-size:14px;">${escapeHtml(s.model || '—')}</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Tokens (entrada / salida)</span>
          <span class="kpi-mini-value" style="font-size:14px;">${s.input_tokens ?? 0} / ${s.output_tokens ?? 0}</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Latencia total</span>
          <span class="kpi-mini-value" style="font-size:14px;">${fmtMs(s.total_latency_ms)}</span>
          <span class="kpi-mini-sub">gateway ${fmtMs(s.gateway_latency_ms)} · Azure ${fmtMs(s.backend_latency_ms)}</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Costo</span>
          <span class="kpi-mini-value" style="font-size:14px;">${fmtMoney(s.cost_total)}</span>
        </div>
      </div>
      <div class="module-meta-row">
        ${statusBadgeFor(s.status)}
        <span class="meta-chip">request_id: ${escapeHtml(requestId)}</span>
        <span class="meta-chip">${escapeHtml((s.timestamp || '').replace('T', ' ').slice(0, 19))}</span>
      </div>
      <div class="timeline">${items || '<div class="empty-state">No hay eventos detallados guardados para esta solicitud.</div>'}</div>`;
  } catch (err) {
    renderLoadError(panel, err);
  }
}

/* ============================================================
   Módulo: Arquitectura Azure  →  GET /api/architecture
   ============================================================ */
async function loadArquitecturaAzure() {
  const el = document.getElementById('viewArquitecturaAzure');
  el.innerHTML = '<div class="loading-state">Cargando arquitectura…</div>';
  try {
    const data = await fetchJSON('/api/architecture');
    const nodesById = {};
    (data.nodes || []).forEach(n => { nodesById[n.id] = n; });

    const nodeCards = (data.nodes || []).map(n => `
      <div class="arch-node">
        <div class="arch-node-top">
          <span class="arch-node-type">${escapeHtml(n.type)}</span>
          <span class="badge ${n.status === 'active' ? 'badge-ok' : 'badge-idle'}"><span class="badge-dot"></span>${escapeHtml(n.status)}</span>
        </div>
        <div class="arch-node-label">${escapeHtml(n.label)}</div>
      </div>`).join('');

    const edgeList = (data.edges || []).map(e => {
      const from = nodesById[e.from]?.label || e.from;
      const to = nodesById[e.to]?.label || e.to;
      return `<div class="arch-edge">${escapeHtml(from)} → ${escapeHtml(to)}</div>`;
    }).join('');

    el.innerHTML = `
      <div class="module-meta-row">
        <span class="meta-chip">endpoint: ${escapeHtml(data.endpoint || '—')}</span>
        <span class="meta-chip">agente: ${escapeHtml(data.agent_name || '—')}</span>
      </div>
      <div class="arch-flow">${nodeCards}</div>
      <div class="arch-edges">${edgeList}</div>`;
  } catch (err) {
    renderLoadError(el, err);
  }
}

/* ============================================================
   Módulo: Métricas  →  GET /api/metrics & GET /api/requests
   ============================================================ */
let mCostChart, mTokensChart, mModelsChart, mLatencyChart;

function computeCallsByHour(requests) {
  const buckets = new Array(24).fill(0);
  (requests || []).forEach(r => {
    const d = new Date(r.timestamp);
    if (isNaN(d.getTime())) return;
    buckets[d.getHours()] += 1;   // hora LOCAL del navegador
  });
  return buckets;
}

function renderHourlyChart(requests) {
  if (mHourlyChart && typeof mHourlyChart.destroy === 'function') {
    try { mHourlyChart.destroy(); } catch (_) {}
  }
  const buckets = computeCallsByHour(requests);
  mHourlyChart = new ApexCharts(document.querySelector('#mChartHourly'), {
    chart: { type: 'bar', height: 210, toolbar: { show: false }, fontFamily: 'Inter, sans-serif' },
    series: [{ name: 'Llamadas', data: buckets }],
    xaxis: { categories: buckets.map((_, h) => String(h).padStart(2, '0') + 'h'), labels: { style: { fontSize: '9px', colors: '#A4AFC3' } } },
    plotOptions: { bar: { columnWidth: '55%', borderRadius: 4 } },
    colors: ['#2F6FED'],
    grid: { borderColor: '#EEF1F6', strokeDashArray: 4 },
    dataLabels: { enabled: false }
  });
  mHourlyChart.render();
}

function renderMetricsCharts(data) {
  const ts = data.time_series || { labels: [], cost_series: [], input_tokens: [], output_tokens: [] };
  const pie = data.cost_by_model_pie || { labels: [], series: [] };
  const lat = data.latency_series || { labels: [], gateway_latency: [], backend_latency: [] };
  const baseOpts = {
    chart: { toolbar: { show: false }, fontFamily: 'Inter, sans-serif', animations: { speed: 300 } },
    grid: { borderColor: '#EEF1F6', strokeDashArray: 4, padding: { left: 6, right: 10 } },
    dataLabels: { enabled: false }
  };

  [mCostChart, mTokensChart, mModelsChart, mLatencyChart].forEach(c => {
    if (c && typeof c.destroy === 'function') {
      try { c.destroy(); } catch (_) {}
    }
  });

  mCostChart = new ApexCharts(document.querySelector('#mChartCost'), {
    ...baseOpts, chart: { ...baseOpts.chart, type: 'area', height: 210 },
    series: [{ name: 'Costo (USD)', data: ts.cost_series || [] }],
    xaxis: { categories: ts.labels || [], labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    yaxis: { labels: { style: { fontSize: '10px', colors: '#A4AFC3' }, formatter: v => '$' + v.toFixed(3) } },
    stroke: { curve: 'smooth', width: 2.5, colors: ['#2F6FED'] },
    fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0, stops: [0, 90] }, colors: ['#2F6FED'] },
    tooltip: { y: { formatter: v => '$' + v.toFixed(5) } }
  });
  mCostChart.render();

  mTokensChart = new ApexCharts(document.querySelector('#mChartTokens'), {
    ...baseOpts, chart: { ...baseOpts.chart, type: 'bar', height: 210, stacked: true },
    series: [{ name: 'Prompt', data: ts.input_tokens || [] }, { name: 'Respuesta', data: ts.output_tokens || [] }],
    xaxis: { categories: ts.labels || [], labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    plotOptions: { bar: { columnWidth: '46%', borderRadius: 4 } },
    colors: ['#5B8DEF', '#183963'],
    legend: { show: true, fontSize: '10.5px', position: 'top', horizontalAlign: 'right' }
  });
  mTokensChart.render();

  mModelsChart = new ApexCharts(document.querySelector('#mChartModels'), {
    chart: { type: 'donut', height: 220, fontFamily: 'Inter, sans-serif' },
    series: pie.series || [], labels: pie.labels || [], colors: MODEL_PALETTE,
    dataLabels: { enabled: true, style: { fontSize: '9.5px' } },
    legend: { show: true, fontSize: '10.5px', position: 'right' },
    stroke: { width: 2, colors: ['#fff'] },
    noData: { text: 'Aún no hay datos', style: { fontSize: '11px', color: '#A4AFC3' } },
    tooltip: { y: { formatter: v => '$' + Number(v).toFixed(5) } }
  });
  mModelsChart.render();

  mLatencyChart = new ApexCharts(document.querySelector('#mChartLatency'), {
    ...baseOpts, chart: { ...baseOpts.chart, type: 'line', height: 210 },
    series: [{ name: 'Gateway', data: lat.gateway_latency || [] }, { name: 'Backend (Azure)', data: lat.backend_latency || [] }],
    xaxis: { categories: lat.labels || [], labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    yaxis: { labels: { formatter: v => v + 'ms', style: { fontSize: '10px', colors: '#A4AFC3' } } },
    stroke: { curve: 'smooth', width: 2.5 }, colors: ['#F2A93B', '#0F2547'],
    legend: { show: true, fontSize: '10.5px', position: 'top', horizontalAlign: 'right' }
  });
  mLatencyChart.render();
}

async function loadMetricas() {
  const el = document.getElementById('viewMetricas');
  el.innerHTML = `
    <div class="chart-grid" id="metricasChartGrid">
      <div class="chart-card"><div class="chart-head"><h3>Evolución del costo</h3><span>histórico</span></div><div id="mChartCost"></div></div>
      <div class="chart-card"><div class="chart-head"><h3>Tokens por mensaje</h3><span>prompt vs respuesta</span></div><div id="mChartTokens"></div></div>
      <div class="chart-card"><div class="chart-head"><h3>Costo por modelo</h3><span>acumulado</span></div><div id="mChartModels"></div></div>
      <div class="chart-card"><div class="chart-head"><h3>Latencia</h3><span>gateway vs backend (ms)</span></div><div id="mChartLatency"></div></div>
    </div>
    <div class="table-wrap" id="metricasAggTable"></div>`;

  try {
    const [data, reqData] = await Promise.all([
      fetchJSON('/api/metrics'),
      fetchJSON('/api/requests?limit=200'),
    ]);
    renderMetricsCharts(data);
    renderHourlyChart(reqData.requests);
    renderAggregatedMetricsTable(data.aggregated);
  } catch (err) {
    renderLoadError(el, err);
  }
}

function renderAggregatedMetricsTable(aggregated) {
  const container = document.getElementById('metricasAggTable');
  if (!container) return;
  const metrics = Object.entries((aggregated && aggregated.metrics) || {});
  const rows = metrics.map(([name, m]) => `
    <tr>
      <td>${escapeHtml(name)}</td>
      <td class="mono">${m.count}</td>
      <td class="mono">${m.avg}</td>
      <td class="mono">${m.min}</td>
      <td class="mono">${m.max}</td>
    </tr>`).join('');
  container.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Métrica</th><th>Muestras</th><th>Prom.</th><th>Mín.</th><th>Máx.</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5" class="empty-state">Sin métricas agregadas en la última ${aggregated?.time_window_minutes || 60} min.</td></tr>`}</tbody>
    </table>`;
}

/* ============================================================
   Módulo: Problemas encontrados  →  GET /api/issues
   ============================================================ */
async function loadProblemas() {
  const el = document.getElementById('viewProblemas');
  el.innerHTML = '<div class="loading-state">Cargando problemas…</div>';
  try {
    const data = await fetchJSON('/api/issues');

    const alerts = (data.alerts || []).map(a => `
      <div class="module-meta-row" style="margin-bottom:4px;">
        <span class="badge badge-${(a.severity || 'warning').toLowerCase()}"><span class="badge-dot"></span>${escapeHtml(a.severity)}</span>
        <span style="font-size:12.5px;color:var(--ink-700);">${escapeHtml(a.message)}</span>
      </div>`).join('') || '<div class="empty-state">Sin alertas activas.</div>';

    const errorRows = (data.errors || []).slice().reverse().map(e => `
      <tr>
        <td class="mono">${escapeHtml((e.timestamp || '').replace('T', ' ').slice(0, 19))}</td>
        <td>${escapeHtml(e.stage)}</td>
        <td>${escapeHtml(e.message)}</td>
        <td class="mono">${escapeHtml(e.request_id)}</td>
      </tr>`).join('');

    const gwRows = (data.gateway_errors || []).slice().reverse().map(g => `
      <tr>
        <td class="mono">${escapeHtml((g.timestamp || '').replace('T', ' ').slice(0, 19))}</td>
        <td><span class="badge badge-error">${g.status_code}</span></td>
        <td>${escapeHtml(g.endpoint)}</td>
        <td class="mono">${fmtMs(g.total_latency_ms)}</td>
      </tr>`).join('');

    el.innerHTML = `
      <div>
        <div class="kpi-mini-label" style="margin-bottom:8px;">Alertas activas</div>
        ${alerts}
      </div>
      <div>
        <div class="kpi-mini-label" style="margin-bottom:8px;">Errores de ejecución</div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Hora</th><th>Etapa</th><th>Mensaje</th><th>Request ID</th></tr></thead>
            <tbody>${errorRows || '<tr><td colspan="4" class="empty-state">Sin errores registrados.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="kpi-mini-label" style="margin-bottom:8px;">Errores del Gateway (status ≥ 400)</div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Hora</th><th>Status</th><th>Endpoint</th><th>Latencia total</th></tr></thead>
            <tbody>${gwRows || '<tr><td colspan="4" class="empty-state">Sin errores de gateway registrados.</td></tr>'}</tbody>
          </table>
        </div>
      </div>`;
  } catch (err) {
    renderLoadError(el, err);
  }
}

/* ============================================================
   Módulo: Estado actual  →  GET /api/status
   ============================================================ */
async function loadEstadoActual() {
  const el = document.getElementById('viewEstadoActual');
  el.innerHTML = '<div class="loading-state">Cargando estado…</div>';
  try {
    const data = await fetchJSON('/api/status');
    const gw = data.gateway || {};
    const fc = data.foundry_connection || {};
    const rpm = data.requests_per_minute || { peak_per_minute: 0, avg_per_minute: 0, labels: [], values: [] };

    const overallBadge = data.status === 'ok'
      ? '<span class="badge badge-ok"><span class="badge-dot"></span>Operativo</span>'
      : '<span class="badge badge-warning"><span class="badge-dot"></span>Degradado</span>';

    el.innerHTML = `
      <div class="module-meta-row">
        ${overallBadge}
        ${liveBadge()}
        <span class="meta-chip">uptime: ${data.uptime_seconds}s</span>
        <span class="meta-chip">presupuesto máx: ${fmtMoney(data.budget?.max_usd)}</span>
      </div>
      <div class="kpi-mini-grid">
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Tasa de éxito (gateway)</span>
          <span class="kpi-mini-value">${gw.success_rate_percentage ?? 100}%</span>
          <span class="kpi-mini-sub">${gw.total_requests || 0} solicitudes · ${gw.error_count || 0} errores</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Latencia promedio</span>
          <span class="kpi-mini-value">${fmtMs(gw.avg_total_latency_ms)}</span>
          <span class="kpi-mini-sub">total (gateway + Azure)</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Solicitudes/min (pico)</span>
          <span class="kpi-mini-value">${rpm.peak_per_minute}</span>
          <span class="kpi-mini-sub">promedio ${rpm.avg_per_minute}/min · últimos ${rpm.labels.length || 15} min</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Conexión Azure AI Foundry</span>
          <span class="kpi-mini-value">${fc.connected ? 'Conectado' : 'Sin conectar'}</span>
          <span class="kpi-mini-sub">agente ${fc.agent_cached ? 'en caché' : 'no cargado'} · ${escapeHtml(fc.agent_name || '')}</span>
        </div>
        <div class="kpi-mini-card">
          <span class="kpi-mini-label">Conversación activa</span>
          <span class="kpi-mini-value">${fc.conversation_active ? 'Sí' : 'No'}</span>
          <span class="kpi-mini-sub">se reutiliza por proceso</span>
        </div>
      </div>`;
  } catch (err) {
    renderLoadError(el, err);
  }
}

/* ============================================================
   Configuración de Gráficos (ApexCharts)
   ============================================================ */
function initCharts() {
  const baseChartOpts = {
    chart: { toolbar: { show: false }, fontFamily: 'Inter, sans-serif', animations: { speed: 400 } },
    grid: { borderColor: '#EEF1F6', strokeDashArray: 4, padding: { left: 6, right: 10 } },
    dataLabels: { enabled: false },
    legend: { show: false }
  };

  costChart = new ApexCharts(document.querySelector("#chartCost"), {
    ...baseChartOpts,
    chart: { ...baseChartOpts.chart, type: 'area', height: 210 },
    series: [{ name: 'Costo (USD)', data: [] }],
    xaxis: { categories: [], labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    yaxis: { labels: { style: { fontSize: '10px', colors: '#A4AFC3' }, formatter: (v) => '$' + v.toFixed(3) } },
    stroke: { curve: 'smooth', width: 2.5, colors: ['#2F6FED'] },
    fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0, stops: [0, 90] }, colors: ['#2F6FED'] },
    tooltip: { y: { formatter: (v) => '$' + v.toFixed(5) + ' USD' } }
  });
  costChart.render();

  tokensChart = new ApexCharts(document.querySelector("#chartTokens"), {
    ...baseChartOpts,
    chart: { ...baseChartOpts.chart, type: 'bar', height: 210, stacked: true },
    series: [{ name: 'Prompt', data: [] }, { name: 'Respuesta', data: [] }],
    xaxis: { categories: [], labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    yaxis: { labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    plotOptions: { bar: { columnWidth: '46%', borderRadius: 4 } },
    colors: ['#5B8DEF', '#183963'],
    legend: { show: true, fontSize: '10.5px', position: 'top', horizontalAlign: 'right', markers: { radius: 3 } }
  });
  tokensChart.render();

  modelsChart = new ApexCharts(document.querySelector("#chartModels"), {
    chart: { type: 'donut', height: 220, fontFamily: 'Inter, sans-serif' },
    series: [],
    labels: [],
    colors: MODEL_PALETTE,
    dataLabels: { enabled: true, style: { fontSize: '9.5px' }, dropShadow: { enabled: false } },
    legend: { show: true, fontSize: '10.5px', position: 'right', markers: { radius: 3 }, itemMargin: { vertical: 5 }, offsetY: 10 },
    stroke: { width: 2, colors: ['#fff'] },
    plotOptions: {
      pie: {
        donut: {
          size: '62%',
          labels: {
            show: true,
            name: { show: false },
            value: { show: false },
            total: {
              show: true,
              showAlways: true,
              label: '',
              fontSize: '20px',
              fontWeight: 700,
              color: '#0F2547',
              formatter: (w) => {
                const totals = w.globals.seriesTotals || [];
                const sum = totals.reduce((a, b) => a + b, 0);
                if (!sum) return '0%';
                const max = Math.max(...totals);
                return Math.round((max / sum) * 100) + '%';
              }
            }
          }
        }
      }
    },
    noData: { text: 'Aún no hay llamadas registradas', style: { fontSize: '11px', color: '#A4AFC3' } },
    tooltip: { y: { formatter: (v) => v + ' llamadas' } }
  });
  modelsChart.render();

  latencyChart = new ApexCharts(document.querySelector("#chartLatency"), {
    ...baseChartOpts,
    chart: { ...baseChartOpts.chart, type: 'line', height: 210 },
    series: [{ name: 'Proceso interno (Flask)', data: [] }, { name: 'Modelo (Azure AI Foundry)', data: [] }],
    xaxis: { categories: [], labels: { style: { fontSize: '10px', colors: '#A4AFC3' } } },
    yaxis: { labels: { style: { fontSize: '10px', colors: '#A4AFC3' }, formatter: (v) => v + 'ms' } },
    stroke: { curve: 'smooth', width: 2.5, colors: ['#F2A93B', '#0F2547'] },
    colors: ['#F2A93B', '#0F2547'],
    legend: { show: true, fontSize: '10.5px', position: 'top', horizontalAlign: 'right', markers: { radius: 3 } },
    tooltip: { y: { formatter: (v) => v + ' ms' } }
  });
  latencyChart.render();
}

function updateModelsChart() {
  const models = Object.keys(state.modelUsage);
  const colors = models.map((_, i) => MODEL_PALETTE[i % MODEL_PALETTE.length]);
  modelsChart.updateOptions({ labels: models, colors: colors });
  modelsChart.updateSeries(models.map(m => state.modelUsage[m]));
}

/* ============================================================
   Tracker de Pasos
   ============================================================ */
function resetTracker() {
  if (!trackFill) return;
  trackFill.style.width = '0%';
  trackMsg.textContent = '\u00A0';
  steps.forEach(s => s.classList.remove('active', 'done'));
}

function runTracker() {
  return new Promise(resolve => {
    resetTracker();
    trackerEl.classList.add('show');
    let i = 0;
    function next() {
      if (i > 0) { steps[i - 1].classList.remove('active'); steps[i - 1].classList.add('done'); }
      if (i >= steps.length) {
        trackFill.style.width = '100%';
        trackMsg.textContent = '✅ Solicitud completada';
        setTimeout(() => { trackerEl.classList.remove('show'); resolve(); }, 900);
        return;
      }
      steps[i].classList.add('active');
      trackMsg.textContent = STEP_MESSAGES[i];
      trackFill.style.width = ((i) / (steps.length - 1) * 100) + '%';
      i++;
      setTimeout(next, 520 + Math.random() * 260);
    }
    next();
  });
}

/* ============================================================
   Gestión de Mensajes & Métricas
   ============================================================ */
function addUserMsg(text) {
  const div = document.createElement('div');
  div.className = 'msg user';
  div.innerHTML = `<div class="bubble"></div>`;
  div.querySelector('.bubble').textContent = text;
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
}

function costTier(cost) {
  if (cost < 0.0003) return 'low';
  if (cost <= 0.001) return 'mid';
  return 'high';
}

function addBotMsg(text, meta) {
  const div = document.createElement('div');
  div.className = 'msg bot';
  const tier = costTier(meta.cost);
  div.innerHTML = `
    <div class="bubble"></div>
    <div class="cost-footer">
      <span class="chip cost ${tier}">$${meta.cost.toFixed(5)}</span>
      <span class="chip tok">🪙 ${meta.promptTok}p / ${meta.respTok}r</span>
      <span class="chip">⚡ ${meta.latencyTotal}ms (${meta.internalMs}ms proceso + ${meta.modelMs}ms Azure AI Foundry)</span>
      <span class="chip">${meta.model}</span>
    </div>`;
  div.querySelector('.bubble').textContent = text;
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
}

function addErrorMsg(text) {
  const div = document.createElement('div');
  div.className = 'msg bot';
  div.innerHTML = `<div class="bubble error">⚠️ Error: </div>`;
  div.querySelector('.bubble').textContent += text;
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
}

const INTERNAL_STAGE_NAMES = ['received', 'validating', 'preparing', 'connecting', 'agent_lookup', 'conversation', 'sent_to_frontend', 'calculating_cost'];
const MODEL_STAGE_NAMES = ['responses_api', 'waiting_model', 'response_received', 'processing_output'];

function computeLatencyBreakdown(events, totalDuration) {
  if (!Array.isArray(events) || events.length === 0) {
    return { internalMs: Math.round(totalDuration * 0.1), modelMs: Math.round(totalDuration * 0.9) };
  }
  let internalMs = 0, modelMs = 0;
  events.forEach(ev => {
    const name = ev.name || ev.stage || ev.label || '';
    const dur = ev.duration_ms ?? ev.duration ?? ev.elapsed_ms ?? 0;
    if (MODEL_STAGE_NAMES.includes(name)) modelMs += dur;
    else if (INTERNAL_STAGE_NAMES.includes(name)) internalMs += dur;
  });
  if (internalMs === 0 && modelMs === 0) {
    return { internalMs: Math.round(totalDuration * 0.1), modelMs: Math.round(totalDuration * 0.9) };
  }
  return { internalMs: Math.round(internalMs), modelMs: Math.round(modelMs) };
}

function fmtTime() {
  const d = new Date();
  return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0') + ':' + d.getSeconds().toString().padStart(2, '0');
}

function updateKPIs() {
  document.getElementById('kpiCost').textContent = '$' + state.totalCost.toFixed(4);
  document.getElementById('kpiCostSub').textContent = state.calls + ' llamadas registradas';
  document.getElementById('kpiCostTrend').textContent = state.calls ? '+' + ((state.totalCost / state.budgetLimit) * 100).toFixed(1) + '%' : '+0%';

  const totalTok = state.totalPromptTokens + state.totalRespTokens;
  document.getElementById('kpiTokens').textContent = totalTok.toLocaleString('es-CO');
  document.getElementById('kpiTokensSub').textContent = `prompt ${state.totalPromptTokens.toLocaleString('es-CO')} · respuesta ${state.totalRespTokens.toLocaleString('es-CO')}`;
  document.getElementById('kpiTokTrend').textContent = state.calls ? '+' + state.calls : '+0';

  const healthPct = state.calls ? Math.max(0, 100 - (state.errors / state.calls * 100)).toFixed(0) : 100;
  document.getElementById('kpiHealth').textContent = healthPct + '%';
  document.getElementById('kpiHealthSub').textContent = `${state.errors} errores · 0 bloqueos`;

  document.getElementById('budgetSpentTxt').textContent = '$' + state.totalCost.toFixed(4);
  document.getElementById('budgetFill').style.width = Math.min(100, (state.totalCost / state.budgetLimit * 100)) + '%';

  const statusEl = document.getElementById('connStatus');
  if (statusEl) {
    if (state.calls > 0 && state.errors < state.calls) {
      statusEl.textContent = 'Conectado a Azure AI Foundry';
    } else if (state.errors > 0 && state.errors === state.calls) {
      statusEl.textContent = 'Error de conexión a Azure';
    } else {
      statusEl.textContent = 'Backend listo · esperando actividad';
    }
  }
}

function pushChartData(entry) {
  const t = fmtTime();

  const cCost = costChart.w.config.series[0].data.concat([entry.cost]);
  const cCat = costChart.w.config.xaxis.categories.concat([t]);
  costChart.updateOptions({ xaxis: { categories: cCat.slice(-8) } });
  costChart.updateSeries([{ name: 'Costo (USD)', data: cCost.slice(-8) }]);

  const tp = tokensChart.w.config.series[0].data.concat([entry.promptTok]);
  const tr = tokensChart.w.config.series[1].data.concat([entry.respTok]);
  tokensChart.updateOptions({ xaxis: { categories: cCat.slice(-8) } });
  tokensChart.updateSeries([
    { name: 'Prompt', data: tp.slice(-8) },
    { name: 'Respuesta', data: tr.slice(-8) }
  ]);

  updateModelsChart();

  const lg = latencyChart.w.config.series[0].data.concat([entry.internalMs]);
  const lb = latencyChart.w.config.series[1].data.concat([entry.modelMs]);
  latencyChart.updateOptions({ xaxis: { categories: cCat.slice(-8) } });
  latencyChart.updateSeries([
    { name: 'Proceso interno (Flask)', data: lg.slice(-8) },
    { name: 'Modelo (Azure AI Foundry)', data: lb.slice(-8) }
  ]);
}

/* ============================================================
   Función principal de Envío de Mensaje
   ============================================================ */
async function handleSend() {
  const text = chatInput.value.trim();
  if (!text) return;

  chatInput.value = '';
  sendBtn.disabled = true;

  addUserMsg(text);

  const trackerPromise = runTracker();

  let data;
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });

    if (!res.ok) {
      let errMsg = `Error ${res.status} del servidor`;
      try {
        const errBody = await res.json();
        errMsg = errBody.error || errBody.message || errMsg;
      } catch (_) {}
      throw new Error(errMsg);
    }

    data = await res.json();
  } catch (err) {
    await trackerPromise;
    trackerEl.classList.remove('show');
    state.errors += 1;
    addErrorMsg(err.message || 'No se pudo conectar con el servidor.');
    updateKPIs();
    sendBtn.disabled = false;
    chatInput.focus();
    return;
  }

  await trackerPromise;

  const usage = data.usage || {};
  const cost = Number(data.cost?.cost?.total) || 0;
  const promptTok = Number(usage.input_tokens) || 0;
  const respTok = Number(usage.output_tokens) || 0;
  const latencyTotal = Number(data.total_duration_ms) || 0;

  const { internalMs, modelMs } = computeLatencyBreakdown(data.events, latencyTotal);

  const model = usage.model || data.model || 'modelo-desconocido';
  const reply = data.reply || data.response || data.text || '(Respuesta vacía)';

  addBotMsg(reply, { cost, promptTok, respTok, latencyTotal, internalMs, modelMs, model });

  loadPanelOperativo();
  refreshVisibleDataModule();

  sendBtn.disabled = false;
  chatInput.focus();
}

/* ============================================================
   Refresca el módulo de datos actualmente visible tras un chat
   ============================================================ */
function refreshVisibleDataModule() {
  const visible = document.querySelector('.module-view.module-placeholder:not([style*="display: none"])');
  if (!visible) return;
  const target = visible.getAttribute('data-module');
  const loader = MODULE_LOADERS[target];
  if (loader && !STATIC_MODULES.has(target)) {
    loader();
  }
}