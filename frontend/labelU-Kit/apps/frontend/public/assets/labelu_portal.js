/**
 * 7GAI进化平台独立工具页：统一顶栏导航顺序 + 页面工作流提示。
 * 在各 HTML 的 body 上设置 data-lu-page="yolo-training" 等，并保留 .labelu-shell-nav 容器。
 */
(function (global) {
  "use strict";

  // Standalone pages use the same six-menu product structure as the main platform.
  // Technical pages remain reachable through their parent menu, but retired project,
  // workflow and device-deployment entries are deliberately not exposed here.
  var NAV_GROUPS = [
    {
      title: "工作台",
      items: [{ href: "/workspace", label: "工作台", key: "workspace" }],
    },
    {
      title: "任务与标注",
      items: [
        { href: "/tasks", label: "任务列表", key: "tasks" },
        { href: "/yolo-preannotate", label: "自动预标注", key: "yolo-preannotate" },
        { href: "/annotation-stats", label: "标注统计", key: "annotation-stats" },
        { href: "/annotation-settings", label: "标签与标注设置", key: "annotation" },
        { href: "/ai-labeling-settings", label: "通用预标注配置", key: "ai-labeling" },
      ],
    },
    {
      title: "训练中心",
      items: [
        { href: "/training", label: "训练作业", key: "yolo-training" },
        { href: "/yolo-quality-policies", label: "质量标准", key: "yolo-quality-policies" },
        { href: "/yolo-eval-prompts", label: "评估设置", key: "yolo-eval-prompts" },
        { href: "/yolo-eval-reports-hub", label: "评估报告", key: "yolo-eval-hub" },
      ],
    },
    {
      title: "模型中心",
      items: [
        { href: "/yolo-training?embedded=1&panel=models", label: "PT 模型", key: "yolo-training" },
        { href: "/yolo-bm1688-exports", label: "编译任务", key: "yolo-bm1688" },
        { href: "/yolo-bm1688-exports?view=bmodels", label: "BModel 模型", key: "yolo-bm1688-bmodels" },
        { href: "/yolo-model-publish", label: "发布记录", key: "yolo-model-publish" },
        { href: "/yolo-model-catalog", label: "已发布模型", key: "yolo-model-catalog" },
      ],
    },
    {
      title: "进化中心",
      items: [
        { href: "/evolution", label: "问题样本回流", key: "evolution" },
      ],
    },
    {
      title: "系统管理",
      items: [
        { href: "/admin?view=governance", label: "成员与标签管理", key: "admin" },
        { href: "/aicm-publish-environments", label: "外部发布环境", key: "aicm-publish-environments" },
        { href: "/edge-device-registry", label: "边端验证设备", key: "edge-device-registry" },
        { href: "/providers", label: "预标注与推理能力", key: "providers" },
      ],
    },
  ];

  var PAGE_META = {
    "yolo-training": {
      title: "YOLO11 模型训练",
      flow: [],
      hideHero: true,
    },
    "yolo-bm1688": {
      title: "模型编译",
      flow: [],
      hideHero: true,
    },
    "edge-device-registry": {
      title: "边端设备配置",
      flow: [],
      hideHero: true,
    },
    "aicm-publish-environments": {
      title: "AICM 发布环境配置",
      flow: [],
      hideHero: true,
    },
    "yolo-model-publish": {
      title: "模型发布",
      flow: [],
      hideHero: true,
    },
    "yolo-model-catalog": {
      title: "模型列表",
      flow: [],
      hideHero: true,
    },
    "yolo-quality-policies": {
      title: "场景质量标准",
      flow: [],
      hideHero: true,
    },
    "yolo-preannotate": {
      title: "YOLO 批量预标注",
      flow: [],
      hideHero: true,
    },
    "yolo-eval-prompts": {
      title: "YOLO AI 评估提示词",
      flow: [],
      hideHero: true,
    },
    "yolo-eval-hub": {
      title: "模型评估报告中心",
      flow: [],
      hideHero: true,
    },
    "ai-labeling": {
      title: "AI 标注配置",
      flow: [],
      hideHero: true,
    },
    annotation: {
      title: "标注预设与全局维度",
      flow: [],
      hideHero: true,
    },
  };

var BRAND_NAME = "7GAI进化平台";

  function renderBranding() {
    document.querySelectorAll(".labelu-shell-logo").forEach(function (el) {
      el.textContent = "";
      var logo = document.createElement("img");
      logo.src = "/brand/7g-logo-purple.png";
      logo.alt = "7G 标志";
      logo.width = 34;
      logo.height = 28;
      el.appendChild(logo);
      el.appendChild(document.createTextNode(BRAND_NAME));
      el.setAttribute("aria-label", BRAND_NAME);
    });
    document.querySelectorAll(".labelu-shell-brand").forEach(function (el) {
      var current = String(el.textContent || "").trim();
      if (/^labelu\b/i.test(current)) {
        el.textContent = current.replace(/^labelu\b/i, BRAND_NAME);
      }
    });
    if (document.title) {
      document.title = document.title.replace(/\blabelu\b/gi, BRAND_NAME);
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderShellNav(activeKey) {
    var nav = document.querySelector(".labelu-shell-nav");
    if (!nav) return;
    var html = [];
    NAV_GROUPS.forEach(function (g) {
      html.push('<span class="lu-nav-group-label">' + esc(g.title) + "</span>");
      g.items.forEach(function (it) {
        var cls = it.key === activeKey ? " lu-active" : "";
        html.push(
          '<a href="' +
            esc(it.href) +
            '" class="lu-nav-item' +
            cls +
            '">' +
            esc(it.label) +
            "</a>"
        );
      });
    });
    nav.setAttribute("aria-label", BRAND_NAME + " 工具导航");
    nav.innerHTML = html.join("");
  }

  function renderPageHero(pageKey, rootId) {
    var meta = PAGE_META[pageKey];
    var root = rootId ? document.getElementById(rootId) : document.getElementById("luPageHero");
    if (!meta || !root) return;
    if (meta.hideHero) {
      root.innerHTML = "";
      root.style.display = "none";
      return;
    }
    root.style.display = "";
    var flowHtml = meta.flow
      .map(function (s) {
        return (
          '<li><span class="lu-flow-num">' +
          s.n +
          '</span><span class="lu-flow-text">' +
          esc(s.text) +
          "</span></li>"
        );
      })
      .join("");
    root.innerHTML =
      '<div class="lu-page-hero">' +
      '<div class="lu-page-hero-main">' +
      "<h1>" +
      esc(meta.title) +
      "</h1>" +
      '<p class="lu-page-hero-desc">按业务顺序使用下列功能；顶栏可在各工具页间切换。</p>' +
      '<ol class="lu-flow-list">' +
      flowHtml +
      "</ol>" +
      "</div>" +
      '<div class="lu-page-hero-aside" id="luPageHeroAside"></div>' +
      "</div>";
  }

  function renderTrainViewHero() {}

  function updateSiderHint() {}

  var _authState = { verified: false, ok: false, pending: null };

  /** 规范化 Authorization：主站 token 含 Bearer；手动粘贴裸 JWT 时自动补前缀 */
  function normalizeBearer(raw) {
    var t = (raw == null ? "" : String(raw)).trim();
    if (!t) return "";
    if (/^bearer\s+/i.test(t)) return t;
    if (t.indexOf("eyJ") === 0) return "Bearer " + t;
    return t;
  }

  function isLoginPage() {
    return /^\/login\/?$/i.test(location.pathname || "");
  }

  function loginRedirectUrl() {
    var next = location.pathname + location.search + (location.hash || "");
    return "/login?redirect=" + encodeURIComponent(next);
  }

  function redirectToLogin() {
    if (isLoginPage()) return;
    window.location.href = loginRedirectUrl();
  }

  /** 与主站 login 共用 localStorage.token */
  function getShellToken() {
    return normalizeBearer(localStorage.getItem("token") || "");
  }

  function getAuthToken() {
    return getShellToken();
  }

  function setAuthToken(t) {
    if (t) localStorage.setItem("token", normalizeBearer(t));
    else localStorage.removeItem("token");
    _authState.verified = false;
    _authState.ok = false;
  }

  function verifySession() {
    var token = getAuthToken();
    if (!token) return Promise.resolve(false);
    return fetch("/api/v1/users/me", {
      headers: { Authorization: token },
      cache: "no-store",
      credentials: "same-origin",
    })
      .then(function (r) {
        if (r.ok) return true;
        if (r.status === 401 || r.status === 403) {
          localStorage.removeItem("token");
          syncAuthBox();
          return false;
        }
        return true;
      })
      .catch(function () {
        return !!token;
      });
  }

  function ensureAuth(opts) {
    opts = opts || {};
    return verifySession().then(function (ok) {
      _authState.verified = true;
      _authState.ok = ok;
      syncAuthBox();
      if (ok) {
        try {
          window.dispatchEvent(new CustomEvent("labelu-auth-ready"));
        } catch (e) {}
        return true;
      }
      if (opts.redirect !== false && !isLoginPage()) {
        redirectToLogin();
      }
      return false;
    });
  }

  function whenAuthReady(fn) {
    if (typeof fn !== "function") return;
    if (_authState.verified && _authState.ok) {
      fn();
      return;
    }
    if (_authState.pending) {
      _authState.pending.push(fn);
      return;
    }
    _authState.pending = [fn];
    ensureAuth().then(function (ok) {
      var cbs = _authState.pending || [];
      _authState.pending = null;
      if (ok) cbs.forEach(function (f) {
        try {
          f();
        } catch (e) {}
      });
    });
  }

  function handleUnauthorized(status) {
    if (status !== 401 && status !== 403) return false;
    setAuthToken("");
    syncAuthBox();
    try {
      window.dispatchEvent(
        new CustomEvent("labelu-auth-required", {
          detail: { message: "登录已过期，正在跳转登录页…" },
        })
      );
    } catch (e) {}
    redirectToLogin();
    return true;
  }

  /**
   * 已登录则隐藏 #authBox；未登录时显示「去主站登录」+ 可选手动粘贴。
   * 各扩展页在保存 Token 或加载数据前可调用 LabeluPortal.syncAuthBox()。
   */
  function syncAuthBox() {
    var box = document.getElementById("authBox");
    var token = getShellToken();
    document.querySelectorAll("[data-lu-login-link]").forEach(function (a) {
      a.setAttribute("href", loginRedirectUrl());
    });
    var need = document.getElementById("authNeedLogin");
    if (need) {
      var link = need.querySelector("a");
      if (link) link.setAttribute("href", loginRedirectUrl());
    }
    if (!box) return !!token;
    if (token) {
      box.style.display = "none";
      box.setAttribute("aria-hidden", "true");
    } else {
      box.style.display = "";
      box.removeAttribute("aria-hidden");
    }
    var input = document.getElementById("tokenInput");
    if (input && token) input.value = token;
    return !!token;
  }

  var YOLO_TOOLKIT_PAGES = [
    "yolo-training",
    "yolo-preannotate",
    "yolo-bm1688",
    "edge-device-registry",
    "aicm-publish-environments",
    "yolo-model-publish",
    "yolo-model-catalog",
    "yolo-eval-prompts",
    "ai-labeling",
  ];

  var TOOLKIT_SIDER_MAIN = [
    { href: "/yolo-training?embedded=1&panel=models", key: "yolo-training", label: "PT 模型" },
    { href: "/yolo-bm1688-exports", key: "yolo-bm1688", label: "编译任务" },
    { href: "/yolo-bm1688-exports?view=bmodels", key: "yolo-bm1688-bmodels", label: "BModel 模型" },
    { href: "/yolo-model-publish", key: "yolo-model-publish", label: "发布记录" },
    { href: "/yolo-model-catalog", key: "yolo-model-catalog", label: "已发布模型" },
  ];

  var TOOLKIT_SIDER_OTHER = [
    { href: "/yolo-preannotate", key: "yolo-preannotate", label: "YOLO 预标注" },
    { href: "/yolo-quality-policies#trainingStandards", key: "yolo-quality-policies-training", label: "训练质量标准" },
    { href: "/yolo-quality-policies#edgeStandards", key: "yolo-quality-policies-edge", label: "边端验证标准" },
    { href: "/yolo-quality-policies#releaseStandards", key: "yolo-quality-policies-release", label: "发布门禁规则" },
    { href: "/edge-device-registry", key: "edge-device-registry", label: "边端设备配置" },
    { href: "/aicm-publish-environments", key: "aicm-publish-environments", label: "AICM 发布环境" },
    { href: "/yolo-eval-prompts", key: "yolo-eval-prompts", label: "评估提示词" },
    { href: "/tasks", key: "tasks", label: "图像任务列表" },
    { href: "/annotation-settings", key: "annotation", label: "预设与维度" },
    { href: "/ai-labeling-settings", key: "ai-labeling", label: "AI 标注配置" },
  ];
  var TRAINING_HUB_MORE_GROUPS = [
    {
      title: "辅助工作台",
      items: [
        { href: "/third-party-calls", label: "第三方调用中心" },
        { tabId: "consistent", label: "筛选一致汇总" },
        { href: "/yolo-preannotate", label: "YOLO 预标注" },
        { href: "/tasks", label: "图像任务列表" },
      ],
    },
    {
      title: "策略与配置",
      items: [
        { href: "/yolo-quality-policies#trainingStandards", label: "训练质量标准" },
        { href: "/yolo-quality-policies#edgeStandards", label: "边端验证标准" },
        { href: "/yolo-quality-policies#releaseStandards", label: "发布门禁规则" },
        { href: "/edge-device-registry", label: "边端设备配置" },
        { href: "/aicm-publish-environments", label: "AICM 发布环境" },
        { href: "/strategy-integration-s3-config", label: "专家回流 S3 配置" },
        { href: "/yolo-eval-prompts", label: "评估提示词" },
        { href: "/annotation-settings", label: "预设与维度" },
        { href: "/ai-labeling-settings", label: "AI 标注配置" },
      ],
    },
  ];

  var TRAINING_HUB_TABS = [
    { id: "list", label: "训练任务" },
    { id: "autoTrainingPlan", label: "自动训练计划" },
    { id: "models", label: "PT 模型" },
    { id: "compile", label: "编译任务" },
    { id: "bmodels", label: "BModel 模型" },
    { id: "publish", label: "发布记录" },
    { id: "catalog", label: "已发布模型" },
  ];

  var TRAINING_HUB_TAB_URLS = {
    list: "/yolo-training?embedded=1&panel=list",
    autoTrainingPlan: "/yolo-training?embedded=1&panel=autoTrainingPlan",
    consistent: "/yolo-training?embedded=1&panel=consistent",
    models: "/yolo-training?embedded=1&panel=models",
    reports: "/yolo-training?embedded=1&panel=reports",
    compile: "/yolo-bm1688-exports?embedded=1",
    bmodels: "/yolo-bm1688-exports?embedded=1&view=bmodels",
    publish: "/yolo-model-publish?embedded=1",
    catalog: "/yolo-model-catalog?embedded=1",
  };

  var TRAINING_HUB_PAGES = ["yolo-training", "yolo-bm1688", "yolo-model-publish", "yolo-model-catalog"];

  var TRAINING_UI_TEXT = {
    auxEntryLabel: "其他设置",
    auxEntryNote: "这些入口用于策略配置和诊断；正式业务仍按六个一级菜单推进。",
    trainListDefaultHint: "",
    trainListFailurePrefix: "列表加载失败：",
    trainListFailureSuffix: "请重试或点「重置」。",
    trainListEmptyFilteredSuffix: "可点「重置」。",
    openTaskBlocked: "浏览器拦截了新窗口，请允许弹窗后重试。",
    bmExportLoadFailurePrefix: "列表加载失败：",
    bmExportLoadFailureSuffix: "请点击「刷新」重试。",
    bmExportKpiCards: {
      blocked: {
        label: "存在阻塞",
        hint: "当前筛选范围内被训练、编译、边端或发布门禁阻断的记录",
      },
      total: {
        label: "导出总数",
        hint: "符合当前筛选条件的 BM1688 量化模型记录",
      },
      running: {
        label: "进行中",
        hint: "当前页里仍在导出、编译或等待边端回传的记录",
      },
      publishReady: {
        label: "可进入发布评估",
        hint: "当前页里已经具备发布前评估条件的记录",
      },
    },
  };

  function isTerminalBoardValidationStatus(status) {
    var st = String(status || "").toUpperCase();
    return st === "COMPLETED" || st === "PARTIAL_COMPLETED" || st === "FAILED";
  }

  function countBmExportPendingBoard(row) {
    if (!row || row.status !== "COMPLETED") return false;
    return !isTerminalBoardValidationStatus(row.board_validation && row.board_validation.status);
  }

  function countBmExportPublishReady(row) {
    if (!row) return false;
    var verdict = String(row.current_verdict_label || "").trim();
    if (verdict) return verdict.indexOf("可进入发布评估") >= 0 || verdict.indexOf("可发布") >= 0;
    return String(row.edge_int8_eval_label || "").trim() === "通过";
  }

  function countBmExportBlocked(row) {
    if (!row) return false;
    var releaseCode = String(row.release_gate_assessment && row.release_gate_assessment.status_code || "").toLowerCase();
    var verdictCode = String(row.verdict_code || "").toLowerCase();
    return releaseCode === "blocked" || verdictCode.indexOf("failed") >= 0 || String(row.status || "").toUpperCase() === "FAILED";
  }

  function buildTrainListLoadFailureMessage(errorMessage) {
    var detail = String(errorMessage || "未知错误");
    return TRAINING_UI_TEXT.trainListFailurePrefix + detail + "，" + TRAINING_UI_TEXT.trainListFailureSuffix;
  }

  function buildBmExportLoadFailureMessage(errorMessage) {
    var detail = String(errorMessage || "未知错误");
    return TRAINING_UI_TEXT.bmExportLoadFailurePrefix + detail + "，" + TRAINING_UI_TEXT.bmExportLoadFailureSuffix;
  }

  function summarizeBmExportRows(rows, totalCount, errorMessage, serverSummary) {
    var cards = TRAINING_UI_TEXT.bmExportKpiCards;
    if (errorMessage) {
      var failHint = buildBmExportLoadFailureMessage(errorMessage);
      return {
        failed: true,
        cards: {
          blocked: { label: cards.blocked.label, hint: failHint, value: "—" },
          total: { label: cards.total.label, hint: failHint, value: "—" },
          running: { label: cards.running.label, hint: failHint, value: "—" },
          publishReady: { label: cards.publishReady.label, hint: failHint, value: "—" },
        },
      };
    }
    if (serverSummary && serverSummary.total != null) {
      return {
        failed: false,
        cards: {
          blocked: { label: cards.blocked.label, hint: cards.blocked.hint, value: Number(serverSummary.blocked || 0) },
          total: { label: cards.total.label, hint: cards.total.hint, value: Number(serverSummary.total || 0) },
          running: { label: cards.running.label, hint: cards.running.hint, value: Number(serverSummary.running || 0) },
          publishReady: { label: cards.publishReady.label, hint: cards.publishReady.hint, value: Number(serverSummary.publish_ready || 0) },
        },
      };
    }
    var blocked = 0;
    var running = 0;
    var publishReady = 0;
    (rows || []).forEach(function (row) {
      var boardStatus = String(row && row.board_validation && row.board_validation.status || "").toUpperCase();
      if (countBmExportBlocked(row)) blocked += 1;
      if (
        row &&
        (row.status === "PENDING" ||
          row.status === "PROCESSING" ||
          boardStatus === "PENDING" ||
          boardStatus === "PROCESSING")
      ) {
        running += 1;
      }
      if (countBmExportPublishReady(row)) publishReady += 1;
    });
    return {
      failed: false,
      cards: {
        blocked: { label: cards.blocked.label, hint: cards.blocked.hint, value: blocked },
        total: { label: cards.total.label, hint: cards.total.hint, value: totalCount != null ? totalCount : 0 },
        running: { label: cards.running.label, hint: cards.running.hint, value: running },
        publishReady: { label: cards.publishReady.label, hint: cards.publishReady.hint, value: publishReady },
      },
    };
  }

  function buildTrainListSectionHint(ctx) {
    ctx = ctx || {};
    if (ctx.errorMessage) {
      return buildTrainListLoadFailureMessage(ctx.errorMessage);
    }
    if (!ctx.filtered) return TRAINING_UI_TEXT.trainListDefaultHint;
    var criteria = ctx.criteria || "当前筛选";
    var total = ctx.total != null ? ctx.total : 0;
    if (total > 0) {
      return criteria + " · " + total + " 条";
    }
    return criteria + " · 0 条，" + TRAINING_UI_TEXT.trainListEmptyFilteredSuffix;
  }

  function buildTrainListActionFeedback(action, ctx) {
    ctx = ctx || {};
    var total = ctx.total != null ? ctx.total : 0;
    var filtered = !!ctx.filtered;
    var criteria = ctx.criteria || "";
    if (action === "search") {
      return filtered ? "已搜索，" + total + " 条；" + criteria : "已搜索，" + total + " 条";
    }
    if (action === "refresh") {
      return filtered ? "已刷新，" + total + " 条；" + criteria : "已刷新，" + total + " 条";
    }
    if (action === "filter") {
      return filtered ? "筛选已更新，" + total + " 条；" + criteria : "筛选已更新，" + total + " 条";
    }
    if (action === "reset") {
      return "筛选已重置，" + total + " 条";
    }
    return "";
  }

  function buildOpenTaskFeedback(taskId, taskName, blocked) {
    if (blocked) return TRAINING_UI_TEXT.openTaskBlocked;
    return "已打开任务 #" + taskId + (taskName ? "（" + taskName + "）" : "");
  }

  var TRAINING_UI_CONTRACTS = {
    text: TRAINING_UI_TEXT,
    isTerminalBoardValidationStatus: isTerminalBoardValidationStatus,
    countBmExportPendingBoard: countBmExportPendingBoard,
    countBmExportBlocked: countBmExportBlocked,
    countBmExportPublishReady: countBmExportPublishReady,
    buildTrainListLoadFailureMessage: buildTrainListLoadFailureMessage,
    buildBmExportLoadFailureMessage: buildBmExportLoadFailureMessage,
    summarizeBmExportRows: summarizeBmExportRows,
    buildTrainListSectionHint: buildTrainListSectionHint,
    buildTrainListActionFeedback: buildTrainListActionFeedback,
    buildOpenTaskFeedback: buildOpenTaskFeedback,
  };

  var TOOLKIT_SIDER_HINTS = {};

  function mountYoloToolkitShell() {
    /* 左侧工具链侧栏已移除：顶栏导航 + 页内 Tab 即可，避免与 embedded 子导航重复。 */
  }

  function requireAuthToken(message) {
    syncAuthBox();
    if (getAuthToken()) return true;
    try {
      window.dispatchEvent(
        new CustomEvent("labelu-auth-required", {
          detail: { message: message || "请先登录后再操作" },
        })
      );
    } catch (e) {}
    redirectToLogin();
    return false;
  }

  function renderYoloToolkitSider() {}

  function renderTrainingToolkitLinks() {}

  function isEmbeddedMode() {
    try {
      return /(?:^|[?&])embedded=1(?:&|$)/.test(location.search || "");
    } catch (e) {
      return false;
    }
  }

  /** 主站 iframe 内嵌训练页时，工具链链接须在顶层打开，否则 BM1688 等页挤在 iframe 里看不到列表。 */
  function isInMainFrame() {
    try {
      return window.top === window.self;
    } catch (e) {
      return false;
    }
  }

  function resolveAppUrl(url) {
    var target = url || "/";
    try {
      return new URL(target, window.location.href).toString();
    } catch (e) {
      return target;
    }
  }

  function navigateAppTo(url) {
    var target = resolveAppUrl(url);
    try {
      if (!isInMainFrame()) {
        window.top.location.href = target;
        return;
      }
    } catch (e) {}
    window.location.href = target;
  }

  function navigateTrainingHubTo(url) {
    var target = resolveAppUrl(url);
    var frame = window.frameElement;
    if (frame && frame.tagName === "IFRAME") {
      frame.src = target;
      return;
    }
    window.location.href = target;
  }

  function trainingHubActiveTab(pageKey) {
    if (pageKey === "yolo-bm1688") {
      try {
        return new URLSearchParams(location.search || "").get("view") === "bmodels" ? "bmodels" : "compile";
      } catch (e) {
        return "compile";
      }
    }
    if (pageKey === "yolo-model-publish") return "publish";
    if (pageKey === "yolo-model-catalog") return "catalog";
    if (pageKey === "yolo-training") {
      var panel = "list";
      try {
        panel = new URLSearchParams(location.search || "").get("panel") || "list";
      } catch (e) {}
      return ["list", "autoTrainingPlan", "models", "reports", "consistent"].indexOf(panel) >= 0 ? panel : "list";
    }
    return "list";
  }

  function switchTrainingHubTab(tabId) {
    var url = TRAINING_HUB_TAB_URLS[tabId];
    if (!url) return;
    if (isEmbeddedMode()) return navigateTrainingHubTo(url);
    navigateAppTo("/training");
  }

  function ensureTrainingSubNavMount() {
    if (!isEmbeddedMode()) return null;
    var pageKey = (document.body && document.body.getAttribute("data-lu-page")) || "";
    if (TRAINING_HUB_PAGES.indexOf(pageKey) < 0) return null;
    var mount = document.getElementById("luTrainingSubNavMount");
    if (mount) return mount;
    mount = document.createElement("div");
    mount.id = "luTrainingSubNavMount";
    var main =
      document.querySelector("[data-lu-toolkit-main]") ||
      document.querySelector(".page-main") ||
      document.querySelector("main.page") ||
      document.querySelector("main");
    if (!main) return null;
    main.insertBefore(mount, main.firstChild);
    return mount;
  }

  function setTrainingHubMoreOpen(open) {
    var menu = document.getElementById("luTrainingHubMoreMenu");
    var btn = document.getElementById("luTrainingHubMoreBtn");
    if (!menu || !btn) return;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.classList.toggle("active", open || btn.getAttribute("data-context-active") === "1");
  }

  function renderTrainingSubNav(activeTab) {
    if (!isEmbeddedMode()) return;
    var mount = ensureTrainingSubNavMount();
    if (!mount) return;
    var topNav = !isInMainFrame() || isEmbeddedMode();
    var hasActiveMainTab = TRAINING_HUB_TABS.some(function (t) { return t.id === activeTab; });
    var tabsHtml = TRAINING_HUB_TABS.map(function (t) {
      var selected = t.id === activeTab;
      var cls = "lu-hub-tab" + (selected ? " active" : "");
      return (
        '<button type="button" role="tab" aria-selected="' + (selected ? "true" : "false") +
        '" class="' + cls + '" data-hub-tab="' + esc(t.id) + '">' + esc(t.label) + "</button>"
      );
    }).join("");
    var otherHtml =
      '<div class="lu-training-hub-more-note">' + esc(TRAINING_UI_TEXT.auxEntryNote) + "</div>" +
      TRAINING_HUB_MORE_GROUPS.map(function (group) {
        var itemsHtml = (group.items || []).map(function (it) {
          if (it.tabId) {
            return (
              '<button type="button" role="menuitem" class="lu-training-hub-more-item lu-training-hub-more-button" data-hub-more-tab="' +
              esc(it.tabId) +
              '">' +
              esc(it.label) +
              "</button>"
            );
          }
          var href = topNav ? resolveAppUrl(it.href) : it.href;
          return (
            '<a role="menuitem" href="' +
            esc(href) +
            '" class="lu-training-hub-more-item" data-hub-more-href="' +
            esc(href) +
            '" target="_top" rel="noopener">' +
            esc(it.label) +
            "</a>"
          );
        }).join("");
        return (
          '<div class="lu-training-hub-more-group">' +
          '<div class="lu-training-hub-more-group-title">' +
          esc(group.title) +
          "</div>" +
          itemsHtml +
          "</div>"
        );
      }).join("");
    mount.innerHTML =
      '<nav class="lu-training-subnav" role="tablist" aria-label="模型训练工作流">' +
      tabsHtml +
      '<div class="lu-training-hub-more-wrap">' +
      '<button type="button" class="lu-hub-tab lu-training-hub-more-btn' + (!hasActiveMainTab ? " active" : "") +
      '" id="luTrainingHubMoreBtn" data-context-active="' + (!hasActiveMainTab ? "1" : "0") +
      '" aria-expanded="false" aria-haspopup="menu">' +
      esc(TRAINING_UI_TEXT.auxEntryLabel) +
      "</button>" +
      '<div class="lu-training-hub-more-menu" id="luTrainingHubMoreMenu" role="menu" hidden>' +
      otherHtml +
      "</div></div></nav>";
    mount.querySelectorAll("[data-hub-tab]").forEach(function (btn) {
      btn.onclick = function () {
        setTrainingHubMoreOpen(false);
        switchTrainingHubTab(btn.getAttribute("data-hub-tab"));
      };
    });
    var moreBtn = document.getElementById("luTrainingHubMoreBtn");
    if (moreBtn) {
      moreBtn.onclick = function (e) {
        e.stopPropagation();
        var menu = document.getElementById("luTrainingHubMoreMenu");
        setTrainingHubMoreOpen(menu && menu.hidden);
      };
    }
    mount.querySelectorAll("[data-hub-more-href]").forEach(function (a) {
      a.onclick = function (e) {
        e.preventDefault();
        setTrainingHubMoreOpen(false);
        navigateAppTo(a.getAttribute("data-hub-more-href"));
      };
    });
    mount.querySelectorAll("[data-hub-more-tab]").forEach(function (btn) {
      btn.onclick = function (e) {
        e.preventDefault();
        setTrainingHubMoreOpen(false);
        switchTrainingHubTab(btn.getAttribute("data-hub-more-tab"));
      };
    });
    if (!mount._hubClickBound) {
      mount._hubClickBound = true;
      document.addEventListener("click", function (e) {
        if (!e.target.closest(".lu-training-hub-more-wrap")) setTrainingHubMoreOpen(false);
      });
    }
  }

  function applyEmbeddedChrome() {
    if (!isEmbeddedMode()) return;
    document.documentElement.classList.add("labelu-embedded");
    if (document.body) document.body.classList.add("labelu-embedded");
    var header = document.querySelector(".labelu-shell-header");
    if (header) header.style.display = "none";
    var nav = document.querySelector(".labelu-shell-nav");
    if (nav) nav.style.display = "none";
    var hero = document.getElementById("luPageHero");
    if (hero) hero.style.display = "none";
    var sider = document.getElementById("luYoloToolkitSider");
    if (sider) sider.style.display = "none";
    var hint = document.getElementById("siderNextHint");
    if (hint) hint.style.display = "none";
    var legacyTabs = document.querySelector(".train-page-tabs");
    if (legacyTabs) legacyTabs.style.display = "none";
    var bmHint = document.getElementById("bmIframeHint");
    if (bmHint) bmHint.style.display = "none";
  }

  function shouldRedirectOnBootAuth() {
    var body = document.body;
    if (!body) return true;
    return body.getAttribute("data-lu-auth-redirect") !== "off";
  }

  function boot() {
    var pageKey = (document.body && document.body.getAttribute("data-lu-page")) || "";
    var navPageKey = pageKey;
    if (pageKey === "yolo-bm1688") {
      try {
        if (new URLSearchParams(location.search || "").get("view") === "bmodels") {
          navPageKey = "yolo-bm1688-bmodels";
        }
      } catch (e) {}
    }
    renderBranding();
    mountYoloToolkitShell(pageKey);
    applyEmbeddedChrome();
    renderTrainingSubNav(trainingHubActiveTab(pageKey));
    renderTrainingToolkitLinks();
    renderShellNav(navPageKey);
    renderPageHero(pageKey);
    syncAuthBox();
    if (pageKey) {
      ensureAuth({ redirect: shouldRedirectOnBootAuth() });
    }
    window.addEventListener("storage", function (e) {
      if (e.key === "token") {
        _authState.verified = false;
        _authState.ok = false;
        syncAuthBox();
        if (getShellToken()) {
          whenAuthReady(function () {});
        }
        try {
          window.dispatchEvent(
            new CustomEvent("labelu-token-changed", { detail: { token: getShellToken() } })
          );
        } catch (err) {}
      }
    });
  }

  global.LabeluPortal = {
    boot: boot,
    trainingUi: TRAINING_UI_CONTRACTS,
    isEmbeddedMode: isEmbeddedMode,
    isInMainFrame: isInMainFrame,
    navigateAppTo: navigateAppTo,
    navigateTrainingHubTo: navigateTrainingHubTo,
    switchTrainingHubTab: switchTrainingHubTab,
    renderTrainingSubNav: renderTrainingSubNav,
    getShellToken: getShellToken,
    getAuthToken: getAuthToken,
    setAuthToken: setAuthToken,
    normalizeBearer: normalizeBearer,
    requireAuthToken: requireAuthToken,
    syncAuthBox: syncAuthBox,
    verifySession: verifySession,
    ensureAuth: ensureAuth,
    whenAuthReady: whenAuthReady,
    redirectToLogin: redirectToLogin,
    loginRedirectUrl: loginRedirectUrl,
    handleUnauthorized: handleUnauthorized,
    renderTrainingToolkitLinks: renderTrainingToolkitLinks,
    renderShellNav: renderShellNav,
    renderPageHero: renderPageHero,
    renderTrainViewHero: renderTrainViewHero,
    updateSiderHint: updateSiderHint,
    renderYoloToolkitSider: renderYoloToolkitSider,
    mountYoloToolkitShell: mountYoloToolkitShell,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      trainingUi: TRAINING_UI_CONTRACTS,
      normalizeBearer: normalizeBearer,
    };
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})(typeof window !== "undefined" ? window : this);

