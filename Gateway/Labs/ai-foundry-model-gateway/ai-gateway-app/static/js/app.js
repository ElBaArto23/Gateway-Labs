const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const sendSpinner = sendBtn.querySelector(".send-spinner");
const statusDot = document.getElementById("status-dot");
const statusLabel = document.getElementById("status-label");

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function addMessage(role, text, meta) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg msg-${role}`;
  const p = document.createElement("p");
  p.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
  wrapper.appendChild(p);
  if (meta) {
    const metaEl = document.createElement("span");
    metaEl.className = "msg-meta";
    metaEl.innerHTML = meta;
    wrapper.appendChild(metaEl);
  }
  chatLog.appendChild(wrapper);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function setLoading(isLoading) {
  sendBtn.disabled = isLoading;
  chatInput.disabled = isLoading;
  sendSpinner.classList.toggle("active", isLoading);
}

async function sendMessage(message) {
  addMessage("user", message);
  setLoading(true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();

    if (!res.ok) {
      addMessage("error", data.error || "Ocurrió un error consultando el gateway.");
    } else {
      const tokenBadge = data.tokens_were_real
        ? `${data.usage.total_tokens} tokens reales`
        : `⚠️ el gateway no devolvió "usage" — 0 tokens reportados`;
      const meta = `#${data.request_id} · ${data.model} · ${tokenBadge} · ${data.latency_ms.toFixed ? data.latency_ms.toFixed(1) : data.latency_ms} ms · $${data.cost.toFixed(6)} · <em>registrado en tu dashboard</em>`;
      addMessage("assistant", data.reply, meta);
    }
  } catch (err) {
    addMessage("error", "No se pudo conectar con el backend local.");
  } finally {
    setLoading(false);
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  chatInput.value = "";
  sendMessage(message);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

document.querySelectorAll(".quick-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    chatInput.value = btn.dataset.prompt;
    chatForm.requestSubmit();
  });
});

async function checkGatewayHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.apim_configured) {
      statusDot.classList.add("ok");
      statusLabel.textContent = `Conectado · ${data.model}`;
    } else {
      statusDot.classList.add("error");
      statusLabel.textContent = "APIM no configurado";
    }
  } catch (err) {
    statusDot.classList.add("error");
    statusLabel.textContent = "Backend no disponible";
  }
}

checkGatewayHealth();
