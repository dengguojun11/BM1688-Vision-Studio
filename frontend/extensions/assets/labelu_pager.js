/**
 * LabelU 扩展页通用分页：每页条数、跳转页码，默认 10 条/页。
 */
(function (global) {
  "use strict";

  var DEFAULT_SIZE = 10;
  var SIZE_OPTIONS = [10, 20, 50, 100];

  function resolveEl(containerOrId) {
    if (!containerOrId) return null;
    if (typeof containerOrId === "string") return document.getElementById(containerOrId);
    return containerOrId;
  }

  function maxPageIndex(total, size) {
    var s = Math.max(1, parseInt(size, 10) || DEFAULT_SIZE);
    return Math.max(0, Math.ceil((total || 0) / s) - 1);
  }

  function clampPage(page, total, size) {
    var maxP = maxPageIndex(total, size);
    var p = parseInt(page, 10);
    if (isNaN(p) || p < 0) p = 0;
    if (p > maxP) p = maxP;
    return p;
  }

  function buildInner(showSize, showJump, sizeOptions) {
    var opts = (sizeOptions || SIZE_OPTIONS)
      .map(function (n) {
        return '<option value="' + n + '">' + n + "</option>";
      })
      .join("");
    var sizeHtml = showSize
      ? '<span class="lu-pager-size">' +
        '<label class="lu-pager-size-label">每页</label>' +
        '<select class="lu-pager-size-select" aria-label="每页条数">' +
        opts +
        "</select>" +
        '<span class="lu-pager-size-unit">条</span></span>'
      : "";
    var jumpHtml = showJump
      ? '<span class="lu-pager-jump">' +
        '<label class="lu-pager-jump-label">跳至</label>' +
        '<input type="number" class="lu-pager-jump-input" min="1" step="1" aria-label="页码" />' +
        '<span class="lu-pager-jump-unit">页</span>' +
        '<button type="button" class="lu-pager-btn lu-pager-jump-btn">确定</button></span>'
      : "";
    return (
      '<div class="lu-pager-inner">' +
      '<span class="lu-pager-meta"></span>' +
      '<span class="lu-pager-nav">' +
      '<button type="button" class="lu-pager-btn lu-pager-prev">上一页</button>' +
      '<span class="lu-pager-page"></span>' +
      '<button type="button" class="lu-pager-btn lu-pager-next">下一页</button>' +
      "</span>" +
      sizeHtml +
      jumpHtml +
      "</div>"
    );
  }

  /**
   * @param {HTMLElement|string} containerOrId
   * @param {object} opts
   * @param {number} opts.page - 0-based
   * @param {number} [opts.size=10]
   * @param {number} [opts.total=0]
   * @param {function} opts.onChange - ({ page, size, reason })
   * @param {boolean} [opts.showSize=true]
   * @param {boolean} [opts.showJump=true]
   * @param {number[]} [opts.sizeOptions]
   */
  function mount(containerOrId, opts) {
    opts = opts || {};
    var el = resolveEl(containerOrId);
    if (!el) return null;

    var state = {
      page: clampPage(opts.page || 0, opts.total || 0, opts.size || DEFAULT_SIZE),
      size: parseInt(opts.size, 10) || DEFAULT_SIZE,
      total: opts.total || 0,
    };
    var showSize = opts.showSize !== false;
    var showJump = opts.showJump !== false;
    var sizeOptions = opts.sizeOptions || SIZE_OPTIONS;
    var onChange = typeof opts.onChange === "function" ? opts.onChange : function () {};

    el.classList.add("pager", "lu-pager");
    el.innerHTML = buildInner(showSize, showJump, sizeOptions);

    var metaEl = el.querySelector(".lu-pager-meta");
    var pageEl = el.querySelector(".lu-pager-page");
    var btnPrev = el.querySelector(".lu-pager-prev");
    var btnNext = el.querySelector(".lu-pager-next");
    var sizeSel = el.querySelector(".lu-pager-size-select");
    var jumpInput = el.querySelector(".lu-pager-jump-input");
    var jumpBtn = el.querySelector(".lu-pager-jump-btn");

    function emit(nextPage, nextSize, reason) {
      state.page = clampPage(nextPage, state.total, nextSize);
      state.size = Math.max(1, parseInt(nextSize, 10) || DEFAULT_SIZE);
      paint();
      onChange({ page: state.page, size: state.size, reason: reason || "page" });
    }

    function paint() {
      var maxP = maxPageIndex(state.total, state.size);
      state.page = clampPage(state.page, state.total, state.size);
      if (metaEl) metaEl.textContent = "共 " + state.total + " 条";
      if (pageEl) {
        pageEl.textContent =
          "第 " + (state.page + 1) + " / " + (maxP + 1) + " 页";
      }
      if (btnPrev) btnPrev.disabled = state.page <= 0;
      if (btnNext) btnNext.disabled = state.page >= maxP || state.total === 0;
      if (sizeSel) sizeSel.value = String(state.size);
      if (jumpInput) {
        jumpInput.max = String(maxP + 1);
        jumpInput.placeholder = String(state.page + 1);
      }
    }

    if (btnPrev) {
      btnPrev.onclick = function () {
        if (state.page > 0) emit(state.page - 1, state.size, "page");
      };
    }
    if (btnNext) {
      btnNext.onclick = function () {
        var maxP = maxPageIndex(state.total, state.size);
        if (state.page < maxP) emit(state.page + 1, state.size, "page");
      };
    }
    if (sizeSel) {
      sizeSel.onchange = function () {
        emit(0, parseInt(sizeSel.value, 10) || DEFAULT_SIZE, "size");
      };
    }
    if (jumpBtn && jumpInput) {
      function doJump() {
        var raw = parseInt(jumpInput.value, 10);
        if (isNaN(raw) || raw < 1) return;
        emit(raw - 1, state.size, "jump");
        jumpInput.value = "";
      }
      jumpBtn.onclick = doJump;
      jumpInput.onkeydown = function (e) {
        if (e.key === "Enter") doJump();
      };
    }

    paint();

    return {
      update: function (next) {
        if (next.page != null) state.page = next.page;
        if (next.size != null) state.size = next.size;
        if (next.total != null) state.total = next.total;
        paint();
      },
      getState: function () {
        return { page: state.page, size: state.size, total: state.total };
      },
      destroy: function () {
        el.innerHTML = "";
        el.classList.remove("lu-pager");
      },
    };
  }

  global.LuPager = {
    DEFAULT_SIZE: DEFAULT_SIZE,
    SIZE_OPTIONS: SIZE_OPTIONS,
    mount: mount,
    clampPage: clampPage,
    maxPageIndex: maxPageIndex,
  };
})(typeof window !== "undefined" ? window : this);
