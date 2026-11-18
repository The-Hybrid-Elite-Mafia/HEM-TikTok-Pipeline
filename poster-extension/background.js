// HEM TikTok Poster — background service worker.
// Reads queue/queue.json from the HEM-TikTok-Pipeline repo, loads each video
// into TikTok Studio, types the caption, sets the schedule date/time and
// (in "auto" mode) presses Schedule. One video at a time.

const DEFAULT_QUEUE_URL =
  "https://raw.githubusercontent.com/The-Hybrid-Elite-Mafia/HEM-TikTok-Pipeline/main/queue/queue.json";
const UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload";
const ALARM = "hem-check";
const CHECK_EVERY_MIN = 60;
const MIN_LEAD_MS = 20 * 60 * 1000;            // TikTok needs >= 15 min; keep a margin
const MAX_LEAD_MS = 10 * 24 * 3600 * 1000 - 3600 * 1000; // TikTok max is 10 days
const MAX_PER_RUN = 10;
const LOG_KEEP = 150;

let running = false;

// ---------------------------------------------------------------- settings
async function getSettings() {
  const s = await chrome.storage.local.get(["enabled", "mode", "queueUrl"]);
  return {
    enabled: s.enabled ?? false,
    mode: s.mode === "auto" ? "auto" : "review",
    queueUrl: (s.queueUrl || DEFAULT_QUEUE_URL).trim(),
  };
}

async function getStatus() {
  const s = await chrome.storage.local.get(["status"]);
  return s.status || {};
}

async function setItemStatus(id, state, detail = "") {
  const status = await getStatus();
  status[id] = { state, detail, at: new Date().toISOString() };
  await chrome.storage.local.set({ status });
}

async function log(msg) {
  const line = `${new Date().toLocaleString()} — ${msg}`;
  console.log("[HEM]", line);
  const s = await chrome.storage.local.get(["log"]);
  const list = s.log || [];
  list.push(line);
  await chrome.storage.local.set({ log: list.slice(-LOG_KEEP) });
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icon128.png",
    title,
    message: message.slice(0, 250),
  });
}

// ---------------------------------------------------------------- schedule
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(ALARM, { periodInMinutes: CHECK_EVERY_MIN, delayInMinutes: 2 });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM, { periodInMinutes: CHECK_EVERY_MIN, delayInMinutes: 2 });
});
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === ALARM) runQueue("timer").catch((e) => log(`Run crashed: ${e.message}`));
});
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type === "run-now") {
    runQueue("manual", true).catch((e) => log(`Run crashed: ${e.message}`));
    reply({ ok: true });
  } else if (msg?.type === "is-running") {
    reply({ running });
  }
  return false;
});

// ---------------------------------------------------------------- queue
async function fetchQueue(url) {
  const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Queue download failed: HTTP ${res.status}`);
  const data = await res.json();
  const items = Array.isArray(data) ? data : data.items;
  if (!Array.isArray(items)) throw new Error("queue.json has no items list");
  return items;
}

function videoUrlFor(queueUrl, video) {
  if (/^https:\/\//.test(video)) return video;
  return new URL(video, queueUrl).href;
}

function validItem(it) {
  return it && typeof it.id === "string" && typeof it.video === "string" &&
    typeof it.caption === "string" && typeof it.postAt === "string" &&
    !Number.isNaN(Date.parse(it.postAt));
}

async function runQueue(reason, forced = false) {
  if (running) { await log("Already working — skipped this check."); return; }
  const settings = await getSettings();
  if (!settings.enabled && !forced) return;
  running = true;
  const keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
  try {
    await log(`Checking queue (${reason}, mode: ${settings.mode}).`);
    const items = await fetchQueue(settings.queueUrl);
    const status = await getStatus();
    const now = Date.now();
    const due = items
      .filter((it) => {
        if (!validItem(it)) return false;
        if (status[it.id]) return false;
        const t = Date.parse(it.postAt);
        return t - now >= MIN_LEAD_MS && t - now <= MAX_LEAD_MS;
      })
      .sort((a, b) => Date.parse(a.postAt) - Date.parse(b.postAt));
    const missed = items.filter((it) => validItem(it) && !status[it.id] && Date.parse(it.postAt) - now < MIN_LEAD_MS);
    for (const it of missed) {
      await setItemStatus(it.id, "missed", "Post time too close or already passed");
      await log(`${it.id}: post time already passed — skipped.`);
    }
    if (!due.length) { await log("Nothing new to schedule right now."); return; }
    let done = 0;
    for (const item of due.slice(0, MAX_PER_RUN)) {
      const ok = await processItem(item, settings);
      done += ok ? 1 : 0;
      if (!ok) break; // stop on the first failure so you can look at it
      if (settings.mode === "review") break; // review mode: one at a time
    }
    await log(`Finished this check: ${done} video(s) handled.`);
  } catch (e) {
    await log(`Error: ${e.message}`);
    notify("HEM TikTok Poster — error", e.message);
  } finally {
    clearInterval(keepAlive);
    running = false;
  }
}

// ---------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function inPage(tabId, func, args = []) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res?.result;
}

async function waitFor(tabId, check, what, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const s = await inPage(tabId, pageState).catch(() => null);
    if (s && check(s)) return s;
    await sleep(700);
  }
  throw new Error(`Timed out waiting for ${what}`);
}

async function waitTabComplete(tabId, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === "complete") return;
    await sleep(500);
  }
  throw new Error("TikTok Studio page did not finish loading");
}

async function fetchVideoBase64(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Video download failed: HTTP ${res.status} (${url})`);
  const buf = new Uint8Array(await res.arrayBuffer());
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < buf.length; i += CH) bin += String.fromCharCode(...buf.subarray(i, i + CH));
  return { b64: btoa(bin), bytes: buf.length };
}

// Chrome debugger input = real keyboard/mouse events. TikTok's caption box
// breaks if text is pushed in by script, but accepts real typing.
const cdp = (tabId, method, params = {}) => chrome.debugger.sendCommand({ tabId }, method, params);

async function clickAt(tabId, x, y) {
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await cdp(tabId, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
}

async function pressKey(tabId, key, code, vk, modifiers = 0, commands) {
  const base = { key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers };
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "rawKeyDown", ...base, ...(commands ? { commands } : {}) });
  await cdp(tabId, "Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

async function clickTarget(tabId, spec, what) {
  const r = await inPage(tabId, locate, [spec]);
  if (!r) throw new Error(`Could not find ${what} on the page`);
  if (r.disabled) throw new Error(`${what} is greyed out`);
  await clickAt(tabId, r.x, r.y);
  await sleep(500);
}

// ---------------------------------------------------------------- main job
async function processItem(item, settings) {
  const caption = [item.caption.trim(), ...(item.hashtags || [])].join(" ").trim() + " ";
  await log(`${item.id}: starting (post time ${item.postAt}).`);
  let tabId = null;
  let winId = null;
  let attached = false;
  let keepOpen = false;
  try {
    const video = await fetchVideoBase64(videoUrlFor(settings.queueUrl, item.video));
    await log(`${item.id}: video downloaded (${(video.bytes / 1048576).toFixed(1)} MB).`);

    const win = await chrome.windows.create({ url: UPLOAD_URL, focused: true, width: 1280, height: 900 });
    winId = win.id;
    tabId = win.tabs[0].id;
    await waitTabComplete(tabId, 60000);
    let s = await waitFor(tabId, (st) => st.hasFileInput || st.loginNeeded, "the upload box", 60000);
    if (s.loginNeeded) throw new Error("TikTok is signed out in this Chrome — sign in, then press Run now");
    if (s.unsavedBanner) { await inPage(tabId, discardUnsavedBanner); await sleep(1000); }

    const name = item.video.split("/").pop();
    const injected = await inPage(tabId, injectVideo, [video.b64, name]);
    if (!injected) throw new Error("Could not put the video into the upload box");
    await waitFor(tabId, (st) => st.hasEditor && st.hasScheduleRadio, "the post form", 180000);
    await log(`${item.id}: video accepted by TikTok Studio.`);

    await chrome.debugger.attach({ tabId }, "1.3");
    attached = true;

    // Caption: click box, select all, delete, type.
    await clickTarget(tabId, { kind: "css", value: ".public-DraftEditor-content" }, "the caption box");
    await pressKey(tabId, "a", "KeyA", 65, 2, ["selectAll"]);
    await pressKey(tabId, "Backspace", "Backspace", 8);
    await sleep(300);
    await cdp(tabId, "Input.insertText", { text: caption });
    await sleep(1200);
    s = await inPage(tabId, pageState);
    if (!s.editorText.startsWith(item.caption.trim().slice(0, 20))) {
      throw new Error(`Caption did not type correctly (box shows: "${s.editorText.slice(0, 80)}")`);
    }

    // When to post → Schedule (+ TikTok's one-time "Allow" box).
    await clickTarget(tabId, { kind: "radioLabel", value: "schedule" }, "the Schedule option");
    await sleep(1200);
    s = await inPage(tabId, pageState);
    if (s.dialogs.some((d) => /saved for scheduled posting/i.test(d))) {
      await clickTarget(tabId, { kind: "dialogButton", value: "Allow" }, "the Allow button");
      await sleep(1200);
      s = await inPage(tabId, pageState);
    }
    if (!s.scheduleChecked) throw new Error("Schedule option did not switch on");

    // Date and time, worked out in the laptop's own time zone.
    const target = await inPage(tabId, targetParts, [item.postAt]);
    await clickTarget(tabId, { kind: "aria", value: "Select date" }, "the date picker");
    for (let i = 0; i < 14; i++) {
      const cal = await inPage(tabId, calendarTitle);
      if (!cal) throw new Error("Date picker did not open");
      if (cal.month === target.monthLong && cal.year === target.year) break;
      const ahead = (target.year * 12 + target.monthIndex) > (cal.year * 12 + cal.monthIndex);
      await clickTarget(tabId, { kind: "aria", value: ahead ? "Next month" : "Previous month" }, "the month arrow");
      if (i === 13) throw new Error("Could not reach the right month");
    }
    await clickTarget(tabId, { kind: "dayButton", value: target.dayLabel }, `the day ${target.dayLabel}`);
    await pressKey(tabId, "Escape", "Escape", 27);
    await sleep(500);

    await clickTarget(tabId, { kind: "aria", value: "Select time" }, "the time picker");
    await clickTarget(tabId, { kind: "aria", value: `Hours ${target.hh}` }, `hour ${target.hh}`);
    await clickTarget(tabId, { kind: "aria", value: `Minutes ${target.mm}` }, `minute ${target.mm}`);
    await pressKey(tabId, "Escape", "Escape", 27);
    await sleep(800);

    s = await inPage(tabId, pageState);
    if (s.dateText !== target.dateText || s.timeText !== `${target.hh}:${target.mm}`) {
      throw new Error(`Schedule shows ${s.dateText} ${s.timeText}, wanted ${target.dateText} ${target.hh}:${target.mm}`);
    }
    await log(`${item.id}: caption typed, scheduled for ${target.dateText} ${target.hh}:${target.mm} (laptop time).`);

    await waitFor(tabId, (st) => st.uploaded, "the video upload to finish", 300000);

    if (settings.mode === "review") {
      keepOpen = true;
      await setItemStatus(item.id, "filled", `Ready to review: ${target.dateText} ${target.hh}:${target.mm}`);
      await log(`${item.id}: REVIEW MODE — everything filled in. Check the window and press Schedule yourself.`);
      notify("HEM TikTok Poster — ready to review", `${item.id} is filled in for ${target.dateText} ${target.hh}:${target.mm}. Check it and press Schedule.`);
      return true;
    }

    await clickTarget(tabId, { kind: "button", value: "Schedule" }, "the Schedule button");
    const end = Date.now() + 90000;
    let result = null;
    while (Date.now() < end && !result) {
      await sleep(1500);
      const t = await chrome.tabs.get(tabId).catch(() => null);
      if (!t) { result = "tab closed"; break; }
      const st = await inPage(tabId, pageState).catch(() => null);
      if (!st) continue;
      if (!t.url.includes("/tiktokstudio/upload") || st.hasFileInput && !st.hasEditor || /scheduled/i.test(st.toast)) result = "ok";
      else if (st.dialogs.length) result = `TikTok showed: ${st.dialogs.join(" | ").slice(0, 200)}`;
    }
    if (result !== "ok") {
      keepOpen = true;
      throw new Error(result ? `After pressing Schedule — ${result}` : "No confirmation from TikTok after pressing Schedule");
    }
    await setItemStatus(item.id, "scheduled", `${target.dateText} ${target.hh}:${target.mm}`);
    await log(`${item.id}: SCHEDULED for ${target.dateText} ${target.hh}:${target.mm}.`);
    return true;
  } catch (e) {
    keepOpen = true;
    await setItemStatus(item.id, "failed", e.message);
    await log(`${item.id}: FAILED — ${e.message}. Window left open so you can see it.`);
    notify("HEM TikTok Poster — needs a look", `${item.id}: ${e.message}`);
    return false;
  } finally {
    if (attached) await chrome.debugger.detach({ tabId }).catch(() => {});
    if (winId !== null && !keepOpen) await chrome.windows.remove(winId).catch(() => {});
  }
}

// ---------------------------------------------------------------- page-side functions
// These run inside the TikTok Studio page. They must not use outside variables.

function pageState() {
  const vis = (e) => !!(e && e.offsetParent);
  const ed = document.querySelector(".public-DraftEditor-content");
  const radio = document.querySelector('input[name="postSchedule"][value="schedule"]');
  const trig = (label) => document.querySelector(`[role="combobox"][aria-label="${label}"]`);
  const body = document.body ? document.body.innerText : "";
  return {
    hasFileInput: !!document.querySelector('input[type="file"]'),
    hasEditor: !!ed,
    editorText: ed ? ed.textContent : "",
    hasScheduleRadio: !!radio,
    scheduleChecked: !!(radio && radio.checked),
    dateText: trig("Select date") ? trig("Select date").textContent.trim() : "",
    timeText: trig("Select time") ? trig("Select time").textContent.trim() : "",
    uploaded: /Uploaded\s*[（(]/.test(body),
    unsavedBanner: /editing wasn.t saved/i.test(body),
    loginNeeded: /Log in to TikTok|Log in or sign up/i.test(body) && !document.querySelector('input[type="file"]'),
    dialogs: [...document.querySelectorAll('[role="dialog"]')].filter(vis).map((d) => d.innerText.trim()),
    toast: [...document.querySelectorAll('[role="alert"],[class*="toast"],[class*="Toast"]')].filter(vis).map((t) => t.innerText).join(" "),
  };
}

function discardUnsavedBanner() {
  const btns = [...document.querySelectorAll("button")].filter((b) => b.offsetParent && b.textContent.trim() === "Discard");
  const b = btns.find((x) => /editing wasn.t saved/i.test(x.closest("div")?.parentElement?.innerText || ""));
  (b || btns[0])?.click();
  return !!(b || btns[0]);
}

function injectVideo(b64, name) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const file = new File([bytes], name, { type: "video/mp4" });
  const input = document.querySelector('input[type="file"]');
  if (!input) return false;
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return input.files.length === 1;
}

function locate(spec) {
  const vis = (e) => !!(e && e.offsetParent);
  let el = null;
  if (spec.kind === "css") el = document.querySelector(spec.value);
  else if (spec.kind === "aria") el = [...document.querySelectorAll(`[aria-label="${spec.value}"]`)].find(vis) || null;
  else if (spec.kind === "radioLabel") el = document.querySelector(`input[name="postSchedule"][value="${spec.value}"]`)?.closest("label") || null;
  else if (spec.kind === "dayButton") el = [...document.querySelectorAll(".DatePicker__day")].find((b) => b.getAttribute("aria-label") === spec.value) || null;
  else if (spec.kind === "button") el = [...document.querySelectorAll("button")].filter(vis).find((b) => b.textContent.trim() === spec.value && !b.closest('[role="dialog"]')) || null;
  else if (spec.kind === "dialogButton") {
    const d = [...document.querySelectorAll('[role="dialog"]')].filter(vis).pop();
    el = d ? [...d.querySelectorAll("button")].find((b) => b.textContent.trim() === spec.value) || null : null;
  }
  if (!el) return null;
  el.scrollIntoView({ block: "center", inline: "center" });
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return {
    x: r.left + r.width / 2,
    y: r.top + r.height / 2,
    disabled: !!(el.disabled || el.getAttribute("aria-disabled") === "true" || el.getAttribute("data-disabled") === "true"),
  };
}

function calendarTitle() {
  const m = document.querySelector(".DatePicker__titleMonth");
  const y = document.querySelector(".DatePicker__titleYear");
  if (!m || !y) return null;
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const month = m.textContent.trim();
  return { month, monthIndex: months.indexOf(month), year: Number(y.textContent.trim()) };
}

function targetParts(iso) {
  const d = new Date(iso);
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const n = d.getDate();
  const sfx = (n % 100 >= 11 && n % 100 <= 13) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
  const mins = Math.floor(d.getMinutes() / 5) * 5; // TikTok's picker only has 5-minute steps
  return {
    year: d.getFullYear(),
    monthIndex: d.getMonth(),
    monthLong: months[d.getMonth()],
    dayLabel: `${days[d.getDay()]}, ${months[d.getMonth()]} ${n}${sfx}, ${d.getFullYear()}`,
    dateText: `${months[d.getMonth()].slice(0, 3)} ${n}, ${d.getFullYear()}`,
    hh: String(d.getHours()).padStart(2, "0"),
    mm: String(mins).padStart(2, "0"),
  };
}
