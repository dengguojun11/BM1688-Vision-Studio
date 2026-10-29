/**
 * start_labelu_local.py 开发会话检测：服务重启后自动硬刷新当前 LabelU 页面。
 * 仅对本地 LabelU 应用端口（默认 9888）生效，不影响其它站点。
 */
(function () {
  "use strict";

  var STORAGE_KEY = "labelu_local_dev_session_id";
  var POLL_MS = 3000;

  function localAppPorts() {
    var meta = document.querySelector('meta[name="labelu-local-dev-ports"]');
    if (!meta) return [9888];
    return String(meta.getAttribute("content") || "9888")
      .split(",")
      .map(function (item) {
        return parseInt(item.trim(), 10);
      })
      .filter(function (port) {
        return port > 0;
      });
  }

  function isLocalLabeluApp() {
    var host = String(window.location.hostname || "").toLowerCase();
    var port = parseInt(window.location.port || "0", 10);
    if (localAppPorts().indexOf(port) < 0) return false;
    if (host === "127.0.0.1" || host === "localhost" || host === "::1") return true;
    if (/^192\.168\.\d+\.\d+$/.test(host)) return true;
    if (/^10\.\d+\.\d+\.\d+$/.test(host)) return true;
    return false;
  }

  function hardReload() {
    function reloadNow() {
      try {
        var url = new URL(window.location.href);
        url.searchParams.set("_labelu_dev", String(Date.now()));
        window.location.replace(url.toString());
      } catch (e) {
        window.location.reload();
      }
    }

    try {
      if (window.caches && typeof window.caches.keys === "function") {
        window.caches
          .keys()
          .then(function (keys) {
            return Promise.all(
              keys.map(function (key) {
                return window.caches.delete(key);
              })
            );
          })
          .finally(reloadNow);
        return;
      }
    } catch (e) {}
    reloadNow();
  }

  function applySession(sessionId) {
    if (!sessionId) return;
    var previous = sessionStorage.getItem(STORAGE_KEY) || "";
    sessionStorage.setItem(STORAGE_KEY, sessionId);
    if (previous && previous !== sessionId) {
      hardReload();
    }
  }

  function checkSession() {
    fetch("/api/v1/local-dev/session", {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin",
    })
      .then(function (resp) {
        if (!resp.ok) return null;
        return resp.json();
      })
      .then(function (payload) {
        var data = payload && payload.data;
        if (!data || !data.session_id) return;
        applySession(String(data.session_id));
      })
      .catch(function () {});
  }

  if (!isLocalLabeluApp()) return;

  checkSession();
  window.addEventListener("focus", checkSession);
  window.setInterval(checkSession, POLL_MS);
})();
