
(() => {
  "use strict";

  const BTN_CLASS = "downloadx-quality-btn";
  const tracked = new Map();
  let panel = null;
  let scanTimer = null;

  const isHttpUrl = (value) => {
    try {
      const u = new URL(value);
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  };

  function sendMessage(message, callback) {
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const error = chrome.runtime.lastError;

        callback(
          error
            ? {
                ok: false,
                error: error.message || "Extension messaging failed.",
              }
            : response || {
                ok: false,
                error: "DownloadX returned an empty response.",
              }
        );
      });
    } catch (error) {
      callback({
        ok: false,
        error: error?.message || "Could not contact DownloadX.",
      });
    }
  }

  function closePanel() {
    if (panel) {
      panel.remove();
      panel = null;
    }
  }

  function fmtBytes(value) {
    if (value == null || !Number.isFinite(Number(value))) {
      return "—";
    }

    const units = ["B", "KB", "MB", "GB", "TB"];
    let n = Number(value);
    let i = 0;

    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i++;
    }

    return `${n.toFixed(i >= 2 ? 2 : 1)} ${units[i]}`;
  }

  function fmtSpeed(value) {
    return value
      ? `${(Number(value) / 1048576).toFixed(2)} MB/s`
      : "— MB/s";
  }

  function fmtTime(value) {
    if (value == null || !Number.isFinite(Number(value))) {
      return "—";
    }

    let seconds = Math.max(0, Math.round(Number(value)));
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;

    const minutes = Math.floor(seconds / 60);
    seconds %= 60;

    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  function makePanel(video, button) {
    closePanel();

    const box = document.createElement("div");
    box.setAttribute("data-downloadx-panel", "1");

    Object.assign(box.style, {
      position: "fixed",
      zIndex: "2147483647",
      width: "360px",
      maxWidth: "calc(100vw - 24px)",
      maxHeight: "calc(100vh - 24px)",
      overflowY: "auto",
      boxSizing: "border-box",
      padding: "16px",
      background: "#101820",
      color: "#f5f7fa",
      border: "1px solid #3c5265",
      borderRadius: "12px",
      boxShadow: "0 12px 40px rgba(0,0,0,.45)",
      font: "13px/1.5 Arial,sans-serif",
    });

    const title = document.createElement("div");
    title.textContent = "DownloadX · Video Downloader";
    title.style.cssText =
      "font-size:15px;font-weight:700;padding-right:25px;"
      + "margin-bottom:8px;overflow-wrap:anywhere";

    const subtitle = document.createElement("div");
    subtitle.textContent =
      "Choose quality, then monitor speed and progress.";
    subtitle.style.cssText =
      "font-size:12px;color:#9eafbd;margin-bottom:10px";

    const selectLabel = document.createElement("label");
    selectLabel.textContent = "Available formats";
    selectLabel.style.cssText =
      "display:block;font-weight:700;margin:10px 0 5px";

    const select = document.createElement("select");
    select.setAttribute(
      "aria-label",
      "Video quality or audio format"
    );
    select.style.cssText =
      "box-sizing:border-box;width:100%;padding:10px;"
      + "border-radius:7px;border:1px solid #607789;"
      + "background:#1b2935;color:#fff;margin:0 0 10px;"
      + "font-size:13px";
    select.disabled = true;

    const status = document.createElement("div");
    status.textContent = "Loading available formats…";
    status.style.cssText =
      "color:#bdc8d2;white-space:pre-line;margin:10px 0;"
      + "overflow-wrap:anywhere";

    const progressWrap = document.createElement("div");
    progressWrap.style.cssText =
      "height:8px;background:#263744;border-radius:10px;"
      + "overflow:hidden;margin:8px 0 5px";

    const progressBar = document.createElement("div");
    progressBar.style.cssText =
      "height:100%;width:0%;background:#36a3ff;"
      + "transition:width .2s";

    progressWrap.append(progressBar);

    const stats = document.createElement("div");
    stats.style.cssText =
      "font-size:12px;color:#c9d5df;line-height:1.7;"
      + "white-space:pre-line";
    stats.textContent =
      "Progress: —\nDownloaded: — / —\n"
      + "Speed: — MB/s\nTime left: —";

    const actions = document.createElement("div");
    actions.style.cssText =
      "display:flex;gap:7px;margin-top:10px;flex-wrap:wrap";

    function makeBtn(text, color) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      b.style.cssText =
        `flex:1;min-width:90px;padding:9px 7px;`
        + `background:${color};color:#fff;border:0;`
        + "border-radius:7px;font-weight:700;cursor:pointer";
      return b;
    }

    const download = makeBtn("Download selected", "#238636");
    download.disabled = true;

    const direct = makeBtn("Direct file", "#314657");
    const pause = makeBtn("Pause", "#8a5a13");
    const resume = makeBtn("Resume", "#1769aa");
    const cancel = makeBtn("Cancel", "#a52a2a");

    pause.disabled = true;
    resume.disabled = true;
    cancel.disabled = true;

    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "×";
    close.title = "Close";
    close.setAttribute("aria-label", "Close DownloadX");
    close.style.cssText =
      "position:absolute;right:8px;top:6px;"
      + "background:transparent;color:#fff;border:0;"
      + "font-size:22px;cursor:pointer";
    close.addEventListener("click", closePanel);

    actions.append(download, direct, pause, resume, cancel);

    box.append(
      title,
      close,
      subtitle,
      selectLabel,
      select,
      progressWrap,
      stats,
      status,
      actions
    );

    document.body.appendChild(box);

    const rect = button.getBoundingClientRect();
    const panelWidth = Math.min(360, innerWidth - 16);

    box.style.left =
      Math.max(8, Math.min(rect.left, innerWidth - panelWidth - 8))
      + "px";
    box.style.top = "8px";

    const desiredTop = rect.bottom + 8;

    if (desiredTop + 330 <= innerHeight - 8) {
      box.style.top = desiredTop + "px";
    } else {
      box.style.top =
        Math.max(8, Math.min(rect.top - 350, innerHeight - 370))
        + "px";
    }

    panel = box;

    const pageUrl = location.href;
    const referer = location.href;

    const directUrl = () => {
      try {
        return video.currentSrc || video.src || "";
      } catch {
        return "";
      }
    };

    let active = false;
    let paused = false;
    let formatsLoaded = false;
    let taskId = null;
    let requestId = null;

    const setStatus = (message, color = "#bdc8d2") => {
      status.textContent = message;
      status.style.color = color;
    };

    function setControls() {
      download.disabled =
        active || !formatsLoaded || !select.value;
      select.disabled = active || !formatsLoaded;
      direct.disabled = active || !isHttpUrl(directUrl());

      pause.disabled = !active || paused;
      resume.disabled = !active || !paused;
      cancel.disabled = !active;

      for (const b of [download, direct, pause, resume, cancel]) {
        b.style.opacity = b.disabled ? ".55" : "1";
      }
    }

    function updateProgress(event) {
      const downloaded = event.downloaded ?? 0;
      const total = event.total;
      const percent = event.percent;

      progressBar.style.width =
        percent == null
          ? "0%"
          : `${Math.max(0, Math.min(100, Number(percent)))}%`;

      stats.textContent =
        `Progress: ${percent == null ? "Unknown" : `${percent}%`}\n`
        + `Downloaded: ${fmtBytes(downloaded)} / ${fmtBytes(total)}\n`
        + `Speed: ${fmtSpeed(event.speed)}\n`
        + `Time left: ${fmtTime(event.eta)}`;

      if (event.message) setStatus(event.message);
    }

    function beginTask(isDirect) {
      if (active) return;

      active = true;
      paused = false;
      requestId =
        `dx-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      taskId = requestId;

      progressBar.style.width = "0%";
      stats.textContent =
        "Progress: starting…\nDownloaded: — / —\n"
        + "Speed: — MB/s\nTime left: —";

      setStatus(
        isDirect
          ? "Starting direct download…"
          : "Starting selected format…"
      );
      setControls();

      const payload = isDirect
        ? {
            type: "downloadx:download-direct",
            url: directUrl(),
            referer,
            requestId,
          }
        : {
            type: "downloadx:download-format",
            url: pageUrl,
            referer,
            formatId: select.value,
            requestId,
          };

      sendMessage(payload, (response) => {
        if (!box.isConnected) return;

        if (!response.ok) {
          active = false;
          setControls();
          setStatus(
            "Could not start download:\n"
            + (response.error || "Unknown error"),
            "#ff8b8b"
          );
          return;
        }

        taskId = response.task_id || response.request_id || requestId;
        setStatus(
          "Download started. Live progress will appear here.",
          "#a8d5b5"
        );
        setControls();
      });
    }

    download.addEventListener("click", () => beginTask(false));

    direct.addEventListener("click", () => {
      if (!isHttpUrl(directUrl())) {
        setStatus(
          "This player does not expose a direct HTTP/HTTPS media URL.",
          "#ffcc80"
        );
        return;
      }

      beginTask(true);
    });

    function taskCommand(action) {
      if (!taskId) return;

      sendMessage(
        {
          type: `downloadx:${action}`,
          taskId,
        },
        (response) => {
          if (!response.ok) {
            setStatus(
              response.error || `Could not ${action} task.`,
              "#ff8b8b"
            );
          } else if (action === "pause") {
            paused = true;
            setStatus("Paused. Press Resume to continue.", "#ffcc80");
          } else if (action === "resume") {
            paused = false;
            setStatus("Resuming download…", "#a8d5b5");
          } else {
            setStatus("Cancellation requested…", "#ffcc80");
          }

          setControls();
        }
      );
    }

    pause.addEventListener("click", () => taskCommand("pause"));
    resume.addEventListener("click", () => taskCommand("resume"));
    cancel.addEventListener("click", () => taskCommand("cancel"));

    const onEvent = (message) => {
      if (!box.isConnected || message?.type !== "downloadx:event") {
        return;
      }

      if (
        message.request_id !== requestId
        && message.task_id !== taskId
      ) {
        return;
      }

      if (message.event === "progress") {
        updateProgress(message);

      } else if (message.event === "state") {
        paused = message.status === "paused";

        if (message.message) {
          setStatus(
            message.message,
            paused ? "#ffcc80" : "#a8d5b5"
          );
        }

        setControls();

      } else if (message.event === "complete") {
        active = false;
        paused = false;
        progressBar.style.width = "100%";

        const result = message.result || {};

        stats.textContent =
          `Progress: 100%\n`
          + `Downloaded: ${fmtBytes(result.size)} / ${fmtBytes(result.size)}\n`
          + "Speed: — MB/s\nTime left: 0s";

        setStatus(
          "Download completed successfully.\n"
          + (result.path || result.message || "")
          + "\nCheck your DownloadX downloads folder.",
          "#a8d5b5"
        );

        setControls();
        taskId = null;
        requestId = null;

      } else if (message.event === "error") {
        active = false;
        paused = false;

        setStatus(
          (message.cancelled ? "Download cancelled.\n" : "Download error:\n")
          + (message.error || "Unknown error")
          + "\nCheck data\\host.log for details.",
          message.cancelled ? "#ffcc80" : "#ff8b8b"
        );

        setControls();
        taskId = null;
        requestId = null;
      }
    };

    chrome.runtime.onMessage.addListener(onEvent);

    const cleanup = new MutationObserver(() => {
      if (!box.isConnected) {
        chrome.runtime.onMessage.removeListener(onEvent);
        cleanup.disconnect();
      }
    });

    cleanup.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });

    sendMessage(
      {
        type: "downloadx:formats",
        url: pageUrl,
        referer,
      },
      (response) => {
        if (!box.isConnected) return;

        if (!response.ok || !Array.isArray(response.formats)) {
          setStatus(
            "Could not load formats:\n"
            + (response.error || "No formats returned."),
            "#ff8b8b"
          );
          setControls();
          return;
        }

        title.textContent =
          (response.title || "DownloadX · Video Downloader").slice(0, 100);

        const videoGroup = document.createElement("optgroup");
        videoGroup.label = "Video — MP4 output";

        const audioGroup = document.createElement("optgroup");
        audioGroup.label = "Audio — MP3 output";

        for (const format of response.formats) {
          if (format.id == null) continue;

          const option = document.createElement("option");
          option.value = String(format.id);
          option.textContent =
            String(format.label || `Format ${format.id}`);

          const id = String(format.id);
          const kind =
            format.kind || (id.startsWith("audio::") ? "audio" : "video");

          if (kind === "audio" || id.startsWith("audio::")) {
            audioGroup.appendChild(option);
          } else {
            videoGroup.appendChild(option);
          }
        }

        if (videoGroup.children.length) {
          select.appendChild(videoGroup);
        }

        if (audioGroup.children.length) {
          select.appendChild(audioGroup);
        }

        if (!select.options.length) {
          setStatus(
            "No selectable formats found. The media may be DRM-protected "
            + "or unsupported.",
            "#ffcc80"
          );
          formatsLoaded = false;
          setControls();
          return;
        }

        formatsLoaded = true;

        setStatus(
          `${select.options.length} format(s) available.\n`
          + `Download folder: ${response.download_dir || "DownloadX downloads"}\n`
          + "Pause/resume works while the helper remains connected.",
          "#a8d5b5"
        );

        setControls();
      }
    );
  }

  function makeButton(video) {
    const button = document.createElement("button");
    button.className = BTN_CLASS;
    button.textContent = "⬇ DownloadX";
    button.type = "button";
    button.setAttribute("aria-label", "Open DownloadX downloader");

    Object.assign(button.style, {
      position: "fixed",
      zIndex: "2147483646",
      padding: "8px 11px",
      background: "#1769e0",
      color: "#fff",
      border: "0",
      borderRadius: "7px",
      font: "700 12px Arial,sans-serif",
      cursor: "pointer",
      display: "none",
      boxShadow: "0 3px 12px rgba(0,0,0,.35)",
    });

    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      makePanel(video, button);
    });

    document.body.appendChild(button);
    return button;
  }

  function position(video, button) {
    const rect = video.getBoundingClientRect();

    const visible =
      rect.width > 120
      && rect.height > 80
      && rect.bottom > 0
      && rect.top < innerHeight
      && rect.right > 0
      && rect.left < innerWidth;

    if (!visible) {
      button.style.display = "none";
      return;
    }

    button.style.display = "block";
    button.style.top = Math.max(rect.top + 8, 4) + "px";
    button.style.left = Math.max(rect.left + 8, 4) + "px";
  }

  function scan() {
    if (!document.body) return;

    document.querySelectorAll("video").forEach((video) => {
      if (!tracked.has(video)) {
        tracked.set(video, makeButton(video));
      }
    });

    for (const [video, button] of tracked) {
      if (!video.isConnected) {
        button.remove();
        tracked.delete(video);
      } else {
        position(video, button);
      }
    }
  }

  function start() {
    if (scanTimer) clearInterval(scanTimer);

    scan();
    scanTimer = setInterval(scan, 1000);

    addEventListener("scroll", scan, true);
    addEventListener("resize", scan);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closePanel();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
