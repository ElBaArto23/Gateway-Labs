import { useState, useMemo, useRef } from "react";
import {
  LayoutDashboard, Network, PlayCircle, BarChart3, AlertTriangle, Settings,
  DollarSign, Clock, Activity, CalendarClock, CheckCircle2, XCircle,
  StopCircle, ChevronRight, Info, Radio
} from "lucide-react";
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Cell, PieChart, Pie, Legend
} from "recharts";
import logo from "./assets/logo.png";

// ---- Configuración fija del lab (backend-pool-load-balancing) ----
const BACKENDS = [
  { id: "foundry1", location: "eastus", region: "East US", priority: 1, weight: null },
  { id: "foundry2", location: "swedencentral", region: "Sweden Central", priority: 2, weight: 50 },
  { id: "foundry3", location: "westus", region: "West US", priority: 2, weight: 50 },
  { id: "foundry4", location: "uksouth", region: "UK South", priority: 3, weight: null },
];

const PRIORITY_COLOR = {
  1: { hex: "#2a78d6", tone: "blue" },
  2: { hex: "#eb6834", tone: "orange" },
  3: { hex: "#1baf7a", tone: "aqua" },
};

const CHART_COLORS = {
  grid: "#e1e0d9",
  axis: "#898781",
  ink: "#0b0b0b",
  border: "rgba(11,11,11,0.10)",
  accent: "#4f46e5",
};

const NAV = [
  { id: "operativo", label: "Panel Operativo", icon: LayoutDashboard },
  { section: "Pool" },
  { id: "arquitectura", label: "Arquitectura del Pool", icon: Network },
  { id: "metricas", label: "Métricas", icon: BarChart3 },
  { id: "eventos", label: "Eventos", icon: AlertTriangle },
  { section: "Herramientas" },
  { id: "prueba", label: "Prueba en vivo", icon: PlayCircle },
  { section: "Sistema" },
  { id: "configuracion", label: "Configuración", icon: Settings },
];

const VIEW_META = {
  operativo: { title: "Panel Operativo", sub: "Estado del pool en esta sesión de pruebas" },
  arquitectura: { title: "Arquitectura del Pool", sub: "Cascada de prioridad — cómo decide APIM a qué backend enrutar" },
  prueba: { title: "Prueba en vivo", sub: "Configura y lanza una corrida de llamadas contra el pool" },
  metricas: { title: "Métricas", sub: "Distribución de tráfico, latencia y códigos de respuesta" },
  eventos: { title: "Eventos", sub: "Errores y respuestas no exitosas de esta sesión" },
  configuracion: { title: "Configuración", sub: "Conexión a APIM y tarifas para el costo estimado" },
};

function regionFor(header) {
  if (!header) return "Desconocida";
  const match = BACKENDS.find(
    (b) => b.region.toLowerCase() === header.toLowerCase()
  );
  return match ? match.region : header;
}

function fmtMoney(n) {
  if (n === undefined || n === null || Number.isNaN(n)) return "$0.000000";
  return `$${n.toFixed(6)}`;
}

function fmtMs(n) {
  if (n === undefined || n === null) return "—";
  return `${Math.round(n)} ms`;
}

export default function BackendPoolDashboard() {
  const [view, setView] = useState("operativo");
  const [config, setConfig] = useState({
    gatewayUrl: "https://apim-shared-pdcibwky2f5ms.azure-api.net",
    path: "backend-pool-inference",
    apiVersion: "2025-03-01-preview",
    model: "gpt-5-mini",
    apiKey: "",
    runs: 20,
    delayMs: 300,
    systemPrompt: "You are a sarcastic, unhelpful assistant.",
    userPrompt: "Can you tell me the time, please?",
    promptRate: 0.00025,
    completionRate: 0.002,
  });
  const [logs, setLogs] = useState([]);
  const [running, setRunning] = useState(false);
  const [banner, setBanner] = useState(null);
  const stopRef = useRef(false);

  const isConfigured = config.gatewayUrl && config.apiKey && config.model;

  // ---- Derived stats ----
  const stats = useMemo(() => {
    const totalCost = logs.reduce((s, l) => s + (l.cost || 0), 0);
    const totalTokens = logs.reduce((s, l) => s + (l.tokens?.total || 0), 0);
    const okLogs = logs.filter((l) => l.status === 200);
    const avgLatency = okLogs.length
      ? okLogs.reduce((s, l) => s + (l.latencyMs || 0), 0) / okLogs.length
      : 0;
    const errorCount = logs.filter((l) => l.status !== 200).length;
    const usedRegions = new Set(logs.filter((l) => l.status === 200).map((l) => l.region));
    const perBackend = BACKENDS.map((b) => {
      const calls = logs.filter((l) => l.region === b.region);
      const ok = calls.filter((l) => l.status === 200);
      const avg = ok.length ? ok.reduce((s, l) => s + l.latencyMs, 0) / ok.length : null;
      const last = calls[calls.length - 1] || null;
      return { ...b, calls: calls.length, avgLatency: avg, last };
    });
    return { totalCost, totalTokens, avgLatency, errorCount, usedRegions, perBackend };
  }, [logs]);

  const distributionData = stats.perBackend
    .filter((b) => b.calls > 0)
    .map((b) => ({ name: b.region, llamadas: b.calls, fill: PRIORITY_COLOR[b.priority].hex }));

  const latencyData = logs.map((l) => ({
    run: l.index,
    latencia: l.status === 200 ? l.latencyMs : null,
    region: l.region,
  }));

  const statusData = useMemo(() => {
    const map = {};
    logs.forEach((l) => {
      const key = l.status === 200 ? "200 OK" : l.status === "ERR" ? "Error de red" : `HTTP ${l.status}`;
      map[key] = (map[key] || 0) + 1;
    });
    return Object.entries(map).map(([name, value]) => ({ name, value }));
  }, [logs]);

  const gatewayStatus = useMemo(() => {
    if (!isConfigured) {
      return { cls: "critical", label: "Falta configuración", sub: "Completa la conexión en Configuración" };
    }
    if (stats.errorCount > 0) {
      return { cls: "warning", label: "Pool con incidencias", sub: `${stats.errorCount} llamada(s) con error` };
    }
    if (logs.length > 0) {
      return { cls: "", label: "Pool operativo", sub: `${stats.usedRegions.size}/${BACKENDS.length} backends activos` };
    }
    return { cls: "", label: "Gateway configurado", sub: "Listo para probar" };
  }, [isConfigured, stats, logs.length]);

  // ---- Run logic ----
  async function runTest() {
    if (!isConfigured) {
      setBanner({ type: "error", text: "Completa la URL del gateway, la API key y el modelo en Configuración." });
      setView("configuracion");
      return;
    }
    setRunning(true);
    stopRef.current = false;
    setBanner(null);
    const url = `${config.gatewayUrl.replace(/\/$/, "")}/${config.path}/openai/deployments/${config.model}/chat/completions?api-version=${config.apiVersion}`;

    for (let i = 0; i < config.runs; i++) {
      if (stopRef.current) break;
      const start = performance.now();
      const entry = {
        id: `${Date.now()}-${i}`,
        index: i + 1,
        time: new Date().toLocaleTimeString(),
        status: null,
        region: null,
        latencyMs: null,
        tokens: { prompt: 0, completion: 0, total: 0 },
        cost: 0,
        error: null,
        reply: null,
      };
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "api-key": config.apiKey },
          body: JSON.stringify({
            messages: [
              { role: "system", content: config.systemPrompt },
              { role: "user", content: config.userPrompt },
            ],
          }),
        });
        entry.latencyMs = performance.now() - start;
        entry.status = res.status;
        entry.region = regionFor(res.headers.get("x-ms-region"));
        const data = await res.json().catch(() => null);
        if (res.ok && data) {
          const usage = data.usage || {};
          entry.tokens = {
            prompt: usage.prompt_tokens || 0,
            completion: usage.completion_tokens || 0,
            total: usage.total_tokens || 0,
          };
          entry.cost =
            (entry.tokens.prompt / 1000) * config.promptRate +
            (entry.tokens.completion / 1000) * config.completionRate;
          entry.reply = data.choices?.[0]?.message?.content?.slice(0, 160) || null;
        } else {
          entry.error = data?.error?.message || `HTTP ${res.status}`;
        }
      } catch (e) {
        entry.status = "ERR";
        entry.latencyMs = performance.now() - start;
        entry.error = e.message;
        setBanner({
          type: "error",
          text: "No se pudo conectar directo desde el navegador (probable bloqueo CORS del gateway compartido). Habilita CORS en la política de la API o usa un proxy propio.",
        });
      }
      setLogs((prev) => [...prev, entry]);
      if (i < config.runs - 1) await new Promise((r) => setTimeout(r, config.delayMs));
    }
    setRunning(false);
  }

  function stopTest() {
    stopRef.current = true;
    setRunning(false);
  }

  function clearLogs() {
    setLogs([]);
    setBanner(null);
  }

  const meta = VIEW_META[view];

  return (
    <div className="flex min-h-screen w-full">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo">
            <img src={logo} alt="" />
          </div>
          <div className="brand-text">
            <div className="t1">Controles</div>
            <div className="t2">Empresariales</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map((item, idx) =>
            item.section ? (
              <div key={`s-${idx}`} className="nav-section-label">{item.section}</div>
            ) : (
              <button
                key={item.id}
                onClick={() => setView(item.id)}
                className={`nav-item ${view === item.id ? "active" : ""}`}
              >
                <item.icon className="nav-icon" />
                <span>{item.label}</span>
              </button>
            )
          )}
        </nav>

        <div className={`sidebar-status ${isConfigured ? "ok" : "warn"}`}>
          <span className="dot" style={{ width: 6, height: 6, borderRadius: "50%", background: "currentColor" }} />
          {isConfigured ? "Conectado a APIM" : "Falta configuración"}
        </div>

        <div className="sidebar-footer">
          Transformamos ideas en<br />soluciones <span className="accent">seguras</span>
        </div>
      </aside>

      {/* Main */}
      <main className="main-area">
        <div className="topbar">
          <div className="topbar-left">
            <div className="topbar-icon">
              <Network />
            </div>
            <div>
              <div className="topbar-title-row">
                <h1 className="topbar-title">Backend Pool LB</h1>
                <span className="model-pill">{config.model}</span>
              </div>
              <p className="topbar-subtitle">Priority 1 (PTU) con fallback a priority 2/3 · Azure APIM</p>
            </div>
          </div>
          <div className="topbar-right">
            <div>
              <span className={`gateway-badge ${gatewayStatus.cls}`}>
                <span className="dot" />
                {gatewayStatus.label}
              </span>
              <span className="gateway-badge-sub">{gatewayStatus.sub}</span>
            </div>
            {running ? (
              <button onClick={stopTest} className="btn danger">
                <StopCircle /> Detener
              </button>
            ) : (
              <button onClick={runTest} className="btn primary">
                <PlayCircle /> Ejecutar {config.runs} llamadas
              </button>
            )}
          </div>
        </div>

        <div className="page-pad">
          {banner && (
            <div className="banner warning">
              <AlertTriangle />
              <span>{banner.text}</span>
            </div>
          )}

          <h1 className="page-title">{meta.title}</h1>
          <p className="page-subtitle">{meta.sub}</p>

          {view === "operativo" && (
            <OperativoView stats={stats} logs={logs} running={running} />
          )}
          {view === "arquitectura" && <ArquitecturaView stats={stats} />}
          {view === "prueba" && (
            <PruebaView
              config={config}
              setConfig={setConfig}
              logs={logs}
              running={running}
              clearLogs={clearLogs}
            />
          )}
          {view === "metricas" && (
            <MetricasView distributionData={distributionData} latencyData={latencyData} statusData={statusData} />
          )}
          {view === "eventos" && <EventosView logs={logs} />}
          {view === "configuracion" && <ConfiguracionView config={config} setConfig={setConfig} />}
        </div>
      </main>
    </div>
  );
}

// ---------------- Sub-views ----------------

function Card({ children, className = "" }) {
  return <div className={`panel ${className}`}>{children}</div>;
}

function MetricCard({ icon: Icon, label, value, sub, tone = "blue", footTone = "" }) {
  return (
    <div className="tile">
      <div className="tile-top">
        <div className={`tile-icon tone-${tone}`}>
          <Icon />
        </div>
        <div className="tile-label">{label}</div>
      </div>
      <div className="tile-value">{value}</div>
      {sub && <div className={`tile-foot ${footTone}`}>{sub}</div>}
    </div>
  );
}

function OperativoView({ stats, logs, running }) {
  const recent = [...logs].slice(-6).reverse();
  return (
    <div>
      <div className="tiles">
        <MetricCard icon={DollarSign} label="Costo acumulado (estimado)" value={fmtMoney(stats.totalCost)} sub={`${stats.totalTokens} tokens totales`} tone="emerald" />
        <MetricCard icon={Clock} label="Latencia media" value={fmtMs(stats.avgLatency)} sub="Solo llamadas 200 OK" tone="blue" />
        <MetricCard
          icon={Activity}
          label="Salud del pool"
          value    ={`${stats.usedRegions.size}/${BACKENDS.length}`}
          sub="Backends que respondieron"
          tone={stats.usedRegions.size === BACKENDS.length ? "emerald" : "amber"}
          footTone={stats.usedRegions.size === BACKENDS.length ? "good" : ""}
        />
        <MetricCard
          icon={CalendarClock}
          label="Llamadas realizadas"
          value={logs.length}
          sub={`${stats.errorCount} con error/fallback`}
          tone={stats.errorCount > 0 ? "amber" : "blue"}
          footTone={stats.errorCount > 0 ? "critical" : ""}
        />
      </div>

      <div className="grid grid-cols-3 gap-6">
        <Card className="col-span-2">
          <div className="panel-header">
            <p className="panel-title">Últimas llamadas</p>
            {running && (
              <span style={{ fontSize: 12, color: CHART_COLORS.accent, display: "flex", alignItems: "center", gap: 4 }}>
                <Radio className="animate-pulse" style={{ width: 12, height: 12 }} /> en curso…
              </span>
            )}
          </div>
          {recent.length === 0 ? (
            <EmptyState text='Todavía no hay llamadas. Ve a "Prueba en vivo" y ejecuta el test contra el pool.' />
          ) : (
            <LogTable rows={recent} compact />
          )}
        </Card>

        <Card>
          <p className="panel-title" style={{ marginBottom: 14 }}>Pool de backends</p>
          <div className="space-y-3">
            {[1, 2, 3].map((p) => {
              const inTier = BACKENDS.filter((b) => b.priority === p);
              const color = PRIORITY_COLOR[p];
              return (
                <div key={p} className="flex items-center gap-3">
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: color.hex, flexShrink: 0 }} />
                  <span className="text-muted" style={{ fontSize: 12, width: 76 }}>Prioridad {p}</span>
                  <span className="text-secondary" style={{ fontSize: 12 }}>{inTier.map((b) => b.region).join(" · ")}</span>
                </div>
              );
            })}
          </div>
          <p className="hint">
            Cuando priority 1 se satura (429), la política de retry de APIM reintenta contra el pool y
            la petición pasa a priority 2/3 sin que el cliente lo note.
          </p>
        </Card>
      </div>
    </div>
  );
}

function ArquitecturaView({ stats }) {
  const tiers = [1, 2, 3];
  return (
    <div className="space-y-6">
      <Card>
        <div className="flex items-center gap-2" style={{ marginBottom: 4 }}>
          <Network style={{ width: 16, height: 16, color: CHART_COLORS.accent }} />
          <p className="panel-title">Cascada de prioridad</p>
        </div>
        <p className="hint" style={{ marginTop: 0, marginBottom: 20 }}>
          El cliente siempre apunta al mismo endpoint del backend pool. APIM decide internamente a qué
          backend enruta según prioridad, peso y disponibilidad.
        </p>

        <div className="flex items-start gap-4" style={{ overflowX: "auto", paddingBottom: 8 }}>
          {tiers.map((p, idx) => {
            const color = PRIORITY_COLOR[p];
            const inTier = stats.perBackend.filter((b) => b.priority === p);
            return (
              <div key={p} className="flex items-center gap-4">
                <div className="flex flex-col gap-3">
                  <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".04em", color: color.hex }}>
                    Prioridad {p} {p === 1 ? "(PTU)" : "(fallback)"}
                  </div>
                  {inTier.map((b) => (
                    <div
                      key={b.id}
                      className="panel"
                      style={{
                        width: 224,
                        padding: "12px 16px",
                        boxShadow: b.calls > 0 ? `0 0 0 1px ${color.hex}66` : "none",
                      }}
                    >
                      <div className="flex items-center justify-between">
                        <span style={{ fontSize: 13.5, fontWeight: 600 }}>{b.region}</span>
                        <span style={{ width: 8, height: 8, borderRadius: "50%", background: b.calls > 0 ? color.hex : "#e1e0d9" }} />
                      </div>
                      <div className="text-muted" style={{ fontSize: 11, marginTop: 4 }}>
                        {b.weight ? `peso ${b.weight}%` : "sin peso (única en su nivel)"}
                      </div>
                      <div className="flex items-center justify-between" style={{ marginTop: 10, fontSize: 12 }}>
                        <span className="text-muted">{b.calls} llamadas</span>
                        <span className="text-primary" style={{ fontFamily: "ui-monospace, monospace" }}>{fmtMs(b.avgLatency)}</span>
                      </div>
                    </div>
                  ))}
                </div>
                {idx < tiers.length - 1 && <ChevronRight style={{ width: 18, height: 18, color: "#c3c2b7", marginTop: 24 }} />}
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <div className="flex items-center gap-2" style={{ marginBottom: 6 }}>
          <Info style={{ width: 15, height: 15, color: "var(--text-muted)" }} />
          <p className="panel-title">Cómo leer esto</p>
        </div>
        <p className="hint" style={{ marginTop: 0 }}>
          Si ves tráfico solo en priority 1, el backend PTU todavía tiene capacidad. En cuanto se satura
          (capacidad configurada baja a propósito en el lab), empezarás a ver llamadas repartidas ~50/50
          entre los dos backends de priority 2. Priority 3 solo entra si también priority 2 se agota.
        </p>
      </Card>
    </div>
  );
}

function PruebaView({ config, setConfig, logs, running, clearLogs }) {
  return (
    <div className="grid grid-cols-3 gap-6">
      <Card>
        <p className="panel-title" style={{ marginBottom: 16 }}>Parámetros de la corrida</p>
        <div className="space-y-4">
          <Field label="Número de llamadas">
            <input
              type="number"
              min={1}
              max={100}
              value={config.runs}
              onChange={(e) => setConfig({ ...config, runs: Number(e.target.value) })}
            />
          </Field>
          <Field label="Pausa entre llamadas (ms)">
            <input
              type="number"
              min={0}
              value={config.delayMs}
              onChange={(e) => setConfig({ ...config, delayMs: Number(e.target.value) })}
            />
          </Field>
          <Field label="Prompt del sistema">
            <textarea
              rows={2}
              value={config.systemPrompt}
              onChange={(e) => setConfig({ ...config, systemPrompt: e.target.value })}
            />
          </Field>
          <Field label="Prompt del usuario">
            <textarea
              rows={2}
              value={config.userPrompt}
              onChange={(e) => setConfig({ ...config, userPrompt: e.target.value })}
            />
          </Field>
          <button
            onClick={clearLogs}
            disabled={running || logs.length === 0}
            className="btn link"
          >
            Limpiar historial ({logs.length})
          </button>
        </div>
      </Card>

      <Card className="col-span-2">
        <div className="panel-header">
          <p className="panel-title">Log en vivo</p>
          <span className="panel-note">{logs.length} / {config.runs} llamadas</span>
        </div>
        {logs.length === 0 ? (
          <EmptyState text='Pulsa "Ejecutar" arriba para lanzar la corrida contra el backend pool.' />
        ) : (
          <LogTable rows={[...logs].reverse()} />
        )}
      </Card>
    </div>
  );
}

function MetricasView({ distributionData, latencyData, statusData }) {
  const pieColors = ["#2a78d6", "#eb6834", "#1baf7a", "#d03b3b", "#898781"];
  const tooltipStyle = { background: "#fff", border: `1px solid ${CHART_COLORS.border}`, fontSize: 12, borderRadius: 8 };
  return (
    <div className="grid grid-cols-2 gap-6">
      <Card>
        <p className="panel-title">Distribución por backend</p>
        <p className="hint" style={{ marginTop: 2, marginBottom: 16 }}>Cuántas llamadas atendió cada región (evidencia del failover)</p>
        <div style={{ height: 256 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={distributionData}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} vertical={false} />
              <XAxis dataKey="name" stroke={CHART_COLORS.axis} fontSize={11} />
              <YAxis stroke={CHART_COLORS.axis} fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="llamadas" radius={[4, 4, 0, 0]}>
                {distributionData.map((d, i) => (
                  <Cell key={i} fill={d.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <p className="panel-title">Latencia por corrida</p>
        <p className="hint" style={{ marginTop: 2, marginBottom: 16 }}>Tiempo de respuesta observado en cada llamada</p>
        <div style={{ height: 256 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={latencyData}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} vertical={false} />
              <XAxis dataKey="run" stroke={CHART_COLORS.axis} fontSize={11} />
              <YAxis stroke={CHART_COLORS.axis} fontSize={11} />
              <Tooltip contentStyle={tooltipStyle} />
              <Line type="monotone" dataKey="latencia" stroke={CHART_COLORS.accent} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <p className="panel-title">Códigos de respuesta</p>
        <p className="hint" style={{ marginTop: 2, marginBottom: 16 }}>200 esperado; 503 = pool agotado</p>
        <div style={{ height: 256 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={statusData} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={3}>
                {statusData.map((_, i) => (
                  <Cell key={i} fill={pieColors[i % pieColors.length]} />
                ))}
              </Pie>
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Tooltip contentStyle={tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <p className="panel-title" style={{ marginBottom: 14 }}>Resumen por backend</p>
        <table className="data-table">
          <thead>
            <tr>
              <th>Región</th>
              <th>Prioridad</th>
              <th className="num">Llamadas</th>
              <th className="num">Latencia media</th>
            </tr>
          </thead>
          <tbody>
            {distributionData.map((d, i) => (
              <tr key={i}>
                <td>{d.name}</td>
                <td>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", display: "inline-block", background: d.fill }} />
                </td>
                <td className="num">{d.llamadas}</td>
                <td className="num">—</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function EventosView({ logs }) {
  const events = logs.filter((l) => l.status !== 200);
  return (
    <Card>
      <p className="panel-title">Errores y respuestas no exitosas</p>
      <p className="hint" style={{ marginTop: 2, marginBottom: 16 }}>
        Los 429 individuales quedan absorbidos por el retry de APIM y no llegan aquí. Solo verás algo si
        <b> todo</b> el pool está agotado (503) o si hay un problema de red/CORS.
      </p>
      {events.length === 0 ? (
        <EmptyState text="Sin errores registrados en esta sesión." icon={CheckCircle2} tone="ok" />
      ) : (
        <div className="space-y-2">
          {events.map((e) => (
            <div
              key={e.id}
              className="flex items-start gap-3"
              style={{ borderRadius: 10, border: "1px solid rgba(208,59,59,0.25)", background: "#fdeaea", padding: "10px 14px" }}
            >
              <XCircle style={{ width: 15, height: 15, color: "var(--status-critical)", marginTop: 2, flexShrink: 0 }} />
              <div style={{ fontSize: 12.5 }}>
                <div className="text-secondary">
                  Llamada #{e.index} · {e.time} · <span style={{ fontFamily: "ui-monospace, monospace" }}>{e.status}</span>
                </div>
                <div className="text-muted" style={{ marginTop: 2 }}>{e.error || "Sin detalle adicional"}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function ConfiguracionView({ config, setConfig }) {
  return (
    <div className="grid grid-cols-2 gap-6" style={{ maxWidth: 900 }}>
      <Card>
        <p className="panel-title" style={{ marginBottom: 16 }}>Conexión a APIM</p>
        <div className="space-y-4">
          <Field label="Gateway URL">
            <input
              value={config.gatewayUrl}
              onChange={(e) => setConfig({ ...config, gatewayUrl: e.target.value })}
              placeholder="https://apim-shared-xxxx.azure-api.net"
            />
          </Field>
          <Field label="Path de la API (inferenceAPIPath)">
            <input
              value={config.path}
              onChange={(e) => setConfig({ ...config, path: e.target.value })}
            />
          </Field>
          <Field label="API version">
            <input
              value={config.apiVersion}
              onChange={(e) => setConfig({ ...config, apiVersion: e.target.value })}
            />
          </Field>
          <Field label="Modelo (deployment name)">
            <input
              value={config.model}
              onChange={(e) => setConfig({ ...config, model: e.target.value })}
            />
          </Field>
          <Field label="Subscription key (api-key)">
            <input
              type="password"
              value={config.apiKey}
              onChange={(e) => setConfig({ ...config, apiKey: e.target.value })}
              placeholder="pega aquí el shared-subscription key"
            />
          </Field>
        </div>
      </Card>

      <Card>
        <p className="panel-title" style={{ marginBottom: 8 }}>Tarifas para el costo estimado</p>
        <p className="hint" style={{ marginTop: 0, marginBottom: 16 }}>
          El costo mostrado en el panel es estimado con estas tarifas por 1K tokens — ajústalas a tu
          contrato real si las conoces.
        </p>
        <div className="space-y-4">
          <Field label="USD por 1K tokens de prompt">
            <input
              type="number"
              step="0.00001"
              value={config.promptRate}
              onChange={(e) => setConfig({ ...config, promptRate: Number(e.target.value) })}
            />
          </Field>
          <Field label="USD por 1K tokens de respuesta">
            <input
              type="number"
              step="0.00001"
              value={config.completionRate}
              onChange={(e) => setConfig({ ...config, completionRate: Number(e.target.value) })}
            />
          </Field>
        </div>

        <div className="flex items-start gap-3 panel" style={{ marginTop: 20, background: "var(--page)" }}>
          <Info style={{ width: 15, height: 15, color: "var(--text-muted)", marginTop: 2, flexShrink: 0 }} />
          <p className="hint" style={{ marginTop: 0 }}>
            La API key queda solo en memoria del navegador durante esta sesión. Si el gateway compartido no
            tiene CORS habilitado para este origen, las llamadas directas desde el navegador fallarán —
            en ese caso necesitarás un proxy propio.
          </p>
        </div>
      </Card>
    </div>
  );
}

// ---------------- Small helpers ----------------

function Field({ label, children }) {
  return (
    <label className="field block">
      <span style={{ display: "block", fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 5 }}>{label}</span>
      {children}
    </label>
  );
}

function EmptyState({ text, icon: Icon = Info, tone = "muted" }) {
  return (
    <div className="empty-state" style={tone === "ok" ? { color: "var(--status-good)" } : {}}>
      <Icon style={tone === "ok" ? { color: "var(--status-good)" } : {}} />
      <p style={{ maxWidth: 320, margin: 0 }}>{text}</p>
    </div>
  );
}

function StatusBadge({ status }) {
  if (status === 200)
    return (
      <span className="status-badge ok">
        <CheckCircle2 style={{ width: 12, height: 12 }} /> 200
      </span>
    );
  if (status === "ERR")
    return (
      <span className="status-badge error">
        <XCircle style={{ width: 12, height: 12 }} /> ERR
      </span>
    );
  return (
    <span className="status-badge quota">
      <AlertTriangle style={{ width: 12, height: 12 }} /> {status}
    </span>
  );
}

function LogTable({ rows, compact = false }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Hora</th>
            <th>Backend</th>
            <th>Status</th>
            <th className="num">Latencia</th>
            <th className="num">Tokens</th>
            <th className="num">Costo</th>
            {!compact && <th>Respuesta</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const p = BACKENDS.find((b) => b.region === r.region)?.priority;
            const color = p ? PRIORITY_COLOR[p] : null;
            return (
              <tr key={r.id}>
                <td style={{ fontFamily: "ui-monospace, monospace", color: "var(--text-muted)" }}>{r.index}</td>
                <td className="text-muted">{r.time}</td>
                <td>
                  {r.region ? (
                    <span className="inline-flex items-center gap-1.5">
                      <span style={{ width: 6, height: 6, borderRadius: "50%", display: "inline-block", background: color ? color.hex : "#c3c2b7" }} />
                      {r.region}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td><StatusBadge status={r.status} /></td>
                <td className="num">{fmtMs(r.latencyMs)}</td>
                <td className="num">{r.tokens?.total || "—"}</td>
                <td className="num">{r.cost ? fmtMoney(r.cost) : "—"}</td>
                {!compact && (
                  <td className="text-muted" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.reply || r.error || "—"}</td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
