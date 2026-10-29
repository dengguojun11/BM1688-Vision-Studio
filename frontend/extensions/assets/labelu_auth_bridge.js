/**
 * 主站 SPA 登录后与扩展页共用 localStorage.token；登录成功后按 ?redirect= 回跳。
 */
(function () {
  "use strict";

  function normalizeBearer(raw) {
    var t = (raw == null ? "" : String(raw)).trim();
    if (!t) return "";
    if (/^bearer\s+/i.test(t)) return t;
    if (t.indexOf("eyJ") === 0) return "Bearer " + t;
    return t;
  }

  function getToken() {
    return normalizeBearer(localStorage.getItem("token") || "");
  }

  function redirectTarget() {
    try {
      var p = new URLSearchParams(location.search);
      return (p.get("redirect") || p.get("next") || "").trim();
    } catch (e) {
      return "";
    }
  }

  function safeRedirect(url) {
    if (!url || url.charAt(0) !== "/" || /^\/login/i.test(url)) return false;
    location.replace(url);
    return true;
  }

  function tryReturn() {
    var next = redirectTarget();
    if (!next || !getToken()) return;
    safeRedirect(next);
  }

  if (/^\/login\/?$/i.test(location.pathname)) {
    tryReturn();
    window.addEventListener("storage", function (e) {
      if (e.key === "token") tryReturn();
    });
    var n = 0;
    var timer = setInterval(function () {
      tryReturn();
      if (++n > 120) clearInterval(timer);
    }, 1000);
  } else {
    var pending = redirectTarget();
    if (pending && getToken()) safeRedirect(pending);
  }
})();
