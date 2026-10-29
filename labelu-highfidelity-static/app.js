const state = {
  page: "overview",
  trainingId: "job-534",
  policyId: "policy-night",
  validationId: "val-534",
  releaseId: "rel-534",
  libraryId: "model-534",
  trainingTab: "metrics",
  validationTab: "clusters",
  modal: null
};

const overviewCards = [
  { label: "活跃训练任务", value: "07", note: "+2 today" },
  { label: "待修复验证", value: "03", note: "2 edge / 1 export" },
  { label: "可放行模型", value: "04", note: "2 staging / 2 prod-ready" },
  { label: "在线节点", value: "12", note: "GPU 8 / BM1688 4" }
];

const overviewQueues = [
  {
    id: "job-534",
    name: "garbageStreet / v4",
    lane: "训练中",
    tone: "amber",
    progress: 68,
    tags: ["night", "bulky", "bm1688"]
  },
  {
    id: "job-528",
    name: "roadsideMixed / v2",
    lane: "待发布",
    tone: "green",
    progress: 91,
    tags: ["release-candidate", "int8", "city-east"]
  },
  {
    id: "job-512",
    name: "vendorBox / v1",
    lane: "边端阻塞",
    tone: "red",
    progress: 42,
    tags: ["validation-blocked", "sun-glare", "review-needed"]
  }
];

const trainingJobs = [
  {
    id: "job-534",
    name: "garbageStreet / v4",
    status: "PROCESSING",
    tone: "amber",
    owner: "环境治理组",
    version: "run_2026_07_09_534",
    metrics: { map5095: 0.61, map50: 0.82, precision: 0.79, recall: 0.74 },
    dataset: { images: 18240, classes: 6, hardNegatives: 1260, coverage: "night + roadside + scattered" },
    artifacts: ["best.pt", "last.pt", "analysis bundle", "gt-pred compare"],
    failures: ["night glare", "bulky far-angle", "external mix false positive"],
    history: [28, 34, 40, 48, 55, 61, 67],
    readiness: 72,
    edge: 61,
    release: 44
  },
  {
    id: "job-528",
    name: "roadsideMixed / v2",
    status: "COMPLETED",
    tone: "green",
    owner: "巡检算法组",
    version: "run_2026_07_08_528",
    metrics: { map5095: 0.69, map50: 0.87, precision: 0.84, recall: 0.79 },
    dataset: { images: 11600, classes: 5, hardNegatives: 920, coverage: "day + mixed roadside" },
    artifacts: ["best.pt", "onnx", "bmodel", "quality bundle"],
    failures: ["tiny can", "rainy scarcity"],
    history: [36, 41, 50, 59, 64, 67, 69],
    readiness: 83,
    edge: 76,
    release: 71
  },
  {
    id: "job-512",
    name: "vendorBox / v1",
    status: "WAIT_VALIDATE",
    tone: "red",
    owner: "街面感知组",
    version: "run_2026_07_06_512",
    metrics: { map5095: 0.57, map50: 0.78, precision: 0.73, recall: 0.66 },
    dataset: { images: 8940, classes: 4, hardNegatives: 360, coverage: "mixed box / inverse light" },
    artifacts: ["best.pt", "analysis bundle"],
    failures: ["inverse light", "box clutter", "background confusion"],
    history: [22, 31, 39, 45, 49, 53, 57],
    readiness: 66,
    edge: 39,
    release: 18
  }
];

const policies = [
  {
    id: "policy-night",
    name: "夜间垃圾暴露",
    tone: "green",
    mode: "ACTIVE",
    scene: "night / roadside / exposure",
    weights: [
      ["训练质量", 35],
      ["泛化质量", 20],
      ["边端可用", 35],
      ["发布就绪", 10]
    ],
    triggers: ["反光误报 > 12%", "night recall < 0.72", "bulky miss > 7%"],
    actions: ["提权 negative samples", "强制边端复验", "导入回标清单"]
  },
  {
    id: "policy-day",
    name: "白天混合路侧",
    tone: "amber",
    mode: "DRAFT",
    scene: "day / mixed / roadside",
    weights: [
      ["训练质量", 30],
      ["泛化质量", 25],
      ["边端可用", 25],
      ["发布就绪", 20]
    ],
    triggers: ["tiny can precision < 0.8", "source drift high"],
    actions: ["补雨天样本", "开启版本对比", "锁定导出回归"]
  },
  {
    id: "policy-int8",
    name: "INT8 快速交付",
    tone: "red",
    mode: "REVIEW",
    scene: "bm1688 / export / int8",
    weights: [
      ["训练质量", 20],
      ["泛化质量", 15],
      ["边端可用", 50],
      ["发布就绪", 15]
    ],
    triggers: ["quant drop > 8%", "edge score < 70", "threshold unstable"],
    actions: ["自动推送修正", "禁止直发 prod", "生成差异摘要"]
  }
];

const validations = [
  {
    id: "val-534",
    name: "Export #534 / BM1688 INT8",
    tone: "amber",
    verdict: "NEEDS_REPAIR",
    score: 64,
    thresholds: { conf: 0.38, nms: 0.52, match: 0.50 },
    clusters: [
      ["night_glare", 29],
      ["bulky_far", 18],
      ["external_mix", 12]
    ],
    samples: ["s_1480.png", "s_7611.png", "s_9921.png"],
    notes: ["AI recommended thresholds available", "second-stage refine pending"]
  },
  {
    id: "val-528",
    name: "Export #528 / ONNX baseline",
    tone: "green",
    verdict: "PASSED",
    score: 82,
    thresholds: { conf: 0.42, nms: 0.50, match: 0.52 },
    clusters: [
      ["tiny_can", 9],
      ["rainy_roadside", 5],
      ["dump_shadow", 4]
    ],
    samples: ["s_313.png", "s_421.png", "s_1182.png"],
    notes: ["publishable", "best current baseline"]
  },
  {
    id: "val-512",
    name: "Export #512 / mixed upload",
    tone: "red",
    verdict: "BLOCKED",
    score: 38,
    thresholds: { conf: 0.35, nms: 0.56, match: 0.48 },
    clusters: [
      ["background_confusion", 33],
      ["inverse_light", 19],
      ["dump_false_alarm", 17]
    ],
    samples: ["s_022.png", "s_079.png", "s_845.png"],
    notes: ["relabel required", "do not publish"]
  }
];

const releases = [
  {
    id: "rel-534",
    name: "Model 534 / garbageStreet",
    tone: "amber",
    gate: "HOLD",
    environment: "prod-city-east",
    version: "aicm_model_534",
    checks: [
      ["训练完成", true],
      ["导出完成", true],
      ["边端验证通过", false],
      ["发布摘要生成", false],
      ["正式版对比完成", false]
    ],
    blockers: ["night glare false positive", "no publish summary", "no last-release compare"]
  },
  {
    id: "rel-528",
    name: "Model 528 / roadsideMixed",
    tone: "green",
    gate: "READY",
    environment: "staging",
    version: "aicm_model_528",
    checks: [
      ["训练完成", true],
      ["导出完成", true],
      ["边端验证通过", true],
      ["发布摘要生成", true],
      ["正式版对比完成", true]
    ],
    blockers: ["await grayscale rollout window"]
  },
  {
    id: "rel-512",
    name: "Model 512 / vendorBox",
    tone: "red",
    gate: "BLOCKED",
    environment: "prod-city-west",
    version: "aicm_model_512",
    checks: [
      ["训练完成", true],
      ["导出完成", false],
      ["边端验证通过", false],
      ["发布摘要生成", false],
      ["正式版对比完成", false]
    ],
    blockers: ["export missing", "validation missing", "release explanation missing"]
  }
];

const library = [
  {
    id: "model-534",
    name: "garbageStreet / 534",
    state: "Candidate",
    tone: "amber",
    assets: ["best.pt", "onnx", "bmodel-int8", "qa-bundle"],
    source: "training job 534",
    tags: ["night", "bm1688", "candidate"]
  },
  {
    id: "model-528",
    name: "roadsideMixed / 528",
    state: "Release",
    tone: "green",
    assets: ["best.pt", "onnx", "bmodel-int8", "publish-manifest"],
    source: "training job 528",
    tags: ["release", "staging", "baseline"]
  },
  {
    id: "model-451",
    name: "imported / city-base-451",
    state: "Imported",
    tone: "green",
    assets: ["imported.pt", "metadata.json"],
    source: "external import",
    tags: ["imported", "city-base"]
  }
];

const sideInsights = {
  overview: [
    ["当前最强入口", "发布门禁"],
    ["最该新增", "失败簇分析"],
    ["最该改页", "训练中心"],
    ["产品重心", "模型交付闭环"]
  ],
  training: [
    ["当前选中", "job-534"],
    ["风险主题", "night glare"],
    ["推荐动作", "发起边端复验"],
    ["下一联动", "质量策略中心"]
  ],
  policies: [
    ["当前策略", "night exposure"],
    ["作用模型", "534 / 512"],
    ["命中类型", "night + glare"],
    ["建议补齐", "版本管理"]
  ],
  validation: [
    ["当前任务", "val-534"],
    ["主修方向", "AI refine"],
    ["风险簇", "bulky_far"],
    ["联动目标", "publish hold"]
  ],
  release: [
    ["当前候选", "rel-534"],
    ["门禁状态", "HOLD"],
    ["缺失项", "summary / compare"],
    ["目标动作", "补齐发布摘要"]
  ],
  library: [
    ["资产主线", "train -> export -> publish"],
    ["重点价值", "跨阶段追踪"],
    ["建议增强", "版本对比"],
    ["建议联动", "发布页 / 验证页"]
  ]
};

const pageMeta = {
  overview: { kicker: "Program Board", title: "总控台" },
  training: { kicker: "Training Hub", title: "训练中心" },
  policies: { kicker: "Policy Studio", title: "策略中心" },
  validation: { kicker: "Edge Validation", title: "边端验证" },
  release: { kicker: "Publish Gate", title: "发布交付" },
  library: { kicker: "Model Library", title: "模型资产" }
};

function toneBadge(tone, text) {
  const map = { green: "green", amber: "amber", red: "red" };
  return `<span class="badge ${map[tone] || ""}">${text}</span>`;
}

function progressBar(value, tone = "") {
  return `
    <div class="bar-track">
      <div class="bar-fill ${tone}" style="width:${value}%"></div>
    </div>
  `;
}

function renderCommandStrip() {
  return overviewCards.map((item) => `
    <article class="metric-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
      <small>${item.note}</small>
    </article>
  `).join("");
}

function renderOverviewMain() {
  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Live Program</small>
          <strong>Program Board</strong>
        </div>
        <div class="panel-actions">
          <button class="chip active">全部</button>
          <button class="chip">仅阻塞</button>
          <button class="chip">待发布</button>
        </div>
      </div>

      <div class="stat-rack">
        <article class="stat-tile">
          <small>运行中训练</small>
          <div class="value">07</div>
          <div class="row-meta"><span class="micro-tag">GPU 5</span><span class="micro-tag">Remote 2</span></div>
        </article>
        <article class="stat-tile">
          <small>待修复验证</small>
          <div class="value">03</div>
          <div class="row-meta"><span class="micro-tag">night glare</span><span class="micro-tag">threshold drift</span></div>
        </article>
        <article class="stat-tile">
          <small>待发布候选</small>
          <div class="value">04</div>
          <div class="row-meta"><span class="micro-tag">staging 2</span><span class="micro-tag">prod 2</span></div>
        </article>
        <article class="stat-tile">
          <small>在线资源</small>
          <div class="value">12</div>
          <div class="row-meta"><span class="micro-tag">BM1688 4</span><span class="micro-tag">AICM 3</span></div>
        </article>
      </div>
    </div>

    <div class="surface-block">
      <div class="split-panel">
        <article class="chart-card">
          <div class="sheet-head">
            <div>
              <small>Training / Validation / Release trend</small>
              <strong>七日趋势</strong>
            </div>
            <div class="row-meta">
              <span class="micro-tag">train</span>
              <span class="micro-tag">edge</span>
              <span class="micro-tag">release</span>
            </div>
          </div>
          <svg class="chart-svg" viewBox="0 0 620 220" aria-hidden="true">
            <line class="chart-gridline" x1="40" y1="30" x2="600" y2="30"></line>
            <line class="chart-gridline" x1="40" y1="80" x2="600" y2="80"></line>
            <line class="chart-gridline" x1="40" y1="130" x2="600" y2="130"></line>
            <line class="chart-gridline" x1="40" y1="180" x2="600" y2="180"></line>
            <text class="axis-label" x="24" y="34">90</text>
            <text class="axis-label" x="24" y="84">70</text>
            <text class="axis-label" x="24" y="134">50</text>
            <text class="axis-label" x="24" y="184">30</text>
            <path class="line-a" d="M50 152 L130 144 L210 129 L290 114 L370 106 L450 98 L530 84"></path>
            <path class="line-b" d="M50 164 L130 158 L210 151 L290 140 L370 131 L450 122 L530 110"></path>
            <path class="line-c" d="M50 175 L130 170 L210 162 L290 153 L370 145 L450 132 L530 120"></path>
            <circle class="chart-point" cx="530" cy="84" r="4"></circle>
            <circle class="chart-point" cx="530" cy="110" r="4"></circle>
            <circle class="chart-point" cx="530" cy="120" r="4"></circle>
            <text class="axis-label" x="46" y="206">07-03</text>
            <text class="axis-label" x="126" y="206">07-04</text>
            <text class="axis-label" x="206" y="206">07-05</text>
            <text class="axis-label" x="286" y="206">07-06</text>
            <text class="axis-label" x="366" y="206">07-07</text>
            <text class="axis-label" x="446" y="206">07-08</text>
            <text class="axis-label" x="526" y="206">07-09</text>
          </svg>
        </article>

        <article class="sheet-card">
          <div class="sheet-head">
            <div>
              <small>Gate Monitor</small>
              <strong>门禁风险分布</strong>
            </div>
          </div>
          <table class="dense-table">
            <thead>
              <tr><th>Model</th><th>Gate</th><th>Owner</th></tr>
            </thead>
            <tbody>
              <tr data-page-jump="release" data-release="rel-534"><td>534</td><td>HOLD</td><td>环境治理组</td></tr>
              <tr data-page-jump="release" data-release="rel-528"><td>528</td><td>READY</td><td>巡检算法组</td></tr>
              <tr data-page-jump="release" data-release="rel-512"><td>512</td><td>BLOCKED</td><td>街面感知组</td></tr>
              <tr><td>451</td><td>IMPORTED</td><td>平台共享</td></tr>
            </tbody>
          </table>
        </article>
      </div>
    </div>

    <div class="surface-block">
      <div class="grid-3">
        ${overviewQueues.map((item) => `
          <button class="row-card ${state.trainingId === item.id ? "active" : ""}" data-page-jump="training" data-training="${item.id}">
            <div class="row-head">
              <div class="row-title">
                <strong>${item.name}</strong>
                <span class="muted">${item.lane}</span>
              </div>
              ${toneBadge(item.tone, item.lane)}
            </div>
            ${progressBar(item.progress, item.tone)}
            <div class="row-meta">
              ${item.tags.map((tag) => `<span class="micro-tag">${tag}</span>`).join("")}
            </div>
          </button>
        `).join("")}
      </div>
    </div>

    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Queue Monitor</small>
          <strong>运行队列</strong>
        </div>
        <button class="chip" data-open-modal="command">新建运行</button>
      </div>

      <div class="queue-table">
        ${trainingJobs.map((job) => `
          <button class="queue-row ${state.trainingId === job.id ? "active" : ""}" data-page-jump="training" data-training="${job.id}">
            <div>
              <strong>${job.name}</strong>
              <small>${job.owner} · ${job.version}</small>
              <div class="row-meta">
                <span class="micro-tag">mAP50-95 ${(job.metrics.map5095 * 100).toFixed(0)}%</span>
                <span class="micro-tag">Edge ${job.edge}%</span>
                <span class="micro-tag">Release ${job.release}%</span>
              </div>
            </div>
            ${toneBadge(job.tone, job.status)}
          </button>
        `).join("")}
      </div>
    </div>

    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Ops Snapshot</small>
          <strong>操作快照</strong>
        </div>
      </div>

      <div class="grid-4">
        <article class="small-card">
          <strong>Night glare risk</strong>
          <div class="value">29</div>
          <p>failure clusters</p>
        </article>
        <article class="small-card">
          <strong>Publish hold</strong>
          <div class="value">03</div>
          <p>release candidates blocked</p>
        </article>
        <article class="small-card">
          <strong>Quant drop watch</strong>
          <div class="value">08%</div>
          <p>max recent export delta</p>
        </article>
        <article class="small-card">
          <strong>Ready baseline</strong>
          <div class="value">528</div>
          <p>best stable release line</p>
        </article>
      </div>
    </div>
  `;
}

function renderTrainingMain() {
  const job = trainingJobs.find((item) => item.id === state.trainingId);
  const tabs = [
    ["metrics", "Metrics"],
    ["dataset", "Dataset"],
    ["artifacts", "Artifacts"],
    ["failures", "Failures"]
  ];

  let tabContent = "";
  if (state.trainingTab === "metrics") {
    tabContent = `
      <div class="detail-grid">
        <article class="detail-stat"><small>mAP50-95</small><strong>${(job.metrics.map5095 * 100).toFixed(0)}%</strong></article>
        <article class="detail-stat"><small>Precision</small><strong>${(job.metrics.precision * 100).toFixed(0)}%</strong></article>
        <article class="detail-stat"><small>Recall</small><strong>${(job.metrics.recall * 100).toFixed(0)}%</strong></article>
      </div>
      <div class="detail-section">
        <h3>Epoch Trend</h3>
        <div class="sparkline">${job.history.map((v) => `<span style="height:${v}%"></span>`).join("")}</div>
      </div>
    `;
  } else if (state.trainingTab === "dataset") {
    tabContent = `
      <div class="detail-grid">
        <article class="detail-stat"><small>Images</small><strong>${job.dataset.images}</strong></article>
        <article class="detail-stat"><small>Classes</small><strong>${job.dataset.classes}</strong></article>
        <article class="detail-stat"><small>Hard Negatives</small><strong>${job.dataset.hardNegatives}</strong></article>
      </div>
      <div class="detail-section">
        <h3>Coverage</h3>
        <p>${job.dataset.coverage}</p>
      </div>
    `;
  } else if (state.trainingTab === "artifacts") {
    tabContent = `
      <div class="detail-section">
        <h3>Artifacts</h3>
        <ul>${job.artifacts.map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
    `;
  } else {
    tabContent = `
      <div class="detail-section">
        <h3>Failure Clusters</h3>
        <ul>${job.failures.map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
    `;
  }

  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Training Queue</small>
          <strong>Training Hub</strong>
        </div>
        <div class="nav-tabs">
          ${tabs.map(([id, label]) => `<button class="nav-tab ${state.trainingTab === id ? "active" : ""}" data-training-tab="${id}">${label}</button>`).join("")}
        </div>
      </div>

      <div class="board-layout">
        <div class="left-column">
          <div class="row-stack">
            ${trainingJobs.map((item) => `
              <button class="row-card ${state.trainingId === item.id ? "active" : ""}" data-training="${item.id}">
                <div class="row-head">
                  <div class="row-title">
                    <strong>${item.name}</strong>
                    <span class="muted">${item.owner}</span>
                  </div>
                  ${toneBadge(item.tone, item.status)}
                </div>
                <div class="row-meta">
                  <span class="micro-tag">Ready ${item.readiness}%</span>
                  <span class="micro-tag">Edge ${item.edge}%</span>
                  <span class="micro-tag">Release ${item.release}%</span>
                </div>
              </button>
            `).join("")}
          </div>
        </div>

        <div class="detail-card">
          <div class="detail-top">
            <div>
              <small>${job.version}</small>
              <h2>${job.name}</h2>
              <div class="detail-meta">${job.owner}</div>
            </div>
            <div class="detail-actions">
              <button class="ghost-btn">Create Iteration</button>
              <button class="ghost-btn" data-page-jump="validation">Run Validation</button>
              <button class="strong-btn">Open Analysis</button>
            </div>
          </div>
          <div class="mini-kpis">
            <article class="mini-kpi"><small>Ready</small><strong>${job.readiness}%</strong></article>
            <article class="mini-kpi"><small>Edge</small><strong>${job.edge}%</strong></article>
            <article class="mini-kpi"><small>Release</small><strong>${job.release}%</strong></article>
          </div>
          <div class="detail-section">${tabContent}</div>
          <div class="detail-section">
            <h3>Pipeline</h3>
            <div class="timeline-grid">
              <div class="timeline-node"><strong>Dataset</strong><small>${job.dataset.images} images</small></div>
              <div class="timeline-node"><strong>Train</strong><small>${job.status}</small></div>
              <div class="timeline-node"><strong>Eval</strong><small>quality bundle ready</small></div>
              <div class="timeline-node"><strong>Edge</strong><small>${job.edge}% usable</small></div>
              <div class="timeline-node"><strong>Release</strong><small>${job.release}% ready</small></div>
            </div>
          </div>
          <div class="detail-section">
            <h3>Gate Signals</h3>
            <table class="dense-table">
              <thead><tr><th>Signal</th><th>Value</th><th>State</th></tr></thead>
              <tbody>
                <tr><td>Training readiness</td><td>${job.readiness}%</td><td>${job.readiness > 80 ? "stable" : "watch"}</td></tr>
                <tr><td>Edge usability</td><td>${job.edge}%</td><td>${job.edge > 70 ? "ok" : "repair"}</td></tr>
                <tr><td>Release readiness</td><td>${job.release}%</td><td>${job.release > 70 ? "ready" : "hold"}</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderPoliciesMain() {
  const policy = policies.find((item) => item.id === state.policyId);
  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Policy Matrix</small>
          <strong>策略配置</strong>
        </div>
        <div class="panel-actions">
          <button class="chip active">Scene</button>
          <button class="chip">Threshold</button>
          <button class="chip">Release Gate</button>
        </div>
      </div>

      <div class="editor-layout">
        <div class="left-column">
          <div class="row-stack">
            ${policies.map((item) => `
              <button class="row-card ${state.policyId === item.id ? "active" : ""}" data-policy="${item.id}">
                <div class="row-head">
                  <div class="row-title">
                    <strong>${item.name}</strong>
                    <span class="muted">${item.scene}</span>
                  </div>
                  ${toneBadge(item.tone, item.mode)}
                </div>
              </button>
            `).join("")}
          </div>
        </div>

        <div class="detail-card">
          <div class="detail-top">
            <div>
              <small>Scene policy</small>
              <h2>${policy.name}</h2>
              <div class="detail-meta">${policy.scene}</div>
            </div>
            <div class="detail-actions">
              <button class="ghost-btn">AI Draft</button>
              <button class="ghost-btn">Simulate Hit</button>
              <button class="strong-btn">Save Policy</button>
            </div>
          </div>

          <div class="mini-kpis">
            <article class="mini-kpi"><small>绑定模型</small><strong>03</strong></article>
            <article class="mini-kpi"><small>活跃任务</small><strong>06</strong></article>
            <article class="mini-kpi"><small>触发次数</small><strong>42</strong></article>
          </div>

          <div class="grid-2" style="margin-top:14px">
            <div class="detail-section">
              <h3>Weights</h3>
              <div class="matrix">
                ${policy.weights.map(([label, value]) => `
                  <div class="matrix-row">
                    <span>${label}</span>
                    ${progressBar(value)}
                    <strong>${value}%</strong>
                  </div>
                `).join("")}
              </div>
            </div>

            <div class="detail-section">
              <h3>Triggers</h3>
              <ul>${policy.triggers.map((x) => `<li>${x}</li>`).join("")}</ul>
            </div>
          </div>

          <div class="detail-section">
            <h3>Actions</h3>
            <div class="row-meta">
              ${policy.actions.map((x) => `<span class="micro-tag">${x}</span>`).join("")}
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderValidationMain() {
  const validation = validations.find((item) => item.id === state.validationId);
  const tabs = [
    ["clusters", "Clusters"],
    ["thresholds", "Thresholds"],
    ["samples", "Samples"]
  ];

  let body = "";
  if (state.validationTab === "clusters") {
    body = `
      <div class="grid-3">
        ${validation.clusters.map(([label, count]) => `
          <article class="threshold-card">
            <strong>${label}</strong>
            <div class="value">${count}</div>
          </article>
        `).join("")}
      </div>
    `;
  } else if (state.validationTab === "thresholds") {
    body = `
      <div class="grid-3">
        <article class="detail-stat"><small>conf_thres</small><strong>${validation.thresholds.conf}</strong></article>
        <article class="detail-stat"><small>nms_thres</small><strong>${validation.thresholds.nms}</strong></article>
        <article class="detail-stat"><small>match_iou</small><strong>${validation.thresholds.match}</strong></article>
      </div>
    `;
  } else {
    body = `
      <div class="detail-section">
        <h3>Selected Samples</h3>
        <ul>${validation.samples.map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
    `;
  }

  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Validation Jobs</small>
          <strong>Edge Validation</strong>
        </div>
        <div class="nav-tabs">
          ${tabs.map(([id, label]) => `<button class="nav-tab ${state.validationTab === id ? "active" : ""}" data-validation-tab="${id}">${label}</button>`).join("")}
        </div>
      </div>

      <div class="validation-layout">
        <div class="left-column">
          <div class="row-stack">
            ${validations.map((item) => `
              <button class="row-card ${state.validationId === item.id ? "active" : ""}" data-validation="${item.id}">
                <div class="row-head">
                  <div class="row-title">
                    <strong>${item.name}</strong>
                    <span class="muted">score ${item.score}</span>
                  </div>
                  ${toneBadge(item.tone, item.verdict)}
                </div>
              </button>
            `).join("")}
          </div>
        </div>

        <div class="detail-card">
          <div class="detail-top">
            <div>
              <small>Validation Result</small>
              <h2>${validation.name}</h2>
              <div class="detail-meta">${validation.verdict}</div>
            </div>
            <div class="detail-actions">
              <button class="ghost-btn">AI Recommend</button>
              <button class="ghost-btn">Refine Threshold</button>
              <button class="strong-btn">Export Report</button>
            </div>
          </div>

          <div class="mini-kpis">
            <article class="mini-kpi"><small>Edge Score</small><strong>${validation.score}</strong></article>
            <article class="mini-kpi"><small>Conf</small><strong>${validation.thresholds.conf}</strong></article>
            <article class="mini-kpi"><small>NMS</small><strong>${validation.thresholds.nms}</strong></article>
          </div>

          <div class="detail-grid" style="margin-top:14px">
            <article class="detail-stat"><small>Edge Score</small><strong>${validation.score}</strong></article>
            <article class="detail-stat"><small>Conf</small><strong>${validation.thresholds.conf}</strong></article>
            <article class="detail-stat"><small>NMS</small><strong>${validation.thresholds.nms}</strong></article>
          </div>

          <div class="detail-section">
            <h3>Workspace</h3>
            ${body}
          </div>

          <div class="detail-section">
            <h3>Notes</h3>
            <div class="row-meta">${validation.notes.map((x) => `<span class="micro-tag">${x}</span>`).join("")}</div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderReleaseMain() {
  const release = releases.find((item) => item.id === state.releaseId);
  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Release Candidates</small>
          <strong>发布门禁</strong>
        </div>
        <div class="panel-actions">
          <button class="chip active">All</button>
          <button class="chip">Hold</button>
          <button class="chip">Ready</button>
        </div>
      </div>

      <div class="release-layout">
        <div class="left-column">
          <div class="row-stack">
            ${releases.map((item) => `
              <button class="row-card ${state.releaseId === item.id ? "active" : ""}" data-release="${item.id}">
                <div class="row-head">
                  <div class="row-title">
                    <strong>${item.name}</strong>
                    <span class="muted">${item.environment}</span>
                  </div>
                  ${toneBadge(item.tone, item.gate)}
                </div>
              </button>
            `).join("")}
          </div>
        </div>

        <div class="detail-card">
          <div class="detail-top">
            <div>
              <small>Release Candidate</small>
              <h2>${release.name}</h2>
              <div class="detail-meta">${release.environment} · ${release.version}</div>
            </div>
            <div class="detail-actions">
              <button class="ghost-btn">Compare Last Release</button>
              <button class="ghost-btn">Generate Summary</button>
              <button class="strong-btn" data-open-modal="publish">Publish</button>
            </div>
          </div>

          <div class="mini-kpis">
            <article class="mini-kpi"><small>Environment</small><strong>${release.environment}</strong></article>
            <article class="mini-kpi"><small>Gate</small><strong>${release.gate}</strong></article>
            <article class="mini-kpi"><small>Version</small><strong>${release.version.split("_").pop()}</strong></article>
          </div>

          <div class="detail-section">
            <h3>Gate Matrix</h3>
            <table class="mini-table">
              <tbody>
                ${release.checks.map(([label, ok]) => `
                  <tr>
                    <td>${label}</td>
                    <td>${ok ? '<span class="badge green">OK</span>' : '<span class="badge red">BLOCK</span>'}</td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>

          <div class="detail-section">
            <h3>Blockers</h3>
            <ul>${release.blockers.map((x) => `<li>${x}</li>`).join("")}</ul>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderLibraryMain() {
  const asset = library.find((item) => item.id === state.libraryId);
  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Asset Registry</small>
          <strong>模型资产库</strong>
        </div>
        <div class="panel-actions">
          <button class="chip active">All</button>
          <button class="chip">Release</button>
          <button class="chip">Imported</button>
        </div>
      </div>

      <div class="grid-3">
        ${library.map((item) => `
          <button class="library-card ${state.libraryId === item.id ? "active" : ""}" data-library="${item.id}">
            <div class="row-head">
              <div class="row-title">
                <strong>${item.name}</strong>
                <span class="muted">${item.source}</span>
              </div>
              ${toneBadge(item.tone, item.state)}
            </div>
            <div class="row-meta">
              ${item.tags.map((tag) => `<span class="micro-tag">${tag}</span>`).join("")}
            </div>
          </button>
        `).join("")}
      </div>

      <div class="detail-card" style="margin-top:14px">
        <div class="detail-top">
          <div>
            <small>Selected Asset</small>
            <h2>${asset.name}</h2>
            <div class="detail-meta">${asset.source}</div>
          </div>
          <div class="detail-actions">
            <button class="ghost-btn">Open Export</button>
            <button class="ghost-btn">Open Validation</button>
            <button class="strong-btn">Create Publish Package</button>
          </div>
        </div>
        <div class="mini-kpis">
          <article class="mini-kpi"><small>Assets</small><strong>${asset.assets.length}</strong></article>
          <article class="mini-kpi"><small>Source</small><strong>${asset.source}</strong></article>
          <article class="mini-kpi"><small>State</small><strong>${asset.state}</strong></article>
        </div>
        <div class="detail-section">
          <h3>Assets</h3>
          <div class="row-meta">${asset.assets.map((x) => `<span class="micro-tag">${x}</span>`).join("")}</div>
        </div>
      </div>
    </div>
  `;
}

function renderSide() {
  const insights = sideInsights[state.page];
  const releaseReady = releases.filter((x) => x.gate === "READY").length;
  return `
    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Inspector</small>
          <strong>状态面板</strong>
        </div>
      </div>
      <div class="insight-grid">
        ${insights.map(([label, value]) => `
          <article class="insight-stat">
            <small>${label}</small>
            <strong>${value}</strong>
          </article>
        `).join("")}
      </div>
    </div>

    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Priority Queue</small>
          <strong>高优先动作</strong>
        </div>
      </div>
      <div class="priority-list">
        <article class="priority-item"><strong>Night glare 修复链</strong>${toneBadge("amber", "P0")}</article>
        <article class="priority-item"><strong>发布摘要生成</strong>${toneBadge("red", "P0")}</article>
        <article class="priority-item"><strong>模型对比视图</strong>${toneBadge("green", "P1")}</article>
      </div>
    </div>

    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Release Watch</small>
          <strong>放行概况</strong>
        </div>
      </div>
      <div class="side-note">
        <p>Ready candidates</p>
        <strong style="font-size:28px;color:var(--text-strong);font-family:Manrope,sans-serif">${releaseReady}</strong>
        ${progressBar(67, "green")}
      </div>
    </div>

    <div class="surface-block">
      <div class="panel-head">
        <div class="panel-title">
          <small>Activity Log</small>
          <strong>最近动作</strong>
        </div>
      </div>
      <div class="log-list">
        <div class="log-row"><span>534 second-stage refine requested</span><span>2m</span></div>
        <div class="log-row"><span>528 publish manifest regenerated</span><span>14m</span></div>
        <div class="log-row"><span>policy-night hit on 3 active jobs</span><span>31m</span></div>
        <div class="log-row"><span>512 blocked by export missing</span><span>42m</span></div>
      </div>
    </div>
  `;
}

function renderMain() {
  if (state.page === "overview") return renderOverviewMain();
  if (state.page === "training") return renderTrainingMain();
  if (state.page === "policies") return renderPoliciesMain();
  if (state.page === "validation") return renderValidationMain();
  if (state.page === "release") return renderReleaseMain();
  return renderLibraryMain();
}

function renderModal() {
  if (!state.modal) return "";
  if (state.modal === "publish") {
    return `
      <div class="modal-head">
        <div>
          <h2>Publish Job</h2>
          <p class="muted">AICM publish package</p>
        </div>
        <button class="chip" data-close-modal>Close</button>
      </div>
      <div class="modal-grid">
        <div class="field"><span>Model</span><strong>garbageStreet / 534</strong></div>
        <div class="field"><span>Environment</span><strong>prod-city-east</strong></div>
        <div class="field"><span>Current Gate</span><strong>HOLD</strong></div>
        <div class="field"><span>Required Action</span><strong>Complete validation summary</strong></div>
      </div>
      <div class="modal-actions" style="margin-top:16px">
        <button class="top-action quiet" data-close-modal>Cancel</button>
        <button class="top-action strong">Force Draft Package</button>
      </div>
    `;
  }
  if (state.modal === "filter") {
    return `
      <div class="modal-head">
        <div>
          <h2>Global Filter</h2>
          <p class="muted">Workspace scope</p>
        </div>
        <button class="chip" data-close-modal>Close</button>
      </div>
      <div class="modal-grid">
        <div class="field"><span>Rect Preset</span><strong>garbageStreet</strong></div>
        <div class="field"><span>Environment</span><strong>all</strong></div>
        <div class="field"><span>Stage</span><strong>training + validation + release</strong></div>
        <div class="field"><span>Owner</span><strong>all operators</strong></div>
      </div>
      <div class="modal-actions" style="margin-top:16px">
        <button class="top-action quiet" data-close-modal>Reset</button>
        <button class="top-action strong" data-close-modal>Apply</button>
      </div>
    `;
  }
  return `
    <div class="modal-head">
      <div>
        <h2>Quick Run</h2>
        <p class="muted">Create job</p>
      </div>
      <button class="chip" data-close-modal>Close</button>
    </div>
    <div class="modal-grid">
      <div class="field"><span>Training Task</span><strong>garbageStreet / label-dimension</strong></div>
      <div class="field"><span>Base Model</span><strong>best.pt / 528 baseline</strong></div>
      <div class="field"><span>Edge Validation Mode</span><strong>run_and_eval</strong></div>
      <div class="field"><span>Publish Target</span><strong>staging first</strong></div>
    </div>
    <div class="modal-actions" style="margin-top:16px">
      <button class="top-action quiet" data-close-modal>Cancel</button>
      <button class="top-action strong" data-close-modal>Create</button>
    </div>
  `;
}

function syncTitles() {
  const meta = pageMeta[state.page];
  document.getElementById("page-kicker").textContent = meta.kicker;
  document.getElementById("page-title").textContent = meta.title;
}

function render() {
  syncTitles();
  document.querySelectorAll(".nav-link").forEach((el) => {
    el.classList.toggle("active", el.dataset.page === state.page);
  });
  document.getElementById("command-strip").innerHTML = renderCommandStrip();
  document.getElementById("surface-main").innerHTML = renderMain();
  document.getElementById("surface-side").innerHTML = renderSide();
  const modalLayer = document.getElementById("modal-layer");
  document.getElementById("modal-card").innerHTML = renderModal();
  modalLayer.classList.toggle("hidden", !state.modal);
}

document.addEventListener("click", (event) => {
  const pageEl = event.target.closest("[data-page]");
  if (pageEl) {
    state.page = pageEl.dataset.page;
    render();
    return;
  }

  const jobEl = event.target.closest("[data-training]");
  if (jobEl) {
    state.trainingId = jobEl.dataset.training;
    render();
    return;
  }

  const policyEl = event.target.closest("[data-policy]");
  if (policyEl) {
    state.policyId = policyEl.dataset.policy;
    render();
    return;
  }

  const validationEl = event.target.closest("[data-validation]");
  if (validationEl) {
    state.validationId = validationEl.dataset.validation;
    render();
    return;
  }

  const releaseEl = event.target.closest("[data-release]");
  if (releaseEl) {
    state.releaseId = releaseEl.dataset.release;
    render();
    return;
  }

  const libraryEl = event.target.closest("[data-library]");
  if (libraryEl) {
    state.libraryId = libraryEl.dataset.library;
    render();
    return;
  }

  const pageJump = event.target.closest("[data-page-jump]");
  if (pageJump) {
    state.page = pageJump.dataset.pageJump;
    if (pageJump.dataset.training) state.trainingId = pageJump.dataset.training;
    render();
    return;
  }

  const tabEl = event.target.closest("[data-training-tab]");
  if (tabEl) {
    state.trainingTab = tabEl.dataset.trainingTab;
    render();
    return;
  }

  const vTabEl = event.target.closest("[data-validation-tab]");
  if (vTabEl) {
    state.validationTab = vTabEl.dataset.validationTab;
    render();
    return;
  }

  const modalOpen = event.target.closest("[data-open-modal]");
  if (modalOpen) {
    state.modal = modalOpen.dataset.openModal;
    render();
    return;
  }

  if (event.target.closest("[data-close-modal]") || event.target.id === "modal-layer") {
    state.modal = null;
    render();
  }
});

document.getElementById("primary-cta").addEventListener("click", () => {
  state.modal = "command";
  render();
});

render();
