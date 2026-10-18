"use strict";

// DownloadX IDM-style background (v0.8.0 compatible).
// - Every storage write is serialized (no lost updates, no duplicates).
// - Same URL+format can never run twice at the same time.
// - Every task carries exact sizes: downloaded_text, total_text,
//   speed_text and size_text ("245.30 MB / 1.20 GB").
// - Normal browser downloads (software, zip, exe, pdf...) are captured.
const HOST = "com.downloadx.host";
const STORE_KEY = "downloadx.tasks.v1";
const QUEUE_KEY = "downloadx.queue.v1";
const MAX_SIMULTANEOUS = 3;

const ACTIVE_STATUSES = [
  "starting",
  "downloading",
  "paused",
  "processing",
  "cancelling"
];
const LIVE_STATUSES = ["queued", ...ACTIVE_STATUSES];
const FINISHED_STATUSES = ["queued", "error", "cancelled", "complete"];

let nativePort = null;
let nextId = 1;
const pending = new Map();
const taskTabs = new Map();
let queuePumpRunning = false;
let pumpAgain = false;

// Dashboard tab management
let dashboardOpeningPromise = null;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeId(prefix = "dx") {
  return `${prefix}-${Date.now()}-${nextId++}`;
}

function formatBytes(value) {
  if (value === null || value === undefined || value === "") return "";
  let n = Number(value);
  if (!Number.isFinite(n) || n < 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return i === 0 ? `${Math.round(n)} B` : `${n.toFixed(2)} ${units[i]}`;
}

function storageGet(keys) {
  return new Promise(resolve => chrome.storage.local.get(keys, resolve));
}

function storageSet(obj) {
  return new Promise(resolve => chrome.storage.local.set(obj, resolve));
}

function broadcast(message) {
  chrome.runtime.sendMessage(message, () => void chrome.runtime.lastError);
}

function postToTab(tabId, payload) {
  if (typeof tabId !== "number") return;
  chrome.tabs.sendMessage(tabId, payload, () => void chrome.runtime.lastError);
}

// ---------------------------------------------------------------------------
// Serialized storage (task list + queue)
// ---------------------------------------------------------------------------

let storeChain = Promise.resolve();
function withStoreLock(fn) {
  const run = storeChain.then(fn);
  storeChain = run.catch(() => {});
  return run;
}

const NO_CHANGE = Symbol("no-change");

async function getTasks() {
  const data = await storageGet([STORE_KEY]);
  return Array.isArray(data[STORE_KEY]) ? data[STORE_KEY] : [];
}

async function getQueue() {
  const data = await storageGet([QUEUE_KEY]);
  return Array.isArray(data[QUEUE_KEY]) ? data[QUEUE_KEY] : [];
}

// fn receives the tasks array and may mutate it in place.
// Return NO_CHANGE to skip the write.
function mutateTasks(fn) {
  return withStoreLock(async () => {
    const tasks = await getTasks();
    const result = fn(tasks);
    if (result === NO_CHANGE) return false;
    await storageSet({ [STORE_KEY]: tasks });
    broadcast({ type: "downloadx:tasks-updated" });
    return result === undefined ? true : result;
  });
}

function mutateQueue(fn) {
  return withStoreLock(async () => {
    const queue = await getQueue();
    const result = fn(queue);
    await storageSet({ [QUEUE_KEY]: queue });
    broadcast({ type: "downloadx:queue-updated" });
    return result;
  });
}

function saveTask(task) {
  return mutateTasks(tasks => {
    const index = tasks.findIndex(item => item.id === task.id);

    if (index >= 0) {
      tasks[index] = { ...tasks[index], ...task, updatedAt: Date.now() };
    } else {
      tasks.unshift({ ...task, updatedAt: Date.now() });
    }

    tasks.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    if (tasks.length > 500) tasks.length = 500;
  });
}

// guard(task) => boolean lets a late patch be ignored when the task has
// already moved on (e.g. "Download started" must not overwrite "complete").
function patchTask(id, patch, guard) {
  return mutateTasks(tasks => {
    const i = tasks.findIndex(t => t.id === id);
    if (i < 0) return NO_CHANGE;
    if (guard && !guard(tasks[i])) return NO_CHANGE;
    tasks[i] = { ...tasks[i], ...patch, updatedAt: Date.now() };
  });
}

function failInterruptedTasks(reason) {
  return mutateTasks(tasks => {
    let changed = false;
    for (let i = 0; i < tasks.length; i++) {
      if (ACTIVE_STATUSES.includes(tasks[i].status)) {
        tasks[i] = {
          ...tasks[i],
          status: "error",
          error: reason,
          message: reason,
          speed: null,
          speed_text: "",
          eta: null,
          finishedAt: Date.now(),
          updatedAt: Date.now()
        };
        changed = true;
      }
    }
    return changed ? true : NO_CHANGE;
  });
}

// ---------------------------------------------------------------------------
// Dashboard tab
// ---------------------------------------------------------------------------

// activate=true is used only for an explicit toolbar click.
function openDashboard(activate = false) {
  const dashboardUrl = chrome.runtime.getURL("dashboard.html");

  if (dashboardOpeningPromise) {
    return dashboardOpeningPromise.then(tab => {
      if (activate && tab?.id != null) {
        return new Promise(resolve => {
          chrome.tabs.update(tab.id, { active: true }, () => {
            void chrome.runtime.lastError;
            resolve(tab);
          });
        });
      }
      return tab;
    });
  }

  dashboardOpeningPromise = new Promise(resolve => {
    chrome.tabs.query({}, tabs => {
      if (chrome.runtime.lastError) {
        console.error(
          "[DownloadX] Could not query tabs:",
          chrome.runtime.lastError.message
        );
        resolve(null);
        return;
      }

      const existing = (tabs || []).find(tab => {
        if (!tab.url) return false;

        try {
          const current = new URL(tab.url);
          const expected = new URL(dashboardUrl);

          return current.origin === expected.origin &&
                 current.pathname === expected.pathname;
        } catch {
          return false;
        }
      });

      if (existing) {
        if (activate && existing.id != null) {
          chrome.tabs.update(existing.id, { active: true }, () => {
            if (chrome.runtime.lastError) {
              console.warn(
                "[DownloadX] Could not activate dashboard:",
                chrome.runtime.lastError.message
              );
            }
            resolve(existing);
          });
        } else {
          resolve(existing); // do not steal focus
        }
        return;
      }

      chrome.tabs.create({ url: dashboardUrl, active: true }, tab => {
        if (chrome.runtime.lastError) {
          console.error(
            "[DownloadX] Dashboard open failed:",
            chrome.runtime.lastError.message
          );
          resolve(null);
          return;
        }

        resolve(tab || null);
      });
    });
  }).finally(() => {
    dashboardOpeningPromise = null;
  });

  return dashboardOpeningPromise;
}

// ---------------------------------------------------------------------------
// Native host connection
// ---------------------------------------------------------------------------

function connectHost() {
  if (nativePort) return nativePort;

  try {
    nativePort = chrome.runtime.connectNative(HOST);

    nativePort.onMessage.addListener(onNativeMessage);

    nativePort.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError?.message ||
        "Native helper disconnected.";

      console.error("[DownloadX] Native host disconnected:", err);
      nativePort = null;

      for (const [id, item] of pending) {
        item.resolve({
          ok: false,
          request_id: id,
          error: "Native helper disconnected: " + err
        });
      }
      pending.clear();
      taskTabs.clear();

      // The host dies together with its downloads (and deletes their
      // temp files on its next start), so running tasks cannot continue.
      failInterruptedTasks("Native helper disconnected: " + err)
        .then(() => pumpQueue());
    });

    return nativePort;
  } catch (error) {
    console.error("[DownloadX] connectNative failed:", error);
    nativePort = null;
    return null;
  }
}

async function onNativeMessage(message) {
  if (!message || typeof message !== "object") return;

  const id = message.request_id;

  if (message.event) {
    const taskId = String(message.task_id || id || "");
    const patch = { lastEvent: message.event };

    if (message.event === "progress") {
      patch.status = message.status || "downloading";

      for (const key of [
        "percent",
        "downloaded",
        "total",
        "speed",
        "eta",
        "message",
        "filename",
        "title"
      ]) {
        const value = message[key];
        if (value === undefined) continue;
        // Never wipe known sizes with null (processing events send null).
        if ((key === "downloaded" || key === "total") && value === null) {
          continue;
        }
        patch[key] = value;
      }

      if (message.downloaded !== undefined && message.downloaded !== null) {
        patch.downloaded_text =
          message.downloaded_text || formatBytes(message.downloaded);
      }
      if (message.total !== undefined && message.total !== null) {
        patch.total_text = message.total_text || formatBytes(message.total);
      }
      if (message.speed) {
        patch.speed_text =
          message.speed_text || (formatBytes(message.speed) + "/s");
      } else if (message.speed !== undefined) {
        patch.speed_text = "";
      }

      if (patch.downloaded_text && patch.total_text) {
        patch.size_text = `${patch.downloaded_text} / ${patch.total_text}`;
      } else if (patch.total_text) {
        patch.size_text = patch.total_text;
      }
    } else if (message.event === "state") {
      patch.status = message.status || "downloading";
      if (message.message) patch.message = message.message;
    } else if (message.event === "complete") {
      const result = message.result || {};
      const sizeText = result.size_text || formatBytes(result.size);

      patch.status = "complete";
      patch.percent = 100;
      patch.completedAt = Date.now();
      patch.result = message.result || null;
      patch.message = "Download complete";
      patch.speed = null;
      patch.speed_text = "";
      patch.eta = null;
      if (result.filename) patch.filename = result.filename;
      if (result.size) {
        patch.downloaded = result.size;
        patch.total = result.size;
      }
      if (sizeText) {
        patch.downloaded_text = sizeText;
        patch.total_text = sizeText;
        patch.size_text = sizeText;
      }
    } else if (message.event === "error") {
      patch.status = message.cancelled ? "cancelled" : "error";
      patch.error = message.error || "Download failed";
      patch.message = patch.error;
      patch.finishedAt = Date.now();
      patch.speed = null;
      patch.speed_text = "";
      patch.eta = null;
    }

    await patchTask(taskId, patch);

    const tabId = taskTabs.get(taskId);
    if (tabId != null) {
      postToTab(tabId, { type: "downloadx:event", ...message });
    }

    if (message.event === "complete" || message.event === "error") {
      taskTabs.delete(taskId);
      await pumpQueue();
    }

    return;
  }

  const waiter = pending.get(id);

  if (waiter) {
    pending.delete(id);
    waiter.resolve(message);
  }
}

function nativeRequest(payload, tabId) {
  const port = connectHost();

  if (!port) {
    return Promise.resolve({
      ok: false,
      error: "Could not connect to DownloadX native helper."
    });
  }

  const requestId = payload.request_id || makeId("req");
  const taskId = payload.task_id || requestId;
  const outgoing = {
    ...payload,
    request_id: requestId,
    task_id: taskId
  };

  if (
    ["download", "download_format"].includes(payload.action) &&
    typeof tabId === "number"
  ) {
    taskTabs.set(taskId, tabId);
  }

  return new Promise(resolve => {
    const timeout = payload.action === "formats" ? 120000 : 15000;

    const timer = setTimeout(() => {
      if (pending.has(requestId)) {
        pending.delete(requestId);
        resolve({
          ok: false,
          request_id: requestId,
          error: "DownloadX helper timed out. Check data\\host.log."
        });
      }
    }, timeout);

    pending.set(requestId, {
      resolve: result => {
        clearTimeout(timer);
        resolve(result);
      }
    });

    try {
      port.postMessage(outgoing);
    } catch (error) {
      pending.delete(requestId);
      clearTimeout(timer);

      resolve({
        ok: false,
        error: error?.message ||
          "Could not send request to native helper."
      });
    }
  });
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

async function enqueueJob(job) {
  const id = job.taskId || makeId("task");
  const tasksNow = await getTasks();

  // Same task id again (retry from a content script) => reuse it.
  const sameId = tasksNow.find(t => t.id === id);
  if (sameId && LIVE_STATUSES.includes(sameId.status)) {
    return { ok: true, task_id: sameId.id, status: sameId.status, duplicate: true };
  }

  // Never run the same URL + format twice at once.
  const existing = tasksNow.find(t =>
    t.url === job.url &&
    String(t.formatId || "") === String(job.formatId || "") &&
    LIVE_STATUSES.includes(t.status)
  );
  if (existing) {
    return {
      ok: true,
      task_id: existing.id,
      status: existing.status,
      duplicate: true
    };
  }

  const task = {
    id,
    task_id: id,
    url: job.url,
    referer: job.referer || "",
    title: job.title || job.url,
    filename: job.filename || "",
    formatId: job.formatId || "",
    kind: job.kind || "video",
    status: "queued",
    percent: 0,
    downloaded: 0,
    total: null,
    downloaded_text: "",
    total_text: "",
    speed_text: "",
    size_text: "",
    speed: null,
    eta: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    message: "Waiting in queue",
    queuedJob: job
  };

  await saveTask(task);
  await mutateQueue(queue => { queue.push({ id, ...job }); });
  pumpQueue();

  return { ok: true, task_id: id, status: "queued" };
}

async function pumpQueue() {
  if (queuePumpRunning) {
    pumpAgain = true;
    return;
  }

  queuePumpRunning = true;

  try {
    do {
      pumpAgain = false;

      while (true) {
        const tasks = await getTasks();
        const active = tasks.filter(t =>
          ACTIVE_STATUSES.includes(t.status)
        ).length;

        if (active >= MAX_SIMULTANEOUS) break;

        const job = await mutateQueue(queue => queue.shift());
        if (!job) break;

        await patchTask(job.id, {
          status: "starting",
          message: "Starting download…"
        });

        const payload = job.formatId
          ? {
              action: "download_format",
              url: job.url,
              referer: job.referer || "",
              format_id: String(job.formatId),
              request_id: makeId("req"),
              task_id: job.id
            }
          : {
              action: "download",
              url: job.url,
              referer: job.referer || "",
              filename: job.filename || "",
              title: job.title || "",
              cookie: job.cookie || "",
              request_id: makeId("req"),
              task_id: job.id
            };

        const response = await nativeRequest(payload, job.tabId);

        if (!response.ok) {
          await patchTask(
            job.id,
            {
              status: "error",
              error: response.error || "Could not start download",
              message: response.error || "Could not start download",
              finishedAt: Date.now()
            },
            t => ACTIVE_STATUSES.includes(t.status)
          );
        } else {
          // Only while still "starting": a fast host may already have
          // reported progress or completion.
          await patchTask(
            job.id,
            { status: "downloading", message: "Download started" },
            t => t.status === "starting"
          );
        }
      }
    } while (pumpAgain);
  } finally {
    queuePumpRunning = false;
  }
}

async function removeTask(id, deleteFile) {
  await mutateQueue(queue => {
    const keep = queue.filter(item => item.id !== id);
    queue.splice(0, queue.length, ...keep);
  });

  const task = (await getTasks()).find(t => t.id === id);

  if (!task) return { ok: true };

  if (FINISHED_STATUSES.includes(task.status)) {
    let note = "Removed from list";

    if (deleteFile && task.result?.path) {
      const res = await nativeRequest({
        action: "delete_file",
        path: task.result.path
      });
      note = res.ok
        ? (res.deleted ? "File deleted" : "File was already gone")
        : "Could not delete file: " + (res.error || "unknown error");
    }

    await mutateTasks(tasks => {
      const keep = tasks.filter(t => t.id !== id);
      tasks.splice(0, tasks.length, ...keep);
    });

    return { ok: true, note };
  }

  // Active task: cancelling makes the host delete every temp file.
  return nativeRequest({
    action: "cancel",
    task_id: id,
    request_id: makeId("req")
  });
}

// ---------------------------------------------------------------------------
// Messages from dashboard / content scripts
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== "string") return false;

  const url = String(message.url || "").trim();
  const referer = String(message.referer || "").trim();
  const tabId = sender.tab?.id;

  if (message.type === "downloadx:list-tasks") {
    Promise.all([getTasks(), getQueue()])
      .then(([tasks, queue]) => {
        sendResponse({
          ok: true,
          tasks,
          queue,
          maxSimultaneous: MAX_SIMULTANEOUS
        });
      });

    return true;
  }

  if (message.type === "downloadx:enqueue") {
    if (!url) {
      sendResponse({ ok: false, error: "Enter a URL first." });
      return false;
    }

    enqueueJob({
      ...message.job,
      url,
      referer: message.job?.referer || referer
    }).then(sendResponse);

    return true;
  }

  if (message.type === "downloadx:control") {
    const id = String(message.taskId || "");

    (async () => {
      if (message.action === "remove") return removeTask(id, false);
      if (message.action === "delete") return removeTask(id, true);

      const task = (await getTasks()).find(t => t.id === id);

      if (message.action === "cancel" && task?.status === "queued") {
        await removeTask(id, false);
        return { ok: true };
      }

      if (message.action === "retry" && task) {
        await removeTask(id, false);
        return enqueueJob({
          url: task.url,
          referer: task.referer,
          title: task.title,
          filename: task.filename,
          formatId: task.formatId,
          kind: task.kind,
          cookie: task.queuedJob?.cookie || ""
        });
      }

      if (!["pause", "resume", "cancel"].includes(message.action)) {
        return { ok: false, error: "Unsupported action" };
      }

      return nativeRequest({
        action: message.action,
        task_id: id,
        request_id: makeId("req")
      });
    })().then(sendResponse);

    return true;
  }

  if (message.type === "downloadx:clear-completed") {
    mutateTasks(tasks => {
      const kept = tasks.filter(t =>
        !["complete", "cancelled", "error"].includes(t.status)
      );
      tasks.splice(0, tasks.length, ...kept);
    }).then(() => sendResponse({ ok: true }));

    return true;
  }

  if (message.type === "downloadx:ping" || message.type === "ping") {
    nativeRequest({ action: "ping" }, tabId).then(sendResponse);
    return true;
  }

  if (message.type === "downloadx:formats") {
    if (!url) {
      sendResponse({
        ok: false,
        error: "Missing video page URL."
      });
      return false;
    }

    nativeRequest({ action: "formats", url, referer }, tabId)
      .then(sendResponse);

    return true;
  }

  if (
    message.type === "downloadx:download-format" ||
    message.type === "downloadx:download-direct"
  ) {
    if (!url) {
      sendResponse({
        ok: false,
        error: "Missing media URL."
      });
      return false;
    }

    const isFormat = message.type === "downloadx:download-format";
    const id = String(
      message.taskId || message.requestId || makeId("task")
    );

    // Open dashboard on website downloads; reuse the existing tab.
    // If dashboard opening fails, do not block the download.
    openDashboard(false)
      .then(() => enqueueJob({
        taskId: id,
        tabId,
        url,
        referer,
        title: message.title || message.filename || url,
        // Never use the URL as a file name; the host reads the real name
        // from the server (Content-Disposition) or the URL path.
        filename: message.filename || "",
        formatId: isFormat ? String(message.formatId || "") : "",
        kind: isFormat ? "video" : "file"
      }))
      .then(sendResponse)
      .catch(error => {
        sendResponse({
          ok: false,
          error: error?.message || "Could not queue download."
        });
      });

    return true;
  }

  if (
    [
      "downloadx:pause",
      "downloadx:resume",
      "downloadx:cancel"
    ].includes(message.type)
  ) {
    const action = message.type.split(":")[1];

    nativeRequest({
      action,
      task_id: String(message.taskId || ""),
      request_id: makeId("req")
    }, tabId).then(sendResponse);

    return true;
  }

  return false;
});

// Toolbar click opens the full dashboard and focuses its existing tab.
chrome.action.onClicked.addListener(() => {
  openDashboard(true);
});

chrome.runtime.onStartup.addListener(() => pumpQueue());
chrome.runtime.onInstalled.addListener(() => pumpQueue());

// Every time this service worker boots there is no native port yet, so any
// task still marked active belongs to a dead helper. Mark it, then continue.
failInterruptedTasks("Interrupted: browser or helper was restarted")
  .then(() => pumpQueue());

// ===== DownloadX: capture normal browser downloads =====
const INTERCEPT_KEY = "downloadx.intercept";
const seenBrowserDownloads = new Set();

async function interceptEnabled() {
  const data = await storageGet([INTERCEPT_KEY]);
  return data[INTERCEPT_KEY] !== false; // ON by default
}

function getCookieHeader(url) {
  return new Promise(resolve => {
    try {
      chrome.cookies.getAll({ url }, cookies => {
        void chrome.runtime.lastError;
        resolve((cookies || []).map(c => `${c.name}=${c.value}`).join("; "));
      });
    } catch {
      resolve("");
    }
  });
}

function lastPart(value) {
  return String(value || "").split(/[\\/]/).pop();
}

chrome.downloads.onCreated.addListener(async item => {
  try {
    if (!item || seenBrowserDownloads.has(item.id)) return;
    seenBrowserDownloads.add(item.id);
    if (item.byExtensionId) return;

    const url = item.finalUrl || item.url || "";
    if (!/^https?:\/\//i.test(url)) return;      // blob:, data:, file: -> leave to Chrome
    if (!(await interceptEnabled())) return;

    // If the native helper is not available, let Chrome download normally.
    const ping = await nativeRequest({ action: "ping" });
    if (!ping.ok) return;

    const cookie = await getCookieHeader(url);

    chrome.downloads.cancel(item.id, () => {
      void chrome.runtime.lastError;
      chrome.downloads.erase({ id: item.id }, () => void chrome.runtime.lastError);
    });

    let urlName = "";
    try { urlName = decodeURIComponent(lastPart(new URL(url).pathname)); } catch {}
    const name = lastPart(item.filename);

    await openDashboard(false);
    await enqueueJob({
      url,
      referer: item.referrer || "",
      title: name || urlName || url,
      filename: name || "",
      kind: "file",
      cookie
    });
  } catch (error) {
    console.error("[DownloadX] Could not capture browser download:", error);
  }
});