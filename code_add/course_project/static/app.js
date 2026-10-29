const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  selfopt: null,
  selectedScene: "",
  currentFile: "",
  currentRevision: null,
  savedContent: "",
  editorDirty: false,
  currentView: "overview",
  lastAi: null,
  lastIteration: null,
  lastBatch: null,
  lastExperiment: null,
  pollers: {},
  activeJobs: {},
  aiConfigVersion: 0,
  aiConnection: null,
  statistics: null,
};
const navItems = [
  ["overview", "总览", "M4 13h6V5H4z M14 19h6V5h-6z M4 19h6v-4H4z"],
  ["assets", "场景与代码", "M4 7h16 M4 12h16 M4 17h10"],
  [
    "ai",
    "Prompt 与 AI",
    "M12 3l2.3 4.7 5.2.8-3.8 3.7.9 5.3L12 16.8 7.1 19l.9-5.3L4.2 10l5.2-.8z",
  ],
  ["validate", "验证沙箱", "M4 19h16 M7 16V8 M12 16V5 M17 16v-4"],
  [
    "iterate",
    "自动迭代",
    "M17 1l4 4-4 4 M3 11V9a4 4 0 014-4h14 M7 23l-4-4 4-4 M21 13v2a4 4 0 01-4 4H3",
  ],
  ["outputs", "实验产物", "M4 6h16 M4 12h16 M4 18h16"],
  ["experiments", "实验分析", "M4 19h16 M8 17V9 M12 17V5 M16 17v-7"],
  ["statistics", "统计基准", "M4 18L9 12l4 3 7-9 M4 20h16"],
  [
    "audit",
    "审计",
    "M4 6c0-2 16-2 16 0v12c0 2-16 2-16 0z M4 6v6c0 2 16 2 16 0 M4 12v6",
  ],
];
const pageCopy = {
  overview: ["平台总览", "查看场景资产、协议演化与实验进展。"],
  assets: ["场景与代码", "管理实验场景，在同一工作区查看和编辑协议资产。"],
  ai: ["协议设计", "选择任务画像与父代协议，探索下一代协议的可能。"],
  validate: ["验证沙箱", "用功能测试、物理约束与信息隔离检查评估协议。"],
  iterate: ["自动迭代", "组合父代协议，连续生成、验证并积累新的实验结果。"],
  outputs: ["实验产物", "集中查看场景输出、实验数据与运行报告。"],
  experiments: ["实验分析", "比较协议表现，生成鲁棒性、演化与性能分析图表。"],
  statistics: ["统计基准", "查看刘滕飞负责的统计协议、通信成本、主实验、消融与 Pareto 证据。"],
  audit: ["运行审计", "追踪任务历史、模型配置与实验记录。"],
};
const manuals = {
  overview: [
    "总览页说明平台闭环：需求画像 -> 场景/GRDL -> 父代协议基因 -> 大模型进化 -> 在线验证 -> 产物沉淀。",
    "场景是数据生成、Task.py、generation 协议和 Prompt 的资产目录；任务画像是统计目标和验证口径。",
    "按钮说明：本页说明打开当前页面手册；按钮说明会再次打开总览手册。",
  ],
  assets: [
    "场景与代码页用于管理源代码和协议资产。generation 是演化世代，base_protocol 是可复用父代基线。",
    "按钮说明：创建新场景会生成新目录；复制基线协议会把父代放进指定世代；刷新资产会重新扫描磁盘；重新读取会丢弃编辑器未保存内容并读取文件；保存当前文件会写回右侧编辑器内容。",
    "文件资产表按任务配置、generation、prompts 和 base_protocol 分组，点击任一文件即可在右侧查看或编辑。",
  ],
  ai: [
    "Prompt 与 AI 页用于单次协议生成。这里只处理父代 A/B 这一组交叉，不再放父代池。",
    "填写中转站 URL、API Key 和模型名即可使用，接口自动识别。修改配置后下次请求立即生效，无需重启；自动迭代使用同一套配置。密钥仅在当前页面使用，刷新后重新填写。",
    "按钮说明：检查模型是否可用会发起最小 OK 探测；列出可用模型会读取 /v1/models；预览生成任务只生成 Prompt 和目标路径，不生成协议文件；生成协议代码会后台调用模型并实时显示进度。",
    "任务画像会进入 Prompt、默认文件名和验证口径。例如均值画像默认生成 web_mean_estimation_candidate.py。",
  ],
  validate: [
    "验证沙箱用于单协议验证，支持多任务画像的功能烟测、物理约束和信息隔离静态扫描。",
    "按钮说明：验证协议会运行当前协议并输出 JSON 细节；相对误差容忍用于均值、方差、分位数、基数等单值任务；价格/数值上下限用于构造模拟标签属性。",
  ],
  iterate: [
    "自动迭代是核心流程：父代池组合、生成 Prompt、模型生成、验证、沉淀新世代。父代池只在本页出现，用于多轮组合。",
    "按钮说明：填入推荐父代池会选择可用的高质量父代；只预览迭代任务不会调用模型，也不会生成协议文件；启动自动迭代会后台执行全部轮次并每秒刷新步骤卡片、日志、轮次表和结果文件。",
    "每轮 Prompt 和真实生成的协议都在本页预览，不会跳转到 Prompt 与 AI 页。",
  ],
  outputs: [
    "Outputs 页直接显示 course_project/outputs 下的全部运行产物，包括 dashboard、CSV、JSON、Markdown、日志、PDF 和图片。",
    "按钮说明：预览会在右侧打开产物；HTML/PDF/图片用内嵌窗口，文本类文件直接显示内容。",
  ],
  experiments: [
    "Experiments 页用于运行原项目实验脚本并生成课程报告图表。这里的 Task.py 是场景数据生成器，任务画像是统计目标和验证口径，二者不是同一个概念。",
    "按钮说明：运行鲁棒性实验生成 CSV；生成鲁棒性图、进化图和终稿图会调用绘图脚本；按钮按下后会先写入控制台并禁用按钮，任务完成后恢复。",
    "实验产物表中的“本页预览”会在右侧直接打开图片、PDF、CSV 或 JSON，不再跳转到 Outputs 页。协议选择采用“来源场景 + 协议”两级下拉，页面会自动拼成脚本需要的 name:source 格式。",
  ],
  statistics: [
    "本页展示共享统计草图、均值/方差/基数/Top-k、通信成本、基线协议、主实验、消融实验和 Pareto 图。",
    "通信 bits 是 reader bits + tag bits；slots、frames、rounds 同时保留，CPU 时间只作为参考。每个实验行携带 distribution、num_tags 和 seed，筛选后可核对公平对比。",
    "图表和原始 JSON/CSV/PDF/PNG 均来自 Experiments/stat_protocols；失败行与 llm_random 负结果不会从页面隐藏。",
  ],
  audit: [
    "审计页用于确认模型配置、AI 生成任务、运行计数和历史记录。",
    "按钮说明：本页主要查看状态，不会修改协议资产；重点关注 Base URL、模型、API Key 是否已配置，以及最近真实任务是否成功。",
  ],
};
function icon(path) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="${path}"/></svg>`;
}
function setStatus(t, m = "ready") {
  $("statusText").textContent = t;
  $("statusDot").className =
    "dot" + (m === "busy" ? " busy" : m === "error" ? " error" : "");
}
async function api(path, opt = {}) {
  try {
    const r = await fetch(path, {
      ...opt,
      headers: { "Content-Type": "application/json", ...opt.headers },
    });
    const d = await r.json().catch(() => {
      throw new Error(`服务返回了无法解析的响应（${r.status}）`);
    });
    if (!r.ok) {
      const error = new Error(d.error || `请求失败（${r.status}）`);
      error.status = r.status;
      throw error;
    }
    return d;
  } catch (error) {
    if (error instanceof TypeError)
      throw new Error("无法连接本地服务，请检查服务是否运行后重试。");
    throw error;
  }
}
function mountNav() {
  $("nav").innerHTML = navItems
    .map(
      ([id, l, p], i) =>
        `<button type="button" class="${i === 0 ? "active" : ""}" data-view="${id}" ${i === 0 ? 'aria-current="page"' : ""}>${icon(p)}<span>${l}</span></button>`,
    )
    .join("");
  document
    .querySelectorAll("nav button")
    .forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
}
function switchView(id) {
  if (!pageCopy[id]) id = "overview";
  state.currentView = id;
  if (id === "statistics" && !state.statistics) loadStatistics();
  const [title, description] = pageCopy[id];
  $("pageTitle").textContent = title;
  $("pageCrumb").textContent = title;
  $("pageDescription").textContent = description;
  document.title = `${title} · RFID Lab`;
  history.replaceState(null, "", `#${id}`);
  document.querySelectorAll("nav button").forEach((x) => {
    const active = x.dataset.view === id;
    x.classList.toggle("active", active);
    if (active) x.setAttribute("aria-current", "page");
    else x.removeAttribute("aria-current");
  });
  document
    .querySelectorAll(".view")
    .forEach((x) => x.classList.toggle("active", x.id === "view-" + id));
}
function sceneByName(n) {
  return (
    (state.selfopt?.scenes || []).find((s) => s.name === n) ||
    state.selfopt?.scenes?.[0] ||
    null
  );
}
function currentScene() {
  return sceneByName(state.selectedScene || state.selfopt?.default_scene);
}
function fillSelect(el, vals, sel) {
  el.innerHTML = vals
    .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
    .join("");
  if (sel && vals.includes(sel)) el.value = sel;
}
function bytes(v) {
  const n = Number(v || 0);
  return n > 1048576
    ? (n / 1048576).toFixed(1) + " MB"
    : n > 1024
      ? (n / 1024).toFixed(1) + " KB"
      : n + " B";
}
function formatDurationMs(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n >= 1000
    ? `${Number((n / 1000).toFixed(2))} s`
    : `${Number(n.toFixed(2))} ms`;
}
function tone(i) {
  return [
    "tone-teal",
    "tone-green",
    "tone-amber",
    "tone-violet",
    "tone-red",
    "",
  ][i % 6];
}
function rel(path) {
  if (!path) return "";
  let p = String(path).replaceAll("\\", "/");
  let root = String(state.selfopt?.project_root || "").replaceAll("\\", "/");
  if (root && p.startsWith(root + "/")) p = p.slice(root.length + 1);
  return p;
}
function splitNames(v) {
  return String(v || "")
    .split(/[\s,，;；]+/)
    .map((x) => x.trim())
    .filter(Boolean);
}
function taskProfileOptions() {
  return Object.entries(state.selfopt?.task_profiles || {}).map(([k, v]) => ({
    key: k,
    title: v.title || k,
    objective: v.objective || "",
    output: v.output || "",
  }));
}
function taskInfo(key) {
  return (
    state.selfopt?.task_profiles?.[key] || {
      title: key,
      objective: "",
      output: "",
    }
  );
}
function customTaskTextFor(prefix) {
  const id = prefix === "ai" ? "customTaskText" : `${prefix}CustomTaskText`;
  return $(id)?.value?.trim() || "";
}
function renderCustomTaskFields() {
  [["taskProfile", "customTaskField"], ["validateTaskProfile", "validateCustomTaskField"], ["iterateTaskProfile", "iterateCustomTaskField"], ["expTaskProfile", "expCustomTaskField"]].forEach(([selectId, fieldId]) => {
    const field = $(fieldId);
    if (field) field.style.display = $(selectId)?.value === "custom_task" ? "block" : "none";
  });
}
function taskSlug(id) {
  return String(id || "mode_query").replace(/[^A-Za-z0-9_]/g, "_");
}
function updateNamesForTask(force = false) {
  const aiSlug = taskSlug($("taskProfile")?.value);
  const itSlug = taskSlug($("iterateTaskProfile")?.value);
  const aiName = $("aiProtocolName");
  if (
    aiName &&
    (force ||
      !aiName.value ||
      /^web_.*_candidate$/.test(aiName.value) ||
      aiName.value === "web_ai_candidate")
  )
    aiName.value = `web_${aiSlug}_candidate`;
  const itName = $("iteratePrefix");
  if (
    itName &&
    (force ||
      !itName.value ||
      /^web_.*_iter$/.test(itName.value) ||
      itName.value === "web_auto_iter")
  )
    itName.value = `web_${itSlug}_iter`;
}
function renderExperimentTaskExplain() {
  const key = $("expTaskProfile")?.value || "mode_query";
  const info = taskInfo(key);
  if ($("expTaskExplain"))
    $("expTaskExplain").innerHTML =
      `<b>当前任务画像：</b>${esc(info.title)}<br><b>画像目标：</b>${esc(info.objective)}<br>Task.py 只负责生成标签样本和真实分布；验证沙箱会按该画像把同一批标签解释成众数、均值、方差、分位数、基数或缺失盘点任务。鲁棒性实验主要测协议在参数扰动下的耗时，画像会进入实验记录与报告说明。`;
}
function renderTaskProfileInfo() {
  const ai = taskInfo($("taskProfile")?.value || "mode_query");
  if ($("taskProfileInfo"))
    $("taskProfileInfo").innerHTML =
      `<b>任务画像：</b>${esc(ai.title)}<br><b>优化目标：</b>${esc(ai.objective)}<br><b>协议输出：</b>${esc(ai.output)}`;
  const it = taskInfo($("iterateTaskProfile")?.value || "mode_query");
  if ($("iterateTaskProfileInfo"))
    $("iterateTaskProfileInfo").innerHTML =
      `<b>任务画像：</b>${esc(it.title)}<br><b>自动迭代会使用该画像生成 Prompt、命名协议并执行验证。</b><br>${esc(it.objective)}`;
  renderExperimentTaskExplain();
  renderCustomTaskFields();
}
function fillTaskProfiles() {
  const opts = taskProfileOptions();
  [
    "taskProfile",
    "validateTaskProfile",
    "iterateTaskProfile",
    "expTaskProfile",
  ].forEach((id) => {
    const el = $(id);
    if (el) {
      const old = el.value;
      el.innerHTML = opts
        .map((o) => `<option value="${esc(o.key)}">${esc(o.title)}</option>`)
        .join("");
      if (opts.some((o) => o.key === old)) el.value = old;
    }
  });
  updateNamesForTask(false);
  renderTaskProfileInfo();
}
function llmPayload() {
  return {
    base_url: $("aiBaseUrl").value.trim(),
    model: $("aiModel").value.trim(),
    api_key_value: $("aiApiKey").value.trim() || null,
    temperature: Number($("aiTemperature").value || 0.2),
    max_tokens: Number($("aiMaxTokens").value || 3600),
    timeout_seconds: Number($("aiTimeout").value || 180),
    validate_num_tags: Number($("aiValidateTags").value || 120),
    retry_attempts: 3,
  };
}
function fillSceneSelects() {
  const names = (state.selfopt?.scenes || []).map((x) => x.name);
  const sel =
    state.selectedScene || state.selfopt?.default_scene || names[0] || "";
  ["assetScene", "aiScene", "validateScene", "iterateScene"].forEach((id) =>
    fillSelect($(id), names, sel),
  );
}
function protocolNames() {
  return Array.from(
    new Set((currentScene()?.protocol_files || []).map((x) => x.name)),
  );
}
function fillProtocolControls() {
  const names = protocolNames();
  const base = (state.selfopt?.base_protocols || []).map((x) => x.name);
  if ($("promptParents"))
    $("promptParents").innerHTML = names
      .map((v) => `<option value="${esc(v)}">${esc(v)}</option>`)
      .join("");
  fillSelect($("aiParentA"), names, $("aiParentA").value || names[0]);
  fillSelect(
    $("aiParentB"),
    names,
    $("aiParentB").value || names[1] || names[0],
  );
  fillSelect(
    $("validateProtocol"),
    names,
    $("validateProtocol").value || names[0],
  );
  fillSelect(
    $("baselineProtocol"),
    base,
    $("baselineProtocol").value || base[0],
  );
  renderParentPoolBars();
}
function selectedValues(id) {
  return Array.from($(id)?.selectedOptions || [])
    .map((o) => o.value)
    .filter(Boolean);
}
function fillMultiSelect(id, items, defaults = []) {
  const el = $(id);
  if (!el) return;
  const old = selectedValues(id);
  const selected = old.length ? old : defaults;
  el.innerHTML = items
    .map((item) => {
      const value = Array.isArray(item) ? item[0] : item;
      const label = Array.isArray(item) ? item[1] : item;
      return `<option value="${esc(value)}" ${selected.includes(value) ? "selected" : ""}>${esc(label)}</option>`;
    })
    .join("");
}
function experimentProtocolRows(source) {
  if (source === "base_protocol")
    return (state.selfopt?.base_protocols || []).map((x) => ({
      ...x,
      generation: "base_protocol",
    }));
  const scene = sceneByName(source);
  const seen = new Set();
  return (scene?.protocol_files || [])
    .filter((x) => {
      if (seen.has(x.name)) return false;
      seen.add(x.name);
      return true;
    })
    .sort(
      (a, b) =>
        genIndex(a.generation) - genIndex(b.generation) ||
        a.name.localeCompare(b.name),
    );
}
function updateExperimentProtocolSpec() {
  const source = $("expScene")?.value || state.selectedScene;
  const protocol = $("expProtocol")?.value || "";
  if ($("expProtocolSpec"))
    $("expProtocolSpec").value =
      protocol && source ? `${protocol}:${source}` : "";
  renderExperimentTaskExplain();
}
function fillExperimentControls() {
  const opts = state.selfopt?.experiment_options || {};
  const sources = [
    ...(state.selfopt?.scenes || []).map((x) => x.name),
    "base_protocol",
  ];
  const sceneEl = $("expScene");
  const oldSource = sceneEl?.value;
  fillSelect(
    sceneEl,
    sources,
    oldSource && sources.includes(oldSource)
      ? oldSource
      : state.selectedScene || state.selfopt?.default_scene || sources[0] || "",
  );
  const rows = experimentProtocolRows(sceneEl.value);
  const protocolEl = $("expProtocol");
  const oldProtocol = protocolEl?.value;
  protocolEl.innerHTML = rows.length
    ? rows
        .map(
          (r) =>
            `<option value="${esc(r.name)}">${esc(r.name)} · ${esc(r.generation || "")}</option>`,
        )
        .join("")
    : `<option value="">无可用协议</option>`;
  if (oldProtocol && rows.some((r) => r.name === oldProtocol))
    protocolEl.value = oldProtocol;
  fillMultiSelect(
    "expFactors",
    opts.robustness_factors || [
      ["density", "密度/取值范围"],
      ["dominance", "众数支配度"],
      ["scale", "标签规模"],
    ],
    ["density"],
  );
  fillSelect(
    $("evoScenario"),
    opts.evolution_scenarios || ["single_peak", "multi_peak"],
    $("evoScenario")?.value || "single_peak",
  );
  fillMultiSelect(
    "evoCharts",
    opts.evolution_charts || [
      ["correction", "LLM 纠正次数"],
      ["performance", "性能-准确率"],
      ["time", "耗时收敛"],
      ["accuracy", "准确率收敛"],
      ["all", "全部图表"],
    ],
    ["correction"],
  );
  updateExperimentProtocolSpec();
}
function renderAiRuntime() {
  const jobs = state.selfopt?.ai_jobs || [];
  const latestReal = jobs.find((j) => !Number(j.dry_run));
  const config = llmPayload();
  const ready = Boolean(config.base_url && config.model && config.api_key_value);
  $("aiRuntimeSummary").textContent = state.aiConnection?.message || (ready
    ? "配置已填写，将随下次请求立即生效。可点击“检查模型是否可用”验证连接。"
    : "请填写 URL、API Key 和模型名，接口将自动识别，无需重启服务。");
  $("aiRuntimeMetrics").innerHTML = [
    [
      "API URL",
      config.base_url ? "已填写" : "未填写",
      config.base_url ? "tone-green" : "tone-amber",
    ],
    [
      "模型",
      config.model || "未填写",
      config.model ? "tone-green" : "tone-amber",
    ],
    [
      "API Key",
      config.api_key_value ? "已填写" : "未填写",
      config.api_key_value ? "tone-green" : "tone-amber",
    ],
    [
      "最近真实任务",
      latestReal ? `#${latestReal.id} ${latestReal.status}` : "暂无",
      latestReal?.status === "success" ? "tone-green" : "tone-amber",
    ],
  ]
    .map(
      ([k, v, t]) =>
        `<div class="metric ${t}"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`,
    )
    .join("");
}
function renderOverview() {
  const scenes = state.selfopt?.scenes || [];
  const pc = scenes.reduce((s, x) => s + Number(x.protocol_count || 0), 0);
  const pr = scenes.reduce((s, x) => s + Number(x.prompt_count || 0), 0);
  const ai = state.selfopt?.database_counts?.ai_jobs || 0;
  const out = (state.selfopt?.output_artifacts || []).length;
  $("overviewMetrics").innerHTML = [
    ["场景数", scenes.length, "tone-teal"],
    ["协议文件", pc, "tone-green"],
    ["Prompt 文件", pr, "tone-amber"],
    ["Outputs", out, "tone-violet"],
    ["AI 任务", ai, "tone-blue"],
  ]
    .map(
      ([k, v, t]) =>
        `<div class="metric ${t}"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`,
    )
    .join("");
  $("defaultScenePill").textContent = currentScene()?.name || "-";
  const loop = [
    ["1 需求画像", "自然语言 + 多维统计任务"],
    ["2 场景/GRDL", "Task.py 与场景参数"],
    ["3 父代基因", "generation + base_protocol"],
    ["4 大模型进化", "交叉、变异、自修复"],
    ["5 在线沙箱", "逻辑/物理/信息隔离验证"],
    ["6 产物沉淀", "outputs、图表、审计、迭代"],
  ];
  $("closedLoop").innerHTML = loop
    .map(
      (x, i) =>
        `<div class="stage" data-step="${i + 1}"><b>${esc(x[0])}</b><div class="muted">${esc(x[1])}</div></div>`,
    )
    .join("");
  renderSceneTable();
  renderGenerationBars();
}
function renderSceneTable() {
  const rows = state.selfopt?.scenes || [];
  $("sceneRows").innerHTML = rows.length
    ? rows
        .map(
          (s) =>
            `<tr><td><b>${esc(s.name)}</b><div class="muted mono">${esc(s.path)}</div></td><td>${esc(s.generation_count)}</td><td>${esc(s.protocol_count)}</td><td>${esc(s.prompt_count)}</td><td>${s.task_path ? '<span class="pill green">Task.py</span>' : '<span class="pill red">缺失</span>'}</td></tr>`,
        )
        .join("")
    : empty(5);
}
function renderGenerationBars() {
  const rows = currentScene()?.generations || [];
  const max = Math.max(1, ...rows.map((x) => Number(x.protocol_count || 0)));
  $("generationBars").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="bar-row"><span class="mono">${esc(x.name)}</span><div class="bar"><span style="width:${Math.max(5, (Number(x.protocol_count || 0) / max) * 100)}%"></span></div><span>${esc(x.protocol_count)}</span></div>`,
        )
        .join("")
    : `<div class="muted">暂无世代数据</div>`;
}
function genIndex(name) {
  const m = String(name || "").match(/^generation_?(\d+)$/);
  return m ? Number(m[1]) : 99999;
}
function renderAssets() {
  const s = currentScene();
  if (!s) return;
  const rows = [];
  const addGroup = (t) => rows.push({ group: t });
  if (s.task_path || s.config_path) {
    addGroup("场景任务与配置");
    if (s.task_path)
      rows.push({
        name: "Task.py",
        generation: "任务",
        path: s.task_path,
        size: 0,
      });
    if (s.config_path)
      rows.push({
        name: "config.py",
        generation: "配置",
        path: s.config_path,
        size: 0,
      });
  }
  const gens = Array.from(
    new Set((s.protocol_files || []).map((x) => x.generation)),
  ).sort((a, b) => genIndex(a) - genIndex(b));
  gens.forEach((g) => {
    addGroup(g);
    (s.protocol_files || [])
      .filter((x) => x.generation === g)
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach((x) => rows.push(x));
  });
  addGroup("prompts");
  (s.prompts || []).forEach((x) => rows.push({ ...x, generation: "prompts" }));
  addGroup("base_protocol");
  (state.selfopt?.base_protocols || []).forEach((x) =>
    rows.push({ ...x, generation: "base_protocol" }),
  );
  const query = $("assetSearch").value.trim().toLowerCase();
  const filtered = rows.filter(
    (f) =>
      !f.group &&
      (!query ||
        `${f.name} ${f.path} ${f.generation}`.toLowerCase().includes(query)),
  );
  $("fileCountPill").textContent = filtered.length;
  $("fileRows").innerHTML =
    pageRows("fileRows", filtered, renderAssets, s.name + query)
      .map((f) =>
        f.group
          ? `<tr class="group-row"><td colspan="3">${esc(f.group)}</td></tr>`
          : `<tr class="file-row ${state.currentFile === f.path ? "active" : ""}" data-open="${esc(f.path)}"><td><b title="${esc(f.name)}">${esc(f.name)}</b></td><td><span class="pill">${esc(f.generation || "-")}</span><div class="muted mono" title="${esc(f.path)}">${esc(f.path)}</div></td><td>${f.size ? esc(bytes(f.size)) : "-"}</td></tr>`,
      )
      .join("") || empty(3, "没有匹配的文件，试试其他关键词。");
  document
    .querySelectorAll("[data-open]")
    .forEach((el) => (el.onclick = () => loadFile(el.dataset.open)));
}
async function loadFile(path) {
  if (!path) return;
  if (
    state.editorDirty &&
    !confirm("当前文件有未保存的修改，确定重新读取或切换文件？")
  )
    return;
  setStatus("读取文件", "busy");
  try {
    const d = await api(`/api/selfopt/file?path=${encodeURIComponent(path)}`);
    state.currentFile = d.path;
    state.currentRevision = d.revision;
    state.editorDirty = false;
    $("currentFilePath").textContent = d.path;
    $("fileEditor").value = d.content;
    // Textareas normalize Windows CRLF endings; compare the visible buffer.
    state.savedContent = $("fileEditor").value;
    updateEditorState();
    renderAssets();
    refreshFileReader();
    setStatus("文件已加载");
  } catch (e) {
    if (e.message.includes("文件过大")) {
      await previewText(path, "大文件只读预览", "modal");
      return;
    }
    setStatus(e.message, "error");
    alert(e.message);
  }
}
async function saveCurrentFile() {
  if (!state.currentFile) return;
  const path = state.currentFile;
  const content = $("fileEditor").value;
  $("saveFile").disabled = true;
  setStatus("保存文件", "busy");
  try {
    const d = await api("/api/selfopt/file", {
      method: "POST",
      body: JSON.stringify({
        path,
        content,
        revision: state.currentRevision,
      }),
    });
    if (state.currentFile === path) {
      state.currentRevision = d.revision;
      state.savedContent = content;
      updateEditorState();
    }
    setStatus(`已保存 ${d.path}`);
  } catch (e) {
    setStatus(e.message, "error");
    alert(e.message);
  } finally {
    $("saveFile").disabled = false;
  }
}
function updateEditorState() {
  state.editorDirty = $("fileEditor").value !== state.savedContent;
  $("editorState").textContent = !state.currentFile
    ? "尚未选择文件"
    : state.editorDirty
      ? "● 有未保存的修改"
      : "已与磁盘同步";
  $("saveFile").disabled = !state.currentFile;
  $("reloadFile").disabled = !state.currentFile;
}
async function previewText(
  path,
  title = "预览",
  target = null,
  missingHint = "",
) {
  const p = rel(path);
  const place =
    target ||
    (state.currentView === "iterate"
      ? "iterate"
      : state.currentView === "ai"
        ? "ai"
        : "modal");
  const slots = {
    ai: ["aiPreviewTitle", "aiPreview"],
    iterate: ["iterationPreviewTitle", "iterationPreview"],
    modal: ["filePreviewTitle", "filePreviewBody"],
  }[place] || ["filePreviewTitle", "filePreviewBody"];
  if (missingHint) {
    $(slots[0]).textContent = title;
    $(slots[1]).innerHTML = emptyPreview("协议文件尚未生成", missingHint);
    return;
  }
  try {
    $(slots[0]).textContent = title;
    if (place === "modal") $("filePreviewModal").classList.add("show");
    const loaded = await renderAssetPreview(p, slots[1]);
    $(slots[1]).scrollIntoView({ behavior: "smooth", block: "nearest" });
    setStatus(
      loaded ? `已预览 ${p}` : "预览失败，请查看文件提示",
      loaded ? "ready" : "error",
    );
  } catch (e) {
    $(slots[0]).textContent = title;
    $(slots[1]).textContent = missingHint || e.message;
    if (place === "modal") $("filePreviewModal").classList.add("show");
    $(slots[1]).scrollIntoView({ behavior: "smooth", block: "nearest" });
    setStatus(missingHint || e.message, "error");
  }
}
async function createScene() {
  const name = $("newSceneName").value.trim();
  if (!name) return;
  setStatus("创建场景", "busy");
  try {
    const d = await api("/api/selfopt/scene", {
      method: "POST",
      body: JSON.stringify({ scene: name }),
    });
    state.selfopt = d.state;
    state.selectedScene = d.scene;
    $("newSceneName").value = "";
    renderAll();
    setStatus(`已创建 ${d.scene}`);
  } catch (e) {
    setStatus(e.message, "error");
    alert(e.message);
  }
}
async function copyBaseline() {
  setStatus("复制基线", "busy");
  try {
    const d = await api("/api/selfopt/baseline-copy", {
      method: "POST",
      body: JSON.stringify({
        scene: state.selectedScene,
        protocol: $("baselineProtocol").value,
        target_generation: $("baselineTargetGeneration").value,
      }),
    });
    state.selfopt = d.state;
    renderAll();
    setStatus(`已复制到 ${d.target}`);
  } catch (e) {
    setStatus(e.message, "error");
    alert(e.message);
  }
}
function selectedPromptParents() {
  return Array.from(
    new Set([
      ...Array.from($("promptParents")?.selectedOptions || []).map(
        (x) => x.value,
      ),
      ...splitNames($("promptParentsText")?.value || ""),
    ]),
  );
}
function recommendedParents() {
  const pref = [
    "g2_1",
    "g2_2",
    "g8_3",
    "g8_4",
    "g7_1",
    "ModeQueryTCS",
    "ModeQueryTKQ",
  ];
  const names = protocolNames();
  const chosen = pref.filter((x) => names.includes(x));
  names.forEach((n) => {
    if (chosen.length < 4 && !chosen.includes(n)) chosen.push(n);
  });
  return chosen.slice(0, 4);
}
function fillRecommendedParents() {
  const p = recommendedParents();
  if ($("promptParentsText")) $("promptParentsText").value = p.join(", ");
  if ($("iterateParents")) $("iterateParents").value = p.join(", ");
  Array.from($("promptParents")?.options || []).forEach(
    (o) => (o.selected = p.includes(o.value)),
  );
  renderParentPoolBars();
}
async function generatePrompts() {
  setStatus("生成提示词文件", "busy");
  try {
    const d = await api("/api/selfopt/prompts", {
      method: "POST",
      body: JSON.stringify({
        scene: state.selectedScene,
        parents: selectedPromptParents(),
      }),
    });
    $("aiOutput").textContent = `已生成 ${d.generated_count} 个提示词文件`;
    await loadState(true);
    setStatus(`生成 ${d.generated_count} 个提示词`);
  } catch (e) {
    setStatus(e.message, "error");
    $("aiOutput").textContent = e.message;
  }
}
function aiRequest(mode) {
  updateNamesForTask(false);
  return {
    scene: state.selectedScene,
    parent_a: $("aiParentA").value,
    parent_b: $("aiParentB").value,
    protocol_name: $("aiProtocolName").value,
    target_generation: $("aiTargetGeneration").value,
    run_mode: mode,
    task_profile: $("taskProfile").value,
    custom_task_text: customTaskTextFor("ai"),
    requirement_text: $("requirementText").value,
    max_repair_rounds: Number($("aiRepairRounds").value || 0),
    ...llmPayload(),
  };
}
const stageDefs = {
  ai: [
    ["queued", "排队", "后台任务已接收"],
    ["validated", "校验", "场景/父代/画像"],
    ["connecting_model", "连接模型", "网关与 Key"],
    ["running_model", "生成", "模型输出协议"],
    ["validation", "验证", "沙箱与信息隔离"],
    ["done", "完成", "协议可查看"],
  ],
  iteration: [
    ["planning", "规划", "父代组合"],
    ["iteration", "轮次", "逐轮生成"],
    ["running_model", "模型", "调用或预览"],
    ["validation", "验证", "沙箱检查"],
    ["persisting", "落盘", "写入记录"],
    ["done", "完成", "结果可查看"],
  ],
};
function renderStageFlow(job, prefix) {
  const el = $(`${prefix}StageFlow`);
  if (!el) return;
  const progress = Number(job?.progress || 0);
  const status = job?.status || "queued";
  const stage = String(job?.stage || "queued");
  const defs = stageDefs[prefix] || [];
  el.innerHTML = defs
    .map((s, i) => {
      const min = i === 0 ? 0 : Math.round(i * (100 / defs.length));
      const next =
        i === defs.length - 1 ? 101 : Math.round((i + 1) * (100 / defs.length));
      let cls =
        progress >= next || status === "success"
          ? "done"
          : progress >= min && progress < next
            ? "active"
            : "";
      if (stage.includes(s[0])) cls = status === "failed" ? "error" : "active";
      if (status === "failed" && cls === "active") cls = "error";
      return `<div class="step-card ${cls}"><b>${esc(i + 1)}. ${esc(s[1])}</b><span>${esc(s[2])}</span></div>`;
    })
    .join("");
}
function renderProgress(job, prefix) {
  $(`${prefix}JobTitle`).textContent =
    `${job.status} · ${job.stage} · ${job.elapsed_s}s`;
  $(`${prefix}JobPct`).textContent = `${job.progress || 0}%`;
  $(`${prefix}JobBar`).style.width = `${job.progress || 0}%`;
  renderStageFlow(job, prefix);
  const log = (job.logs || []).join("\n");
  if (prefix === "ai") $("aiOutput").textContent = log || "等待日志";
  else $("iterationConsole").textContent = log || "等待日志";
}
async function startAsyncJob(kind, payload) {
  const endpoint =
    kind === "ai"
      ? "/api/selfopt/ai-run-async"
      : "/api/selfopt/auto-iterate-async";
  const d = await api(endpoint, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const prefix = kind === "ai" ? "ai" : "iteration";
  renderProgress(d.job, prefix);
  pollJob(d.job.id, kind);
  return d.job;
}
function setJobBusy(kind, busy) {
  const ids = kind === "ai" ? ["previewAi", "runAi"] : ["runIteration"];
  ids.forEach((id) => ($(id).disabled = busy));
}
function pollJob(id, kind) {
  const prefix = kind === "ai" ? "ai" : "iteration";
  clearTimeout(state.pollers[prefix]);
  state.activeJobs[kind] = id;
  setJobBusy(kind, true);
  async function poll() {
    let complete = false;
    try {
      const { job } = await api(
        `/api/selfopt/job?id=${encodeURIComponent(id)}`,
      );
      renderProgress(job, prefix);
      complete = ["success", "failed"].includes(job.status);
      if (complete) {
        if (job.result?.state) {
          state.selfopt = job.result.state;
          renderAll();
        }
        if (kind === "ai") {
          state.lastAi = job.result;
          renderGeneratedProtocols();
          const result = job.result?.result || {};
          if (result.target_file && result.status !== "dry_run")
            previewText(result.target_file, "Protocol", "ai");
          else if (result.prompt_file)
            previewText(result.prompt_file, "本次生成的 Prompt", "ai");
        } else {
          state.lastIteration = job.result;
          renderIterationResult();
          const latest = job.result?.iterations?.at(-1);
          const artifact =
            latest && !job.result.dry_run && latest.status === "success"
              ? latest.target_file
              : latest?.prompt_file;
          if (artifact) previewText(artifact, "本轮生成的资产", "iterate");
        }
        setStatus(
          job.status === "success"
            ? "后台任务完成"
            : job.error || "后台任务失败",
          job.status === "failed" ? "error" : "ready",
        );
      }
    } catch (e) {
      complete = e.status === 404;
      setStatus(e.message, "error");
      // Do not silently detach from accepted work on a transient network failure.
    }
    if (complete) {
      delete state.activeJobs[kind];
      setJobBusy(kind, false);
    } else {
      state.pollers[prefix] = setTimeout(poll, 1500);
    }
  }
  state.pollers[prefix] = setTimeout(poll, 800);
}
async function runAi(mode) {
  const button = mode === "preview" ? $("previewAi") : $("runAi");
  button.disabled = true;
  setStatus(mode === "preview" ? "启动预览任务" : "启动大模型生成", "busy");
  try {
    await startAsyncJob("ai", aiRequest(mode));
    setStatus("后台任务已启动，正在轮询进度", "busy");
  } catch (e) {
    setStatus(e.message, "error");
    $("aiOutput").textContent = e.message;
  } finally {
    setJobBusy("ai", Boolean(state.activeJobs.ai));
  }
}
async function probeModel(mode) {
  const version = state.aiConfigVersion;
  ["probeModel", "listModels"].forEach((id) => ($(id).disabled = true));
  setStatus(mode === "list" ? "列出模型" : "检查模型", "busy");
  try {
    const d = await api("/api/selfopt/ai-models", {
      method: "POST",
      body: JSON.stringify({ mode, ...llmPayload(), max_tokens: 64 }),
    });
    if (version !== state.aiConfigVersion) {
      setStatus("配置已修改，请检查新配置");
      return;
    }
    $("aiOutput").textContent =
      mode === "list"
        ? `可用模型：\n${(d.models || []).slice(0, 80).join("\n")}`
        : JSON.stringify(d, null, 2);
    state.aiConnection = {
      status: mode === "list" ? "listed" : "success",
      message: mode === "list"
        ? `模型列表读取成功，共 ${(d.models || []).length} 个模型；选择支持的模型后可检查连接。`
        : `模型探测成功：${d.model} 返回 ${d.reply || "OK"}，已自动识别 ${d.protocol} 接口。`,
      protocol: d.protocol,
    };
    renderAiRuntime();
    renderAudit();
    setStatus("模型接口正常");
  } catch (e) {
    if (version !== state.aiConfigVersion) {
      setStatus("配置已修改，请检查新配置");
      return;
    }
    setStatus(e.message, "error");
    state.aiConnection = { status: "failed", message: `模型探测失败：${e.message}` };
    renderAiRuntime();
    renderAudit();
    $("aiOutput").textContent = e.message;
  } finally {
    ["probeModel", "listModels"].forEach((id) => ($(id).disabled = false));
  }
}
function renderPrompts() {
  const rows = currentScene()?.prompts || [];
  $("promptCountPill").textContent = rows.length;
  const paged = pageRows(
    "promptRows",
    rows,
    renderPrompts,
    currentScene()?.name,
  );
  $("promptRows").innerHTML = rows.length
    ? paged
        .map(
          (r) =>
            `<tr><td><b title="${esc(r.name)}">${esc(r.name)}</b><div class="muted mono" title="${esc(r.path)}">${esc(r.path)}</div></td><td>${esc(bytes(r.size))}</td><td><button class="btn secondary" type="button" data-preview="${esc(r.path)}">查看 Prompt</button></td></tr>`,
        )
        .join("")
    : empty(3);
  document
    .querySelectorAll("#promptRows [data-preview]")
    .forEach(
      (el) =>
        (el.onclick = () => previewText(el.dataset.preview, "Prompt 预览")),
    );
}
function renderGeneratedProtocols() {
  const jobs = state.selfopt?.ai_jobs || [];
  const latest = state.lastAi;
  const rows = [];
  if (latest)
    rows.push({
      id: latest.job_id,
      scene: latest.request.scene,
      parent_a: latest.request.parent_a,
      parent_b: latest.request.parent_b,
      protocol_name: latest.request.protocol_name,
      status: latest.result.status,
      target_file: latest.result.target_file,
      prompt_file: latest.result.prompt_file,
      task_profile: latest.request.task_profile,
      dry_run: latest.request.dry_run,
      protocol_exists: latest.result.protocol_exists,
    });
  jobs.forEach((j) => {
    if (!rows.some((x) => x.id === j.id)) rows.push(j);
  });
  const paged = pageRows("generatedProtocols", rows, renderGeneratedProtocols);
  $("generatedProtocols").innerHTML = rows.length
    ? paged
        .map((j) => {
          const isPreview = j.status === "dry_run" || Number(j.dry_run);
          const canView = j.target_file && !isPreview;
          return `<div class="generated-card ${isPreview ? "preview" : j.status === "failed" ? "failed" : ""}"><div><b>${esc(j.protocol_name)}</b> <span class="pill ${j.status === "success" ? "green" : j.status === "failed" ? "red" : isPreview ? "blue" : "amber"}">${esc(isPreview ? "预览" : j.status)}</span></div><div class="muted">任务 #${esc(j.id)} · ${esc(j.scene)} · ${esc(j.parent_a)} + ${esc(j.parent_b)} · ${esc(j.task_profile || "")}</div><div class="muted mono">目标：${esc(j.target_file || "")}</div><div class="muted mono">Prompt：${esc(j.prompt_file || "")}</div>${isPreview ? `<div class="mini-note">这是预览任务：已生成 Prompt，但不会调用模型，也不会创建协议文件。点击“生成协议代码”后才会出现可查看的 .py 协议。</div>` : ""}<div class="actions"><button class="btn secondary" type="button" data-preview="${esc(j.prompt_file || "")}">查看 Prompt</button>${canView ? `<button class="btn secondary" type="button" data-protocol="${esc(j.target_file)}">查看协议</button>` : `<button class="btn secondary" type="button" disabled>${isPreview ? "未生成协议" : "协议不可用"}</button>`}</div></div>`;
        })
        .join("")
    : `<div class="muted">还没有生成记录</div>`;
  document
    .querySelectorAll("#generatedProtocols [data-protocol]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          previewText(el.dataset.protocol, "生成协议预览", "ai")),
    );
  document
    .querySelectorAll("#generatedProtocols [data-preview]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          previewText(el.dataset.preview, "Prompt 预览", "ai")),
    );
}
async function runValidate() {
  $("runValidate").disabled = true;
  setStatus("验证协议", "busy");
  try {
    const d = await api("/api/selfopt/validate", {
      method: "POST",
      body: JSON.stringify({
        scene: state.selectedScene,
        protocol: $("validateProtocol").value,
        task_profile: $("validateTaskProfile").value,
        custom_task_text: customTaskTextFor("validate"),
        num_tags: Number($("validateNumTags").value),
        total_runs: Number($("validateRuns").value),
        price_min: Number($("validatePriceMin").value),
        price_max: Number($("validatePriceMax").value),
        relative_tolerance: Number($("validateTolerance").value),
      }),
    });
    $("validateMetrics").innerHTML = [
      [
        "硬约束",
        d.validator_passed ? "通过" : "失败",
        d.validator_passed ? "tone-green" : "tone-red",
      ],
      ["功能命中", d.pass_rate + "%", "tone-teal"],
      [
        "信息隔离",
        d.information_isolation_passed ? "通过" : "告警",
        d.information_isolation_passed ? "tone-green" : "tone-amber",
      ],
      ["平均耗时", formatDurationMs(d.avg_time_ms), "tone-amber"],
    ]
      .map(
        ([k, v, t]) =>
          `<div class="metric ${t}"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`,
      )
      .join("");
    const output = $("validateOutput");
    output.innerHTML = `<div class="validation-summary"><div class="pass-ring" style="--pass:${Math.max(0, Math.min(100, Number(d.pass_rate) || 0))}%"><strong>${esc(d.pass_rate)}%</strong></div><div><h3>${esc(d.task_title)}</h3><p>已完成 ${d.completed_runs} / ${d.total_runs} 次验证 · ${d.num_tags} 个标签</p><p class="muted">${esc(d.runtime_error || "点击下方结构展开检查报告与样本细节。")}</p></div></div><div class="validation-samples"></div><details class="validation-details"><summary>检查报告与原始结果</summary><div class="validation-tree"></div></details>`;
    output.querySelector(".validation-samples").innerHTML = (d.samples || [])
      .map(
        (sample) =>
          `<div class="sample-card"><span>第 ${sample.run} 次</span><b class="${sample.hit ? "upload-success" : "upload-error"}">${sample.hit ? "命中" : "未命中"}</b><span>${formatDurationMs(sample.exec_time_ms)}</span></div>`,
      )
      .join("");
    output.querySelector(".validation-tree").append(jsonNode(d));
    setStatus("验证完成");
  } catch (e) {
    setStatus(e.message, "error");
    $("validateOutput").textContent = e.message;
  } finally {
    $("runValidate").disabled = false;
  }
}
function renderParentPoolBars() {
  const parents = splitNames(
    $("iterateParents")?.value || $("promptParentsText")?.value || "",
  ).length
    ? splitNames(
        $("iterateParents")?.value || $("promptParentsText")?.value || "",
      )
    : recommendedParents();
  const counts = {};
  (currentScene()?.protocol_files || []).forEach(
    (r) => (counts[r.name] = (counts[r.name] || 0) + 1),
  );
  const rows = parents.map((n) => ({ name: n, value: counts[n] || 1 }));
  const max = Math.max(1, ...rows.map((x) => x.value));
  $("parentPoolBars").innerHTML = rows.length
    ? rows
        .map(
          (x) =>
            `<div class="bar-row"><span class="mono">${esc(x.name)}</span><div class="bar"><span style="width:${Math.max(10, (x.value / max) * 100)}%;background:var(--violet)"></span></div><span>${esc(x.value)}</span></div>`,
        )
        .join("")
    : `<div class="muted">暂无父代协议</div>`;
}
async function runIteration() {
  $("runIteration").disabled = true;
  updateNamesForTask(false);
  setStatus("启动自动迭代", "busy");
  try {
    await startAsyncJob("iterate", {
      scene: state.selectedScene,
      parents: splitNames($("iterateParents").value),
      prefix: $("iteratePrefix").value,
      iterations: Number($("iterateCount").value),
      dry_run: $("iteratePreviewOnly").checked,
      task_profile: $("iterateTaskProfile").value,
      custom_task_text: customTaskTextFor("iterate"),
      requirement_text: $("iterateRequirementText").value,
      max_repair_rounds: Number($("iterateRepairRounds").value),
      ...llmPayload(),
      validate_num_tags: Number($("iterateValidateTags").value),
    });
    setStatus("自动迭代已启动，正在轮询进度", "busy");
  } catch (e) {
    setStatus(e.message, "error");
    $("iterationConsole").textContent = e.message;
  } finally {
    setJobBusy("iterate", Boolean(state.activeJobs.iterate));
  }
}
function renderIterationResult() {
  const d = state.lastIteration;
  if (!d) return;
  $("iterationSummary").textContent =
    `${d.iterations.length} 轮 / ${d.dry_run ? "预览" : "真实生成"}`;
  const pagedIterations = pageRows(
    "iterationRows",
    d.iterations,
    renderIterationResult,
  );
  $("iterationRows").innerHTML = d.iterations.length
    ? pagedIterations
        .map((r) => {
          const isPreview = r.status === "dry_run" || d.dry_run;
          const canView = r.target_file && !isPreview;
          return `<tr><td>${esc(r.iteration)}</td><td>${esc(r.parent_a)} + ${esc(r.parent_b)}</td><td><b>${esc(r.protocol_name)}</b><div class="muted">job #${esc(r.job_id)}</div></td><td><span class="pill ${r.status === "success" ? "green" : isPreview ? "blue" : "amber"}">${esc(isPreview ? "预览" : r.status)}</span></td><td><button class="btn secondary" type="button" data-preview="${esc(r.prompt_file || "")}">查看 Prompt</button></td><td>${canView ? `<button class="btn secondary" type="button" data-protocol="${esc(r.target_file)}">查看协议</button>` : `<button class="btn secondary" type="button" data-missing="${esc(r.target_file || "")}">未生成协议</button>`}</td></tr>`;
        })
        .join("")
    : empty(6);
  document
    .querySelectorAll("#iterationRows [data-preview]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          previewText(el.dataset.preview, "迭代 Prompt 预览", "iterate")),
    );
  document
    .querySelectorAll("#iterationRows [data-protocol]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          previewText(el.dataset.protocol, "迭代协议预览", "iterate")),
    );
  document
    .querySelectorAll("#iterationRows [data-missing]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          previewText(
            el.dataset.missing,
            "协议文件未生成",
            "iterate",
            `当前轮次是预览任务，只生成 Prompt，不创建协议文件。\n目标路径预留为：${rel(el.dataset.missing)}`,
          )),
    );
}
async function runExperiment(task, button = null) {
  const names = {
    robust: "鲁棒性实验",
    plot_robust: "鲁棒性图",
    plot_evolution: "进化图",
    plot_final: "终稿图",
  };
  setStatus(`运行${names[task] || "实验任务"}`, "busy");
  if (button) button.disabled = true;
  try {
    updateExperimentProtocolSpec();
    const payload = {
      task,
      source_scene: $("expScene").value,
      protocols: $("expProtocolSpec").value,
      factors: selectedValues("expFactors").join(",") || "density",
      task_profile: $("expTaskProfile").value,
      custom_task_text: customTaskTextFor("exp"),
      num_tags: Number($("expNumTags").value),
      total_runs: Number($("expTotalRuns").value),
      point_count: Number($("expPointCount").value),
      output: $("expOutput").value,
      csv: $("expOutput").value,
      scenario: $("evoScenario").value,
      charts: selectedValues("evoCharts").join(",") || "correction",
      data: $("evoData").value,
      output_dir: $("evoOutputDir").value,
    };
    $("expConsole").textContent =
      `正在运行 ${names[task] || task}...\n\n任务参数\n${JSON.stringify(payload, null, 2)}\n\n若协议本身较慢，页面会在请求结束后一次性展示 stdout/stderr。`;
    if ($("experimentPreview"))
      $("experimentPreview").innerHTML =
        `<div class="mini-note">正在生成 ${esc(names[task] || task)}，完成后会自动刷新实验产物列表。长耗时任务请看右上角状态和下方控制台。</div>`;
    const d = await api("/api/selfopt/experiments", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.lastExperiment = d;
    state.selfopt.experiment_artifacts =
      d.artifacts || state.selfopt.experiment_artifacts;
    renderArtifacts();
    $("expConsole").textContent =
      `TASK\n${d.task}\n\nTASK_PROFILE\n${d.task_profile || payload.task_profile} · ${d.task_title || taskInfo(payload.task_profile).title}\n\n协议来源\n${payload.protocols}\n\nCOMMAND\n${d.command.join(" ")}\n\nRETURN_CODE\n${d.returncode}\n\nSTDOUT\n${d.stdout || ""}\n\nSTDERR\n${d.stderr || ""}`;
    const latest = (d.artifacts || [])[0];
    if (latest) previewExperimentArtifact(latest.path);
    setStatus(
      `实验完成 ${d.wall_clock_s}s${d.returncode === 0 ? "" : "，脚本返回非 0"}`,
      d.returncode === 0 ? "ready" : "error",
    );
  } catch (e) {
    setStatus(e.message, "error");
    $("expConsole").textContent = e.message;
  } finally {
    if (button) button.disabled = false;
  }
}
function artifactButton(path, label = "预览", attr = "artifact") {
  return `<button class="btn secondary" type="button" data-${attr}="${esc(path)}">${esc(label)}</button>`;
}
function renderArtifacts() {
  const rows = state.selfopt?.experiment_artifacts || [];
  $("artifactCount").textContent = rows.length;
  const query = assetFilter("artifactRows", renderArtifacts);
  const visible = filterAssetRows(rows, query);
  const paged = pageRows("artifactRows", visible, renderArtifacts, query);
  $("artifactRows").innerHTML = visible.length
    ? paged
        .map(
          (r) =>
            `<tr><td><b title="${esc(r.name)}">${esc(r.name)}</b><div class="muted mono" title="${esc(r.path)}">${esc(r.path)}</div></td><td><span class="pill">${esc(r.kind)}</span></td><td>${esc(bytes(r.size))}</td><td>${artifactButton(r.path, "本页预览", "exp-artifact")}</td></tr>`,
        )
        .join("")
    : empty(4);
  document
    .querySelectorAll("#artifactRows [data-exp-artifact]")
    .forEach(
      (el) =>
        (el.onclick = () => previewExperimentArtifact(el.dataset.expArtifact)),
    );
}
function renderOutputs() {
  const rows = state.selfopt?.output_artifacts || [];
  $("outputCount").textContent = rows.length;
  const query = assetFilter("outputRows", renderOutputs);
  const visible = filterAssetRows(rows, query);
  const paged = pageRows("outputRows", visible, renderOutputs, query);
  $("outputRows").innerHTML = visible.length
    ? paged
        .map(
          (r) =>
            `<tr><td><b>${esc(r.run)}</b></td><td><b title="${esc(r.name)}">${esc(r.name)}</b><div class="muted mono" title="${esc(r.path)}">${esc(r.path)}</div></td><td><span class="pill">${esc(r.kind)}</span><div class="muted">${esc(bytes(r.size))}</div></td><td>${artifactButton(r.path)}</td></tr>`,
        )
        .join("")
    : empty(4, "暂无 outputs 产物");
  document
    .querySelectorAll("#outputRows [data-artifact]")
    .forEach((el) => (el.onclick = () => previewArtifact(el.dataset.artifact)));
}
async function previewExperimentArtifact(path) {
  $("experimentPreviewTitle").textContent = "资产预览";
  await renderAssetPreview(path, "experimentPreview");
}
async function previewArtifact(path) {
  $("outputPreviewTitle").textContent = "资产预览";
  switchView("outputs");
  await renderAssetPreview(path, "outputPreview");
}
function renderAudit() {
  const counts = state.selfopt?.database_counts || {};
  $("auditMetrics").innerHTML = [
    "runs",
    "design_cases",
    "ai_jobs",
    "system_events",
  ]
    .map(
      (k, i) =>
        `<div class="metric ${tone(i)}"><span>${esc(k)}</span><strong>${esc(counts[k] || 0)}</strong></div>`,
    )
    .join("");
  const config = llmPayload();
  $("runtimeTags").innerHTML = [
    ["API URL", config.base_url || false],
    ["模型", config.model || false],
    ["API Key", Boolean(config.api_key_value)],
    ["连接", state.aiConnection?.status === "success" ? `已验证 · ${state.aiConnection.protocol}` : "待验证"],
  ]
    .map(
      ([k, v]) =>
        `<span class="pill ${v === false || v === "待验证" ? "amber" : "green"}">${esc(k)}：${esc(v === true ? "已填写" : v === false ? "未填写" : v)}</span>`,
    )
    .join("");
  const jobs = state.selfopt?.ai_jobs || [];
  $("aiJobCount").textContent = jobs.length;
  const pagedJobs = pageRows("aiJobRows", jobs, renderAudit);
  $("aiJobRows").innerHTML = jobs.length
    ? pagedJobs
        .map(
          (j) =>
            `<tr><td>${esc(j.id)}</td><td>${esc(j.created_at)}</td><td>${esc(j.scene)}</td><td>${esc(j.parent_a)} + ${esc(j.parent_b)}</td><td>${esc(j.protocol_name)}</td><td><span class="pill ${j.status === "success" ? "green" : j.status === "dry_run" ? "blue" : "amber"}">${esc(j.status === "dry_run" ? "预览" : j.status)}</span></td></tr>`,
        )
        .join("")
    : empty(6);
}
function renderAll() {
  fillSceneSelects();
  fillTaskProfiles();
  fillProtocolControls();
  fillExperimentControls();
  renderAiRuntime();
  renderOverview();
  renderAssets();
  renderPrompts();
  renderGeneratedProtocols();
  renderAudit();
  renderArtifacts();
  renderOutputs();
  renderStatistics();
  renderParentPoolBars();
  if (state.lastIteration) renderIterationResult();
}
async function loadState(keepScene = true) {
  setStatus("同步平台状态", "busy");
  const d = await api("/api/selfopt/state");
  state.selfopt = d;
  if (!keepScene || !state.selectedScene)
    state.selectedScene = d.default_scene || d.scenes?.[0]?.name || "";
  if (
    !d.scenes.some((scene) => scene.name === state.selectedScene) &&
    d.scenes?.length
  )
    state.selectedScene = d.scenes[0].name;
  renderAll();
  setStatus("就绪");
}
function syncSceneFrom(id) {
  state.selectedScene = $(id).value;
  ["assetScene", "aiScene", "validateScene", "iterateScene"].forEach((o) => {
    if ($(o).value !== state.selectedScene) $(o).value = state.selectedScene;
  });
  fillProtocolControls();
  fillExperimentControls();
  renderOverview();
  renderAssets();
  renderPrompts();
}
function openManual(page = state.currentView) {
  $("manualTitle").textContent =
    `${navItems.find((x) => x[0] === page)?.[1] || "页面"}说明书`;
  $("manualBody").innerHTML =
    (manuals[page] || manuals.overview)
      .map((x) => `<p>${esc(x)}</p>`)
      .join("") +
    `<div class="mini-note">说明书随当前页面切换；每个主要按钮在这里和页面蓝色说明框中都有解释。</div>`;
  $("manualModal").classList.add("show");
  $("manualModal").querySelector(".modal").focus();
}
function closeManual() {
  $("manualModal").classList.remove("show");
  $("openManualTop").focus();
}
function closeFilePreview() {
  $("filePreviewModal").classList.remove("show");
}
function empty(cols, msg = "暂无数据") {
  return `<tr><td colspan="${cols}" class="muted">${esc(msg)}</td></tr>`;
}
function bind() {
  document
    .querySelectorAll("[data-go]")
    .forEach(
      (button) => (button.onclick = () => switchView(button.dataset.go)),
    );
  $("assetSearch").oninput = renderAssets;
  $("fileEditor").oninput = updateEditorState;
  window.addEventListener("beforeunload", (event) => {
    if (state.editorDirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  document.addEventListener("keydown", (event) => {
    if (
      (event.ctrlKey || event.metaKey) &&
      event.key.toLowerCase() === "s" &&
      state.currentView === "assets"
    ) {
      event.preventDefault();
      if (!$("saveFile").disabled) saveCurrentFile();
    }
    const modal = document.querySelector(".modal-backdrop.show");
    if (!modal) return;
    if (event.key === "Escape") {
      closeManual();
      closeFilePreview();
    }
    if (event.key === "Tab") {
      const focusable = modal.querySelectorAll(
        "button, a[href], input, textarea, select, [tabindex='0']",
      );
      const first = focusable[0],
        last = focusable[focusable.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === modal.querySelector(".modal"))
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  document.querySelectorAll(".modal-backdrop").forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeManual();
        closeFilePreview();
      }
    });
    new MutationObserver(() => {
      if (modal.classList.contains("show"))
        modal.querySelector(".modal").focus();
      document.body.style.overflow = document.querySelector(
        ".modal-backdrop.show",
      )
        ? "hidden"
        : "";
    }).observe(modal, { attributes: true, attributeFilter: ["class"] });
  });
  ["assetScene", "aiScene", "validateScene", "iterateScene"].forEach(
    (id) => ($(id).onchange = () => syncSceneFrom(id)),
  );
  document
    .querySelectorAll("[data-help]")
    .forEach((b) => (b.onclick = () => openManual(b.dataset.help)));
  $("openManualTop").onclick = () => openManual();
  $("closeManual").onclick = closeManual;
  $("closeFilePreview").onclick = closeFilePreview;
  $("createScene").onclick = createScene;
  $("copyBaseline").onclick = copyBaseline;
  $("refreshAssets").onclick = () => loadState(true);
  $("reloadFile").onclick = () =>
    state.currentFile && loadFile(state.currentFile);
  $("saveFile").onclick = saveCurrentFile;
  if ($("generatePrompts")) $("generatePrompts").onclick = generatePrompts;
  if ($("useTopParents")) $("useTopParents").onclick = fillRecommendedParents;
  $("previewAi").onclick = () => runAi("preview");
  $("runAi").onclick = () => runAi("generate");
  $("probeModel").onclick = () => probeModel("probe");
  $("listModels").onclick = () => probeModel("list");
  ["aiBaseUrl", "aiModel", "aiApiKey"].forEach((id) => {
    $(id).oninput = () => {
      state.aiConfigVersion += 1;
      state.aiConnection = null;
      renderAiRuntime();
      renderAudit();
    };
  });
  $("runValidate").onclick = runValidate;
  $("runIteration").onclick = runIteration;
  $("useTopIterParents").onclick = fillRecommendedParents;
  $("iterateParents").oninput = renderParentPoolBars;
  if ($("promptParentsText"))
    $("promptParentsText").oninput = renderParentPoolBars;
  $("taskProfile").onchange = () => {
    updateNamesForTask(true);
    renderTaskProfileInfo();
  };
  $("validateTaskProfile").onchange = renderTaskProfileInfo;
  $("iterateTaskProfile").onchange = () => {
    updateNamesForTask(true);
    renderTaskProfileInfo();
  };
  $("expTaskProfile").onchange = renderTaskProfileInfo;
  $("expScene").onchange = fillExperimentControls;
  $("expProtocol").onchange = updateExperimentProtocolSpec;
  $("runRobustExp").onclick = (e) => runExperiment("robust", e.currentTarget);
  $("plotRobust").onclick = (e) =>
    runExperiment("plot_robust", e.currentTarget);
  $("plotEvolution").onclick = (e) =>
    runExperiment("plot_evolution", e.currentTarget);
  $("plotFinal").onclick = (e) => runExperiment("plot_final", e.currentTarget);
  bindStatistics();
}
function prepareWorkspace() {
  document.querySelectorAll(".guide").forEach((guide) => {
    const details = document.createElement("details");
    details.className = "guide";
    const summary = document.createElement("summary");
    summary.textContent = "操作指引与页面说明";
    const content = document.createElement("div");
    content.className = "guide-content";
    while (guide.firstChild) content.appendChild(guide.firstChild);
    details.append(summary, content);
    guide.replaceWith(details);
  });
  document.querySelectorAll(".form-grid label").forEach((label) => {
    const control = label.parentElement.querySelector("input,select,textarea");
    if (control?.id) label.htmlFor = control.id;
  });
  updateEditorState();
}
prepareWorkspace();
installAssetUploads();
mountNav();
bind();
switchView(location.hash.slice(1) || "overview");
loadState(false)
  .then(() => {
    fillRecommendedParents();
    updateNamesForTask(true);
    renderTaskProfileInfo();
  })
  .catch((e) => {
    console.error(e);
    setStatus(e.message, "error");
  });
