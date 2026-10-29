import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const repoRoot = path.resolve(import.meta.dirname, "../../..");

class Element {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attributes = {};
    this.style = {};
    this.textContent = "";
    this.className = "";
    this.id = "";
    this.type = "";
    this.onclick = null;
  }

  appendChild(child) {
    return this.insertBefore(child, null);
  }

  insertBefore(child, before) {
    if (child.parentElement) child.remove();
    child.parentElement = this;
    const index = before ? this.children.indexOf(before) : -1;
    if (index >= 0) this.children.splice(index, 0, child);
    else this.children.push(child);
    notifyMutation();
    return child;
  }

  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    if (index >= 0) siblings.splice(index, 1);
    this.parentElement = null;
    notifyMutation();
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
  }

  get innerHTML() {
    return this._innerHTML || "";
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = [];

    const idRe = /<([a-zA-Z0-9-]+)([^>]*)\sid="([^"]+)"([^>]*)>/g;
    let match;
    while ((match = idRe.exec(this._innerHTML))) {
      const child = new Element(match[1]);
      child.id = match[3];
      const attrs = `${match[2]} ${match[4]}`;
      const classMatch = attrs.match(/\sclass="([^"]+)"/);
      if (classMatch) child.className = classMatch[1];
      const dataCloseMatch = attrs.match(/\sdata-close="([^"]+)"/);
      if (dataCloseMatch) child.setAttribute("data-close", dataCloseMatch[1]);
      this.appendChild(child);
    }
  }

  getAttribute(name) {
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    return this.attributes[name] ?? null;
  }

  addEventListener() {}

  closest(selector) {
    if (!selector.startsWith(".")) return null;
    const cls = selector.slice(1);
    let el = this;
    while (el) {
      if (classList(el).includes(cls)) return el;
      el = el.parentElement;
    }
    return null;
  }

  get nextSibling() {
    return this.nextElementSibling;
  }

  get nextElementSibling() {
    if (!this.parentElement) return null;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    return index >= 0 ? siblings[index + 1] || null : null;
  }
}

class Document {
  constructor() {
    this.readyState = "complete";
    this.body = new Element("body");
    this.head = new Element("head");
    this.title = "Task | LabelU";
  }

  createElement(tagName) {
    return new Element(tagName);
  }

  addEventListener() {}

  getElementById(id) {
    return allElements(this).find((el) => el.id === id) || null;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    if (selector.startsWith(".")) {
      const cls = selector.slice(1);
      return allElements(this).filter((el) => classList(el).includes(cls));
    }
    if (selector === "button, a.ant-btn, span.ant-btn, .ant-btn, [role='button']") {
      return allElements(this).filter((el) => {
        if (el.tagName === "BUTTON") return true;
        return classList(el).includes("ant-btn") || el.getAttribute("role") === "button";
      });
    }
    return [];
  }
}

function classList(el) {
  return String(el.className || "").split(/\s+/).filter(Boolean);
}

function allElements(document) {
  const out = [];
  const visit = (node) => {
    out.push(node);
    for (const child of node.children) visit(child);
  };
  visit(document.head);
  visit(document.body);
  return out;
}

const observerCallbacks = new Set();
let notifying = false;
function notifyMutation() {
  if (notifying) return;
  notifying = true;
  try {
    for (const cb of observerCallbacks) cb();
  } finally {
    notifying = false;
  }
}

function makeContext(pathname = "/tasks/1/samples/2", aiButtonText = "AI标注") {
  observerCallbacks.clear();
  const document = new Document();
  const toolbar = document.createElement("div");
  toolbar.style.display = "flex";
  const ai = document.createElement("button");
  ai.className = "ant-btn";
  ai.textContent = aiButtonText;
  toolbar.appendChild(ai);
  document.body.appendChild(toolbar);

  const context = {
    document,
    location: { pathname },
    window: {
      getComputedStyle: () => ({
        display: "flex",
        minHeight: "32px",
        padding: "4px 12px",
        borderRadius: "6px",
        fontSize: "14px",
        fontWeight: "400",
        lineHeight: "22px",
        fontFamily: "system-ui",
      }),
      addEventListener() {},
      setTimeout: (fn) => {
        fn();
        return 1;
      },
      clearTimeout() {},
      setInterval: () => 1,
      clearInterval() {},
      location: { reload() {} },
      history: {
        pushState() {},
        replaceState() {},
      },
    },
    MutationObserver: class {
      constructor(cb) {
        this.cb = cb;
      }
      observe() {
        observerCallbacks.add(this.cb);
      }
    },
    localStorage: { getItem: () => "", setItem() {}, removeItem() {} },
    fetch: () => Promise.reject(new Error("unused")),
    setTimeout: (fn) => {
      fn();
      return 1;
    },
    clearTimeout() {},
    setInterval: () => 1,
    clearInterval() {},
  };
  context.window.document = document;
  return { context, toolbar };
}

function loadScript(context, relativePath) {
  const code = fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
  vm.runInNewContext(code, context, { filename: relativePath });
}

function runScenario(scripts) {
  const { context, toolbar } = makeContext();
  for (const script of scripts) loadScript(context, script);

  for (const cb of observerCallbacks) {
    cb();
    cb();
  }

  assert.deepEqual(
    toolbar.children.map((el) => el.textContent),
    ["AI标注", "YOLO预标注", "全局标签预标注"],
  );
}

function runTagtoolVisibilityScenario(pathname, aiButtonText, expectedButtonText) {
  const { context, toolbar } = makeContext(pathname, aiButtonText);
  loadScript(context, "frontend/extensions/assets/task_tagtool_preannotate_entry.js");
  for (const cb of observerCallbacks) cb();
  assert.deepEqual(
    toolbar.children.map((el) => el.textContent),
    expectedButtonText ? [aiButtonText, expectedButtonText] : [aiButtonText],
  );
}

function runYoloSampleRouteRefreshScenario() {
  const { context } = makeContext("/tasks/1/samples/2", "AI标注");
  loadScript(context, "frontend/extensions/assets/task_yolo_preannotate_entry.js");
  for (const cb of observerCallbacks) cb();

  let button = context.document.querySelector(".labelu-yolo-preannotate-entry");
  assert.ok(button);
  assert.equal(button.getAttribute("data-sample"), "2");

  context.location.pathname = "/tasks/1/samples/3";
  for (const cb of observerCallbacks) cb();

  button = context.document.querySelector(".labelu-yolo-preannotate-entry");
  assert.ok(button);
  assert.equal(button.getAttribute("data-sample"), "3");
}

runScenario(
  [
    "frontend/extensions/assets/task_yolo_preannotate_entry.js",
    "frontend/extensions/assets/task_tagtool_preannotate_entry.js",
  ],
);

runTagtoolVisibilityScenario("/tasks/1", "AI批量标注", "全局标签批量");
runTagtoolVisibilityScenario("/tasks/1/edit", "AI批量标注", null);
runTagtoolVisibilityScenario("/tasks/1/samples/2", "AI标注", "全局标签预标注");
runYoloSampleRouteRefreshScenario();

{
  const { context } = makeContext();
  loadScript(context, "frontend/extensions/assets/task_locateanything_preannotate_entry.js");
  for (const cb of observerCallbacks) cb();
  const button = context.document.querySelector(".labelu-locateanything-preannotate-entry");
  assert.ok(button);
  button.onclick({ preventDefault() {}, stopPropagation() {} });

  assert.equal(context.document.getElementById("lam-token"), null);
  assert.doesNotMatch(
    context.document.getElementById("labelu-la-preannotate-modal-root").innerHTML,
    /Authorization|API Key|Endpoint|provider/i,
  );
}
runScenario(
  [
    "frontend/extensions/assets/task_tagtool_preannotate_entry.js",
    "frontend/extensions/assets/task_yolo_preannotate_entry.js",
  ],
);

{
  const entry = fs.readFileSync(
    path.join(repoRoot, "frontend/extensions/assets/task_yolo_preannotate_entry.js"),
    "utf8",
  );
  const page = fs.readFileSync(
    path.join(repoRoot, "frontend/extensions/pages/yolo_preannotate.html"),
    "utf8",
  );
  assert.match(entry, /applyRecommendedConf\(item, "lym-conf"\)/);
  assert.match(entry, /applyRecommendedConf\(it, "lym-conf"\)/);
  assert.match(page, /applyRecommendedConf\(item\)/);
  assert.match(entry, /建议置信度/);
  assert.match(page, /建议置信度/);
  assert.match(entry, /recommended_conf == null/);
  assert.match(page, /recommended_conf == null/);
  assert.match(entry, /可使用 .* 个共有标签/);
  assert.match(entry, /模型多出的类别自动忽略/);
  assert.match(entry, /需人工补充 .* 个标签/);
  assert.match(entry, /模型分类/);
  assert.doesNotMatch(entry, /按预设分类|lym-preset/);
  assert.doesNotMatch(page, /预设分类|modelPresetSelect|modelPresetWrap/);
}
