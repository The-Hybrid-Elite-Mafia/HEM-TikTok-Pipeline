const DEFAULT_QUEUE_URL =
  "https://raw.githubusercontent.com/The-Hybrid-Elite-Mafia/HEM-TikTok-Pipeline/main/queue/queue.json";

const $ = (id) => document.getElementById(id);

async function load() {
  const s = await chrome.storage.local.get(["enabled", "mode", "queueUrl", "status", "log"]);
  $("enabled").checked = s.enabled ?? false;
  $("mode").value = s.mode === "auto" ? "auto" : "review";
  $("queueUrl").value = s.queueUrl || DEFAULT_QUEUE_URL;
  $("log").textContent = (s.log || []).slice(-40).reverse().join("\n") || "No activity yet.";
  await showQueue(s.queueUrl || DEFAULT_QUEUE_URL, s.status || {});
  const r = await chrome.runtime.sendMessage({ type: "is-running" }).catch(() => null);
  $("run").disabled = !!r?.running;
  $("run").textContent = r?.running ? "Working…" : "Run now";
}

async function showQueue(url, status) {
  const box = $("queue");
  try {
    const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const items = Array.isArray(data) ? data : data.items || [];
    if (!items.length) { box.textContent = "Queue is empty."; return; }
    box.textContent = "";
    for (const it of items) {
      const st = status[it.id]?.state || "waiting";
      const when = new Date(it.postAt).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      const row = document.createElement("div");
      row.className = "item";
      const label = document.createElement("span");
      label.className = `state ${st}`;
      label.textContent = st.toUpperCase();
      row.append(label, ` ${when} — ${it.caption.slice(0, 48)}`);
      if (status[it.id]?.detail && st === "failed") row.title = status[it.id].detail;
      box.append(row);
    }
  } catch (e) {
    box.textContent = `Could not read the queue (${e.message}).`;
  }
}

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    enabled: $("enabled").checked,
    mode: $("mode").value,
    queueUrl: $("queueUrl").value.trim() || DEFAULT_QUEUE_URL,
  });
  $("save").textContent = "Saved";
  setTimeout(() => ($("save").textContent = "Save"), 1200);
});

$("run").addEventListener("click", async () => {
  await chrome.storage.local.set({ mode: $("mode").value, queueUrl: $("queueUrl").value.trim() || DEFAULT_QUEUE_URL });
  await chrome.runtime.sendMessage({ type: "run-now" });
  $("run").disabled = true;
  $("run").textContent = "Working…";
});

$("reset").addEventListener("click", async () => {
  const { status = {} } = await chrome.storage.local.get(["status"]);
  for (const id of Object.keys(status)) {
    if (status[id].state !== "scheduled") delete status[id];
  }
  await chrome.storage.local.set({ status });
  await load();
});

chrome.storage.onChanged.addListener(() => load());
load();
