/**
 * 任务详情 /tasks/:id：「AI 含几何拉框」开关（与标注页共用 localStorage）。
 * - 默认不勾选：include_geometry_tools=false，仅全局 tagTool。
 * - 勾选：include_geometry_tools=true，模型可输出拉框等几何工具。
 * localStorage: ai_include_geometry_tools（兼容旧键 labelu_ai_include_geometry_tools）
 *
 * 仅 patch Axios 使用的 XMLHttpRequest；不要 patch fetch，避免图片预加载请求
 * 的 initiator 被归到本脚本，看起来像额外重复拉图。
 */
(function () {
  var LS_KEY = "ai_include_geometry_tools";
  var LS_KEY_LEGACY = "labelu_ai_include_geometry_tools";

  function readIncludeGeometry() {
    try {
      var v = localStorage.getItem(LS_KEY);
      if (v !== null) return v === "true";
      var legacy = localStorage.getItem(LS_KEY_LEGACY);
      if (legacy !== null) return legacy === "true";
      return false;
    } catch (e) {
      return false;
    }
  }

  function writeIncludeGeometry(on) {
    try {
      localStorage.setItem(LS_KEY, on ? "true" : "false");
    } catch (e) {}
  }

  function isAutoLabelApiUrl(url) {
    if (!url || typeof url !== "string") return false;
    var path = url.split("#")[0].split("?")[0];
    try {
      if (/^https?:\/\//i.test(path)) {
        path = new URL(path).pathname || path;
      }
    } catch (e) {}
    path = path.replace(/\/+$/, "") || path;
    return (
      /\/api\/v1\/tasks\/\d+\/auto_label_job$/.test(path) ||
      /\/api\/v1\/tasks\/\d+\/samples\/\d+\/auto_label$/.test(path)
    );
  }

  function mergeBodyIfJson(body) {
    if (typeof body !== "string" || !body) return body;
    try {
      var o = JSON.parse(body);
      if (o && typeof o === "object") {
        // 标注页 Axios 已显式传 include_geometry_tools 时以请求体为准，避免任务页开关误改单图 AI 行为
        if (!Object.prototype.hasOwnProperty.call(o, "include_geometry_tools")) {
          o.include_geometry_tools = readIncludeGeometry();
        }
        return JSON.stringify(o);
      }
    } catch (e) {}
    return body;
  }

  if (typeof window.XMLHttpRequest !== "undefined" && !window.__labeluAiGeometryXhrPatched) {
    window.__labeluAiGeometryXhrPatched = true;
    var NativeXHR = window.XMLHttpRequest;
    window.XMLHttpRequest = function () {
      var xhr = new NativeXHR();
      var _open = xhr.open;
      var _send = xhr.send;
      var _method = "";
      var _url = "";
      xhr.open = function (method, url) {
        _method = (method || "").toUpperCase();
        _url = String(url || "");
        return _open.apply(xhr, arguments);
      };
      xhr.send = function (body) {
        if (_method === "POST" && isAutoLabelApiUrl(_url) && typeof body === "string") {
          body = mergeBodyIfJson(body);
        }
        return _send.call(xhr, body);
      };
      return xhr;
    };
    window.XMLHttpRequest.prototype = NativeXHR.prototype;
  }

  function mountUi() {
    if (document.getElementById("labelu-ai-geometry-toggle-wrap")) return;

    var bar = document.getElementById("labelu-task-toolbar-ext");
    if (!bar || !bar.appendChild) return false;

    var wrap = document.createElement("span");
    wrap.id = "labelu-ai-geometry-toggle-wrap";
    wrap.style.marginLeft = "4px";
    wrap.style.whiteSpace = "nowrap";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = "labelu-ai-geometry-toggle";
    cb.checked = readIncludeGeometry();
    cb.addEventListener("change", function () {
      writeIncludeGeometry(cb.checked);
    });
    var lab = document.createElement("label");
    lab.htmlFor = "labelu-ai-geometry-toggle";
    lab.style.marginLeft = "4px";
    lab.style.cursor = "pointer";
    lab.textContent = "AI 含几何拉框";
    wrap.appendChild(cb);
    wrap.appendChild(lab);
    var sep = document.createElement("span");
    sep.setAttribute("aria-hidden", "true");
    sep.style.cssText = "color:rgba(0,0,0,.25);user-select:none;margin:0 4px 0 8px;";
    sep.textContent = "|";
    bar.appendChild(sep);
    bar.appendChild(wrap);
    return true;
  }

  window.labeluMountAiGeometryToggle = mountUi;

  function tryMount() {
    mountUi();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tryMount);
  } else {
    tryMount();
  }

  if (!window.__labeluAiGeometryObs) {
    window.__labeluAiGeometryObs = true;
    var obs = new MutationObserver(function () {
      if (
        document.getElementById("labelu-task-toolbar-ext") &&
        !document.getElementById("labelu-ai-geometry-toggle-wrap")
      ) {
        mountUi();
      }
    });
    obs.observe(document.getElementById("root") || document.body, {
      childList: true,
      subtree: true,
    });
  }
})();
