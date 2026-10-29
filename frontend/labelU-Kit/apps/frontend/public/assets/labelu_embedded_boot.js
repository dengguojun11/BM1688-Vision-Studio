/**
 * 在 CSS/portal 加载前同步标记 embedded=1，避免 iframe 内重复顶栏闪动。
 */
(function () {
  "use strict";
  try {
    if (!/(?:^|[?&])embedded=1(?:&|$)/.test(location.search || "")) return;
    document.documentElement.classList.add("labelu-embedded");
    function markBody() {
      if (document.body) document.body.classList.add("labelu-embedded");
    }
    if (document.body) markBody();
    else document.addEventListener("DOMContentLoaded", markBody);
  } catch (e) {}
})();
