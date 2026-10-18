"use strict";
const $ = id => document.getElementById(id);
const taskList = $("taskList"), emptyState = $("emptyState"), taskTemplate = $("taskTemplate");
let tasks = [], queue = [], activeFilter = "all", selected = new Set(), helperDir = "";
const ACTIVE = new Set(["starting", "downloading", "paused", "processing", "cancelling"]);
const RUNNING = ["starting", "downloading", "processing"];
const FINISHED = ["complete", "error", "cancelled"];
const TITLES = {all:"All downloads",downloading:"Downloading",queued:"Queue",complete:"Completed",error:"Failed"};
const ICONS = {audio:"♫", video:"▶", file:"▣"};

/* ---------- helpers ---------- */
function send(message) {
  return new Promise(resolve => chrome.runtime.sendMessage(message, response => {
    const err = chrome.runtime.lastError;
    resolve(response || {ok:false,error:err?.message || "No response from DownloadX background."});
  }));
}
function fmtBytes(value) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "—";
  let n = Number(value); if (n < 0) return "—";
  const units = ["B","KB","MB","GB","TB"]; let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
function fmtSpeed(value) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) || Number(value) <= 0) return "—";
  return `${fmtBytes(value)}/s`;
}
function fmtEta(value) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) || Number(value) < 0) return "";
  const s = Math.round(Number(value));
  if (s < 60) return `${s}s left`;
  if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s left`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m left`;
}
function cleanTitle(task) {
  const title = task.title || task.filename || task.url || "Download";
  return title.length > 160 ? title.slice(0,157) + "…" : title;
}
function statusLabel(task) {
  const s = task.status || "queued";
  return ({starting:"Starting",downloading:"Downloading",paused:"Paused",processing:"Processing",cancelling:"Stopping",queued:"Queued",complete:"Completed",error:"Failed",cancelled:"Cancelled"})[s] || s;
}
function categoryOf(task) {
  if (task.kind === "audio") return "audio";
  const name = `${task.title || ""} ${task.filename || ""}`.toLowerCase();
  if (/\.(mp3|m4a|aac|wav|flac|opus)(\?|$)/.test(name)) return "audio";
  return task.kind === "video" ? "video" : "file";
}
function matchesFilter(task) {
  if (activeFilter === "all") return true;
  if (activeFilter === "downloading") return ACTIVE.has(task.status) && task.status !== "paused" && task.status !== "cancelling";
  if (activeFilter === "queued") return task.status === "queued";
  if (activeFilter === "complete") return task.status === "complete";
  if (activeFilter === "error") return ["error","cancelled"].includes(task.status);
  return true;
}
function matchesQuery(task) {
  const q = $("searchInput").value.trim().toLowerCase();
  return !q || `${task.title||""} ${task.filename||""} ${task.url||""} ${task.status||""}`.toLowerCase().includes(q);
}
function visibleTasks() { return tasks.filter(t => matchesFilter(t) && matchesQuery(t)); }

/* ---------- toast + confirm ---------- */
function toast(text, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`; el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 3800);
}
function confirmBox(title, text, yesLabel = "Delete") {
  return new Promise(resolve => {
    const modal = $("confirmModal");
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmYes").textContent = yesLabel;
    modal.classList.remove("hidden");
    $("confirmNo").focus();
    const done = value => {
      modal.classList.add("hidden");
      $("confirmYes").onclick = $("confirmNo").onclick = null;
      modal.onclick = null; document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = e => { if (e.key === "Escape") done(false); };
    document.addEventListener("keydown", onKey);
    $("confirmYes").onclick = () => done(true);
    $("confirmNo").onclick = () => done(false);
    modal.onclick = e => { if (e.target === modal) done(false); };
  });
}
const DESTRUCTIVE = {
  "delete-file": {title:"Delete permanently?", yes:"Delete permanently", text:n => n > 1 ? `${n} downloads and their files on your computer will be deleted. This cannot be undone.` : "This download and its file on your computer will be deleted. This cannot be undone."},
  "cancel-delete": {title:"Cancel and delete?", yes:"Cancel & delete", text:n => n > 1 ? `${n} downloads will stop and all partial files will be deleted. This cannot be undone.` : "The download will stop and its partial files will be deleted. This cannot be undone."}
};

/* ---------- actions (single + bulk share the same rules) ---------- */
function canDo(action, task) {
  const s = task.status;
  switch (action) {
    case "pause": return RUNNING.includes(s);
    case "resume": return s === "paused";
    case "cancel": return ACTIVE.has(s) || s === "queued";
    case "cancel-delete": return ACTIVE.has(s) || s === "queued";
    case "retry": return s === "error" || s === "cancelled";
    case "remove": return !ACTIVE.has(s);
    case "delete-file": return !ACTIVE.has(s) && s !== "queued";
    default: return false;
  }
}
async function runAction(action, task) {
  return send({type:"downloadx:control", taskId:task.id, action});
}
const DONE_TEXT = {pause:"Paused",resume:"Resumed",cancel:"Cancelled",retry:"Retrying",remove:"Removed from list","delete-file":"Deleted permanently","cancel-delete":"Cancelled and deleted"};
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function taskStillLive(id) {
  const fresh = await send({type:"downloadx:list-tasks"});
  const t = fresh.ok && Array.isArray(fresh.tasks) ? fresh.tasks.find(x => x.id === id) : null;
  return !!t && (ACTIVE.has(t.status) || t.status === "queued");
}
// Permanent cancel: host stops the job and deletes every temp/partial file,
// then the entry is removed from the list.
async function cancelAndDelete(task) {
  const r = await send({type:"downloadx:control", taskId:task.id, action:"cancel"});
  if (!r.ok) return r;
  for (let i = 0; i < 20 && await taskStillLive(task.id); i++) await sleep(400);
  return send({type:"downloadx:control", taskId:task.id, action:"remove"});
}
// Permanent delete: background.js action "delete" removes the entry AND the file on disk.
function deleteWithFile(task) {
  return send({type:"downloadx:control", taskId:task.id, action:"delete"});
}
async function perform(action, list) {
  const targets = list.filter(t => canDo(action, t));
  if (!targets.length) { toast("Nothing to do for the selected items.", "bad"); return; }
  if (DESTRUCTIVE[action]) {
    const d = DESTRUCTIVE[action];
    if (!await confirmBox(d.title, d.text(targets.length), d.yes)) return;
  }
  let failed = 0, lastError = "", warn = "";
  for (const task of targets) {
    let result;
    if (action === "cancel-delete") result = await cancelAndDelete(task);
    else if (action === "delete-file") {
      result = await deleteWithFile(task);
      if (result.ok && /could not/i.test(result.note || "")) warn = result.note;
    } else result = await runAction(action, task);
    if (!result.ok) { failed++; lastError = result.error || "Action failed"; }
    selected.delete(task.id);
  }
  if (failed) { toast(`${failed} failed: ${lastError}`, "bad"); setFooter(lastError); }
  else if (warn) toast(warn, "bad");
  else toast(`${DONE_TEXT[action] || "Done"}${targets.length > 1 ? ` (${targets.length})` : ""}`, "ok");
  await refresh();
}

/* ---------- render ---------- */
function renderCounts() {
  const c = fn => tasks.filter(fn).length;
  $("countAll").textContent = tasks.length;
  $("countDownloading").textContent = c(t => ACTIVE.has(t.status));
  $("countQueued").textContent = c(t => t.status === "queued");
  $("countComplete").textContent = c(t => t.status === "complete");
  $("countFailed").textContent = c(t => ["error","cancelled"].includes(t.status));
  $("statActive").textContent = c(t => ACTIVE.has(t.status));
  $("statQueue").textContent = c(t => t.status === "queued");
  $("statComplete").textContent = c(t => t.status === "complete");
  const total = tasks.filter(t => t.status === "downloading").reduce((a, t) => a + (Number(t.speed) || 0), 0);
  $("statSpeed").textContent = fmtSpeed(total);
}
function render() {
  renderCounts();
  $("pageTitle").textContent = TITLES[activeFilter] || "All downloads";
  const visible = visibleTasks();
  taskList.replaceChildren();
  for (const task of visible) {
    const node = taskTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.status = task.status || "queued";
    node.dataset.kind = categoryOf(task);
    const checkbox = node.querySelector(".task-select"); checkbox.checked = selected.has(task.id);
    checkbox.addEventListener("change", () => { checkbox.checked ? selected.add(task.id) : selected.delete(task.id); updateBulk(); });
    node.querySelector(".file-icon").textContent = ICONS[categoryOf(task)];
    const title = node.querySelector(".task-title"); title.textContent = cleanTitle(task); title.title = cleanTitle(task);
    const pill = node.querySelector(".status-pill"); pill.textContent = statusLabel(task); pill.classList.add(task.status || "queued");
    const subtitle = node.querySelector(".task-subtitle"); subtitle.textContent = task.filename || task.url || ""; subtitle.title = subtitle.textContent;
    const percent = Math.max(0, Math.min(100, Number(task.percent) || 0));
    node.querySelector(".progress-fill").style.width = `${task.status === "complete" ? 100 : percent}%`;
    node.querySelector(".task-message").textContent = task.error || task.message || (task.status === "queued" ? "Waiting in queue" : "");
    node.querySelector(".task-numbers").textContent = task.total ? `${fmtBytes(task.downloaded)} / ${fmtBytes(task.total)} · ${Math.round(percent)}%` : (task.status === "complete" ? "100% complete" : (task.status === "queued" ? "Queued" : `${Math.round(percent)}%`));
    node.querySelector(".speed-value").textContent = task.status === "complete" ? fmtBytes(task.total || task.downloaded) : fmtSpeed(task.speed);
    node.querySelector(".eta-value").textContent = task.status === "complete" ? "Saved" : fmtEta(task.eta);
    node.querySelectorAll("[data-action]").forEach(button => {
      const action = button.dataset.action;
      button.hidden = !canDo(action, task);
      button.addEventListener("click", async () => {
        button.disabled = true;
        await perform(action, [task]);
        button.disabled = false;
      });
    });
    taskList.appendChild(node);
  }
  $("visibleCount").textContent = `${visible.length} item${visible.length === 1 ? "" : "s"}`;
  taskList.style.display = visible.length ? "grid" : "none";
  emptyState.classList.toggle("visible", visible.length === 0);
  const noMatch = tasks.length && !visible.length;
  emptyState.querySelector("h2").textContent = noMatch ? "No matching downloads" : "No downloads here yet";
  emptyState.querySelector("p").textContent = noMatch ? "Try another search or choose a different category." : "Add a file URL or open a video page and use the DownloadX video button.";
  $("emptyAddBtn").textContent = noMatch ? "＋ Add a download" : "＋ Add your first download";
  updateBulk();
}
function updateBulk() {
  $("bulkActions").classList.toggle("visible", selected.size > 0);
  const v = visibleTasks();
  $("selectAll").checked = v.length > 0 && v.every(t => selected.has(t.id));
}
function setFooter(message) { $("footerInfo").textContent = message || "Ready"; }
async function refresh() {
  const response = await send({ type:"downloadx:list-tasks" });
  if (!response.ok) { setFooter(response.error); return; }
  tasks = Array.isArray(response.tasks) ? response.tasks : [];
  queue = Array.isArray(response.queue) ? response.queue : [];
  const ids = new Set(tasks.map(t => t.id));
  selected = new Set([...selected].filter(id => ids.has(id)));
  render();
  setFooter(`${tasks.length} saved item${tasks.length === 1 ? "" : "s"} · ${new Date().toLocaleTimeString()}`);
}

/* ---------- add panel ---------- */
function setAddStatus(text, bad=false) { $("addStatus").textContent = text || ""; $("addStatus").style.color = bad ? "#ff9ba1" : "#9eafd0"; }
function openAddPanel() {
  $("addPanel").classList.remove("hidden");
  if (!$("urlInput").value) {
    chrome.tabs.query({active:true,currentWindow:true}, tabs => {
      const tab = tabs && tabs[0];
      if (tab?.url && /^https?:\/\//i.test(tab.url)) $("urlInput").value = tab.url;
    });
  }
  $("urlInput").focus();
}
async function fetchFormats() {
  const url = $("urlInput").value.trim();
  if (!/^https?:\/\//i.test(url)) { setAddStatus("Enter a valid http(s) video page URL first.", true); return; }
  const button = $("formatsBtn"); button.disabled = true; button.textContent = "Loading formats…"; setAddStatus("Checking available video and audio formats. This may take a little while…");
  const result = await send({type:"downloadx:formats",url,referer:url});
  button.disabled = false; button.textContent = "Get video formats";
  if (!result.ok || !Array.isArray(result.formats)) { setAddStatus(result.error || "No formats returned.", true); return; }
  const select = $("formatSelect"); select.replaceChildren();
  for (const format of result.formats) {
    if (format.id === undefined || format.id === null) continue;
    const option = document.createElement("option"); option.value = String(format.id); option.textContent = String(format.label || `Format ${format.id}`); select.appendChild(option);
  }
  if (!select.options.length) { setAddStatus("No selectable formats were returned for this URL.", true); return; }
  $("formatArea").classList.remove("hidden");
  setAddStatus(`${result.title || "Video"} · ${select.options.length} formats available · ${result.download_dir || "Download folder configured"}`);
}
async function addDirect() {
  const url = $("urlInput").value.trim();
  if (!/^https?:\/\//i.test(url)) { setAddStatus("Enter a valid http(s) file URL first.", true); return; }
  const raw = url.split("/").pop().split(/[?#]/)[0] || "download";
  let name = raw; try { name = decodeURIComponent(raw); } catch {}
  const result = await send({type:"downloadx:enqueue",url,job:{url,referer:url,title:name,filename:raw,kind:"file"}});
  if (!result.ok) setAddStatus(result.error || "Could not add download.", true);
  else { setAddStatus("Added to queue. DownloadX will start it when a slot is available."); toast("Added to queue", "ok"); $("formatArea").classList.add("hidden"); await refresh(); }
}
async function addVideo() {
  const url = $("urlInput").value.trim(), formatId = $("formatSelect").value;
  if (!url || !formatId) { setAddStatus("Choose a video format first.", true); return; }
  const option = $("formatSelect").selectedOptions[0];
  const result = await send({type:"downloadx:enqueue",url,job:{url,referer:url,title:option?.textContent || "Video download",formatId,kind:String(formatId).startsWith("audio::") ? "audio" : "video"}});
  if (!result.ok) setAddStatus(result.error || "Could not add video to queue.", true);
  else { setAddStatus("Video added to queue. Up to 3 downloads can run at once."); toast("Video added to queue", "ok"); await refresh(); }
}

/* ---------- events ---------- */
$("newDownloadBtn").addEventListener("click", () => { $("addPanel").classList.contains("hidden") ? openAddPanel() : $("addPanel").classList.add("hidden"); });
$("emptyAddBtn").addEventListener("click", openAddPanel);
$("closeAddBtn").addEventListener("click", () => $("addPanel").classList.add("hidden"));
$("formatsBtn").addEventListener("click", fetchFormats);
$("directBtn").addEventListener("click", addDirect);
$("queueVideoBtn").addEventListener("click", addVideo);
$("urlInput").addEventListener("keydown", e => { if (e.key === "Enter") addDirect(); });
$("searchInput").addEventListener("input", render);
$("refreshBtn").addEventListener("click", refresh);
$("filters").addEventListener("click", e => {
  const button = e.target.closest("[data-filter]"); if (!button) return;
  activeFilter = button.dataset.filter;
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item === button));
  render();
});
$("selectAll").addEventListener("change", e => {
  visibleTasks().forEach(t => e.target.checked ? selected.add(t.id) : selected.delete(t.id));
  render();
});
$("bulkActions").addEventListener("click", async e => {
  const button = e.target.closest("[data-bulk]"); if (!button) return;
  button.disabled = true;
  await perform(button.dataset.bulk, tasks.filter(t => selected.has(t.id)));
  button.disabled = false;
});
$("clearHistoryBtn").addEventListener("click", async () => {
  if (!tasks.some(t => FINISHED.includes(t.status))) { setFooter("No finished downloads to clear"); toast("No finished downloads to clear"); return; }
  await send({type:"downloadx:clear-completed"}); selected.clear(); toast("Finished downloads cleared (files kept)", "ok"); await refresh();
});
async function pingHelper(manual) {
  $("helperStatus").textContent = "Checking…";
  const result = await send({type:"downloadx:ping"});
  const ok = !!result.ok;
  $("helperDot").style.background = ok ? "" : "#ff6b76";
  if (ok) { helperDir = result.download_dir || ""; $("helperStatus").textContent = `Connected · v${result.version || "?"}`; if (manual) setFooter(helperDir ? `Save folder: ${helperDir}` : "Native helper connected"); }
  else { $("helperStatus").textContent = manual ? "Disconnected" : "Not connected"; if (manual) setFooter(result.error || "Native helper unavailable"); }
}
$("pingBtn").addEventListener("click", () => pingHelper(true));
chrome.runtime.onMessage.addListener(message => { if (["downloadx:tasks-updated","downloadx:queue-updated"].includes(message?.type)) refresh(); });
refresh();
pingHelper(false);

// Capture-browser-downloads switch (read by background.js)
const CAPTURE_KEY = "downloadx.intercept";
chrome.storage.local.get([CAPTURE_KEY], d => { $("captureToggle").checked = d[CAPTURE_KEY] !== false; });
$("captureToggle").addEventListener("change", e => {
  chrome.storage.local.set({[CAPTURE_KEY]: e.target.checked});
  toast(e.target.checked ? "DownloadX will capture browser downloads" : "Browser downloads left to Chrome", "ok");
});