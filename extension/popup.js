
chrome.tabs.create(
  {
    url: chrome.runtime.getURL("dashboard.html"),
    active: true
  },
  (tab) => {
    if (chrome.runtime.lastError) {
      document.getElementById("status").textContent =
        "Error: " + chrome.runtime.lastError.message;
      console.error("[DownloadX] Dashboard error:", chrome.runtime.lastError);
      return;
    }

    window.close();
  }
);
