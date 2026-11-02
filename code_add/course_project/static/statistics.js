/* Statistics evidence UI. No chart/CDN dependencies or credentials in storage. */
const statisticsUI = {
  loading: null,
  initialized: false,
  protocol: "",
  page: 1,
  detailToken: 0,
  candidateId: "",
  job: null,
};
const statisticsLabels = {
  mean: "均值", variance: "总体方差", cardinality: "在场标签数", top_k: "Top-k",
  single_peak: "单峰", multi_peak: "多峰", gaussian: "高斯", long_tail: "长尾", uniform: "均匀",
  polling: "Polling 逐标签读取", full_identification: "全识别（乐观下界）",
  single_serial: "单任务串联（无共享）", shared_sketch: "共享草图（无复核）",
  single_mean: "单任务均值", single_variance: "单任务方差", single_cardinality: "单任务基数", single_top_k: "单任务 Top-k",
  fixed_2: "固定 2 轮", fixed_3: "固定 3 轮", fixed_4: "固定 4 轮",
  adaptive: "自适应 τ=1", adaptive_t05: "自适应 τ=0.5", adaptive_t20: "自适应 τ=2",
  random_sketch_control: "随机参数（非 LLM）", llm_random: "真实 LLM 单次候选",
  execution_failed: "执行失败", communication_cost_missing: "通信成本缺失",
  communication_cost_not_independently_measured: "通信成本为候选自报",
  error_unavailable: "误差不可用", validation_failed: "验证门禁失败", multiple_scenarios: "跨场景汇总",
  duplicate_seed: "seed 重复", dataset_hash_missing: "缺少数据 hash",
  experiment_audit_failed: "实验未完成或审计失败",
};
const statisticsColors = ["#16877e", "#356ce8", "#7c62ca", "#b77a22", "#c24452", "#26875b", "#526b84", "#b45c8d", "#488ba2", "#926c42"];

function fmtStat(value, digits = 2) {
  return value === null || value === undefined || !Number.isFinite(Number(value))
    ? "—" : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
function statPercent(value) {
  return value === null || value === undefined ? "—" : `${fmtStat(value * 100)}%`;
}
function statLabel(value) { return statisticsLabels[value] || value; }
function statScenario(row) { return `${row.missing_rate}|${row.noise_rate}|${row.extreme_rate}`; }
function statModeData() {
  return state.statistics?.modes?.[$("statisticsMode").value] || { rows: [], artifacts: [] };
}
function statSelectedRows() {
  return statModeData().rows.filter((row) =>
    row.task === $("statisticsTask").value &&
    ($("statisticsDistribution").value === "all" || row.distribution === $("statisticsDistribution").value) &&
    ($("statisticsScale").value === "all" || String(row.num_tags) === $("statisticsScale").value) &&
    ($("statisticsScenario").value === "all" || statScenario(row) === $("statisticsScenario").value),
  );
}
function statFillSelect(id, values, fallback, includeAll = true, label = statLabel) {
  const el = $(id), previous = el.value;
  const choices = [...new Set(values.map(String))];
  el.innerHTML = (includeAll ? '<option value="all">全部</option>' : "") + choices.map((v) => `<option value="${esc(v)}">${esc(label(v))}</option>`).join("");
  const allowed = [...choices, ...(includeAll ? ["all"] : [])];
  el.value = statisticsUI.initialized && allowed.includes(previous) ? previous : (allowed.includes(String(fallback)) ? fallback : choices[0]);
}
function renderStatistics() {
  if (!$("statisticsMetrics") || !state.statistics?.available) return;
  const all = statModeData().rows;
  statFillSelect("statisticsTask", all.map((r) => r.task), "mean", false);
  statFillSelect("statisticsDistribution", all.map((r) => r.distribution), "multi_peak");
  statFillSelect("statisticsScale", all.map((r) => r.num_tags).sort((a, b) => a - b), "1000");
  statFillSelect("statisticsScenario", all.map(statScenario), statScenario(all[0] || {}), true, (s) => s.split("|").map((v) => statPercent(Number(v))).join(" / "));
  statisticsUI.initialized = true;
  renderStatisticsProvenance();
  loadStatisticsComparison();
}
let comparisonToken = 0;
async function loadStatisticsComparison() {
  const token = ++comparisonToken;
  const params = new URLSearchParams({ mode: $("statisticsMode").value, task: $("statisticsTask").value,
    distribution: $("statisticsDistribution").value, num_tags: $("statisticsScale").value,
    scenario: $("statisticsScenario").value, axis: $("statisticsCostAxis").value });
  try {
    const result = await api(`/api/statistics/comparison?${params}`);
    if (token !== comparisonToken) return;
    const rows = result.rows;
    $("statisticsMetrics").innerHTML = [
      ["实验 seeds", state.statistics.manifest.seeds.length, "tone-teal"],
      ["当前条件 / 协议", `${result.case_count} / ${rows.length}`, "tone-green"],
      ["逐 seed 结果", result.raw_count, "tone-violet"],
      ["失败 / 待实测", rows.reduce((n, r) => n + r.failures, 0), "tone-amber"],
    ].map(([label, value, toneName]) => `<div class="metric ${toneName}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
    $("statisticsFilterNote").textContent = result.single_scenario
      ? "同场景比较；后端计算置信区间与 Pareto，缺少成本、执行失败或验证失败会明确排除。"
      : "跨场景描述性汇总；不绘制 Pareto 前沿。请选择单一分布、规模和扰动查看前沿与置信区间。";
    drawStatisticsScatter(rows);
    renderStatisticsTable(rows);
    renderStatisticsInsights(statSelectedRows());
    renderStatisticsArtifacts();
  } catch(e) { if (token === comparisonToken) $("statisticsScatter").textContent = e.message; }
}

function renderStatisticsProvenance() {
  const host = $("statisticsProvenance");
  const pill = $("statisticsAuditStatus");
  if (!host || !state.statistics?.available) return;
  const provenance = state.statistics.provenance || {};
  const audit = provenance.audit || {};
  const passed = audit.passed === true;
  const unavailable = audit.status === "not_available";
  if (pill) {
    pill.textContent = passed ? "审计通过" : unavailable ? "未审计" : "审计失败";
    pill.className = `pill ${passed ? "green" : unavailable ? "amber" : "red"}`;
  }
  const mismatches = [...(audit.mismatched || []), ...(audit.missing || []), ...(audit.unexpected || [])];
  host.innerHTML = [
    ["reproducibility key", provenance.reproducibility_key || "—"],
    ["源码 revision", provenance.revision || "unknown"],
    ["已登记产物", `${provenance.artifact_hash_count || 0} 个 SHA-256`],
    ["审计结果", passed ? "manifest 与当前文件一致" : (audit.error || `${mismatches.length} 个产物异常`)],
  ].map(([label, value]) => `<span class="tag"><b>${esc(label)}</b><small class="mono">${esc(value)}</small></span>`).join("");
  const manifest = state.statistics.manifest || {};
  $("statisticsManifestDetails").textContent = JSON.stringify({ task_specs: manifest.task_specs,
    source_hashes: manifest.source_hashes, python_version: manifest.python_version,
    package_versions: manifest.package_versions, reproduction_command: manifest.reproduction_command || state.statistics.commands,
    source_snapshot: manifest.source_snapshot, frozen_llm_path: manifest.frozen_llm_path, audit }, null, 2);
  const files = state.statistics.delivery_artifacts || [];
  $("statisticsDeliveryArtifacts").innerHTML = files.map((a) => `<a class="statistics-artifact" href="/api/selfopt/download?path=${encodeURIComponent(a.path)}"><span class="pill">${esc(a.kind.toUpperCase())}</span><b>${esc(a.name)}</b><small>下载 · ${esc(bytes(a.size))}</small></a>`).join("");
}
function drawStatisticsScatter(rows) {
  const metric = $("statisticsCostAxis").value;
  const valid = rows.filter((r) => r.pareto_eligible && r.mean_error !== null && r[metric] > 0);
  const front = valid.filter((r) => r.is_pareto);
  const minX = Math.max(.5, Math.min(...valid.map((r) => r[metric]), 1e12) * .7);
  const maxX = Math.max(minX * 2, ...valid.map((r) => r[metric] * 1.3));
  const maxY = Math.max(.01, ...valid.map((r) => r.mean_error * 1.2));
  const px = (v) => 70 + (Math.log10(v) - Math.log10(minX)) / (Math.log10(maxX) - Math.log10(minX)) * 605;
  const py = (v) => 278 - v / maxY * 250;
  const ticks = [minX, Math.sqrt(minX * maxX), maxX];
  const yTicks = [0, maxY / 2, maxY];
  const label = $("statisticsCostAxis").selectedOptions[0].textContent;
  const frontGroups = [...new Set(front.map((r) => r.pareto_group))];
  const frontLines = frontGroups.map((group) => {
    const coordinates = front.filter((r) => r.pareto_group === group).sort((a, b) => a[metric] - b[metric]).map((r) => `${px(r[metric])},${py(r.mean_error)}`).join(" ");
    return `<polyline points="${coordinates}" fill="none" stroke="#172f4b" stroke-dasharray="5 5" opacity=".55"/>`;
  }).join("");
  $("statisticsScatter").innerHTML = valid.length ? `<svg viewBox="0 0 720 335" role="img" aria-label="${esc(label)}与误差 Pareto 图">
    ${yTicks.map((v) => `<line x1="70" x2="675" y1="${py(v)}" y2="${py(v)}" stroke="#e5ecf4"/><text x="60" y="${py(v) + 4}" text-anchor="end">${esc(statPercent(v))}</text>`).join("")}
    ${ticks.map((v) => `<text x="${px(v)}" y="304" text-anchor="middle">${esc(fmtStat(v, 0))}</text>`).join("")}
    ${frontLines}
    ${valid.map((r) => {
      const i = rows.indexOf(r), isFront = front.includes(r);
      return `<circle cx="${px(r[metric])}" cy="${py(r.mean_error)}" r="${isFront ? 8 : 6}" fill="${statisticsColors[i % statisticsColors.length]}" stroke="${isFront ? '#172f4b' : 'white'}" stroke-width="1.5" tabindex="0" role="button" data-stat-protocol="${esc(r.protocol)}" aria-label="查看 ${esc(statLabel(r.protocol))} 详情"><title>${esc(statLabel(r.protocol))} | ${esc(label)} ${esc(fmtStat(r[metric]))} | 误差 ${esc(statPercent(r.mean_error))}${isFront ? ' | Pareto 前沿' : ''}</title></circle>`;
    }).join("")}
    <text x="365" y="330" text-anchor="middle">${esc(label)}（对数轴，越低越好）</text>
    <text x="16" y="160" transform="rotate(-90 16 160)" text-anchor="middle">${$("statisticsTask").value === "top_k" ? "1−F1" : "相对误差"}（越低越好）</text>
  </svg>` : '<div class="mini-note">当前筛选没有可参与前沿比较的结果。请选择单一场景，或在下表核对排除原因。</div>';
  $("statisticsPointCount").textContent = `${valid.length} 个点 · ${front.length} 个前沿点`;
  $("statisticsLegend").innerHTML = valid.map((r) => `<button type="button" data-stat-protocol="${esc(r.protocol)}"><i style="background:${statisticsColors[rows.indexOf(r) % statisticsColors.length]}"></i>${esc(statLabel(r.protocol))}${front.includes(r) ? ' ◇' : ''}</button>`).join("");
  bindStatProtocolButtons($("statisticsScatter"));
  bindStatProtocolButtons($("statisticsLegend"));
}
function bindStatProtocolButtons(host) {
  host.querySelectorAll("[data-stat-protocol]").forEach((el) => {
    el.onclick = () => showStatisticsDetails(el.dataset.statProtocol, 1);
    el.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); el.click(); }
    };
  });
}
function renderStatisticsTable(rows) {
  $("statisticsProtocolCount").textContent = `${rows.length} 个协议`;
  const topK = $("statisticsTask").value === "top_k";
  $("statisticsRows").innerHTML = rows.map((r) => {
    const ci = r.ci && r.ci[0] !== null ? (topK ? [1 - r.ci[1], 1 - r.ci[0]] : r.ci) : null;
    const quality = r.mean_error === null ? '<span class="pill amber">待实测 / 失败</span>' : `${topK ? 'F1 ' + statPercent(r.mean_f1) : statPercent(r.mean_error)}${ci ? `<small class="statistics-ci">95% CI ${statPercent(Math.max(0, ci[0]))} – ${statPercent(topK ? Math.min(1, ci[1]) : ci[1])}</small>` : ''}`;
    return `<tr><td><button class="statistics-protocol-link" type="button" data-stat-protocol="${esc(r.protocol)}">${esc(statLabel(r.protocol))}</button><small class="statistics-ci">${esc(r.protocol)}</small><small class="statistics-ci">${esc((r.pareto_exclusion_reasons || []).map(statLabel).join(" / "))}${r.is_pareto ? " ◇ Pareto" : ""}</small></td><td>${esc(statPercent(r.successRate))}<small class="statistics-ci">${r.runs} 次执行<br>精度达标 ${statPercent(r.quality_pass_rate)}</small></td><td>${quality}${r.p95 !== null && r.p95 !== undefined ? `<small class="statistics-ci">误差 P95 ${statPercent(r.p95)}<br>最坏 ${statPercent(r.worst)}</small>` : ''}</td><td>${r.successRate ? esc(fmtStat(r.mean_communication_bits, 0)) : '—'}</td><td>${r.successRate ? `${fmtStat(r.mean_reader_bits, 0)} / ${fmtStat(r.mean_tag_bits, 0)}` : '—'}</td><td>${r.successRate ? `${fmtStat(r.mean_slots, 0)} / ${fmtStat(r.mean_frames)} / ${fmtStat(r.mean_rounds)}` : '—'}</td><td>${r.failures}<small>${esc((r.failure_reasons || []).join(" / "))}</small></td></tr>`;
  }).join("") || empty(7);
  bindStatProtocolButtons($("statisticsRows"));
}
function renderStatisticsInsights(selected) {
  const mode = $("statisticsMode").value, hypotheses = state.statistics.hypotheses;
  const match = (r) => selected.some((s) => s.distribution === r.distribution && s.num_tags === r.num_tags);
  const h1 = hypotheses.H1.filter((r) => match(r) && r.task === $("statisticsTask").value);
  const h2 = hypotheses.H2.filter((r) => match(r) && r.protocol === "adaptive");
  const renderHypothesis = (name, rows) => rows.length ? `<div class="insight-card"><span>${name}：当前条件门槛通过</span><strong>${rows.filter((r) => r.target_passed).length} / ${rows.length}</strong><small>依据配对 seed 和 95% CI 上界判定；未通过条件保留。</small></div>` : '';
  $("statisticsInsights").innerHTML = renderHypothesis("H1 三任务共享", h1) + renderHypothesis("H2 自适应 vs 固定4轮", h2) +
    `<div class="insight-card"><span>研究边界</span><strong>软件匿名 OR 通道仿真</strong><small>发射 bits 与预留 slots 分别计量；大量空槽可能抵消 bits 优势。无真实硬件时延结论。</small></div>` +
    `<div class="insight-card"><span>LLM 基线</span><strong>${state.statistics.manifest.llm_status === 'record_supplied' ? esc(state.statistics.manifest.llm_record?.model || '已提供候选记录') : '待配置模型实测'}</strong><small>${state.statistics.manifest.llm_record ? `一份冻结候选 · ${esc(state.statistics.manifest.llm_record.usage?.total_tokens ?? '用量未返回')} tokens。` : '随机参数对照不会冒充模型生成。'} 执行成功与精度达标分别报告。</small></div>` +
    `<p class="mini-note">当前实验集：${esc($("statisticsMode").selectedOptions[0].textContent)}。${mode === 'blind' ? '盲测 seed 从100开始，调参使用的数据不进入此集合。' : '◇ 表示当前任务与筛选条件下的 Pareto 点。'}</p>`;
}
function statAssetButtons(artifacts) {
  return artifacts.map((a) => `<button class="statistics-artifact" type="button" data-stat-artifact="${esc(a.path)}"><span class="pill">${esc(a.kind.toUpperCase())}</span><b>${esc(a.name)}</b><small>${esc(bytes(a.size))}</small></button>`).join("");
}
function bindStatAssets(host) {
  host.querySelectorAll("[data-stat-artifact]").forEach((el) => el.onclick = async () => {
    await renderAssetPreview(el.dataset.statArtifact, "statisticsAssetPreview");
    $("statisticsAssetPreview").scrollIntoView({ block: "center", behavior: "smooth" });
  });
}
function renderStatisticsArtifacts() {
  const artifacts = statModeData().artifacts || [];
  $("statisticsArtifacts").innerHTML = statAssetButtons(artifacts);
  bindStatAssets($("statisticsArtifacts"));
  const chart = artifacts.find((a) => a.kind === "png");
  $("statisticsChartImage").hidden = !chart;
  if (chart) $("statisticsChartImage").src = `/api/selfopt/raw?path=${encodeURIComponent(chart.path)}`;
}
async function showStatisticsDetails(protocol = statisticsUI.protocol, page = 1) {
  if (!protocol) return;
  statisticsUI.protocol = protocol;
  statisticsUI.page = page;
  const token = ++statisticsUI.detailToken;
  const params = new URLSearchParams({ mode: $("statisticsMode").value, task: $("statisticsTask").value, protocol, distribution: $("statisticsDistribution").value, num_tags: $("statisticsScale").value, scenario: $("statisticsScenario").value, page });
  $("statisticsSelection").textContent = `已选择 ${statLabel(protocol)}；下方显示原始逐 seed 证据。`;
  try {
    const result = await api(`/api/statistics/details?${params}`);
    if (token !== statisticsUI.detailToken) return;
    $("statisticsDetailCount").textContent = `${statLabel(protocol)} · ${result.total} 条`;
    $("statisticsDetails").innerHTML = result.rows.map((r) => {
      const m = r.run_result.metadata;
      return `<details class="statistics-run-detail"><summary><span>seed ${r.seed} · ${esc(statLabel(m.distribution))} · ${fmtStat(m.num_tags, 0)} 标签</span><span class="pill ${r.status === 'success' ? 'green' : 'amber'}">${r.status === 'success' ? '执行成功' : '失败/待实测'}</span><span>${r.failure_reason ? esc(r.failure_reason) : `真值 ${esc(JSON.stringify(r.run_result.truth))} → 估计 ${esc(JSON.stringify(r.run_result.estimate))}`}</span></summary><pre class="console">${esc(JSON.stringify(r, null, 2))}</pre></details>`;
    }).join("") + `<div class="preview-pagination"><span>第 ${page} / ${Math.max(1, Math.ceil(result.total / 20))} 页 · 每页20条</span><div><button type="button" class="btn secondary" id="statisticsPrev" ${page <= 1 ? 'disabled' : ''}>上一页</button><button type="button" class="btn secondary" id="statisticsNext" ${page * 20 >= result.total ? 'disabled' : ''}>下一页</button></div></div>`;
    $("statisticsPrev").onclick = () => showStatisticsDetails(protocol, page - 1);
    $("statisticsNext").onclick = () => showStatisticsDetails(protocol, page + 1);
  } catch (e) {
    if (token === statisticsUI.detailToken) $("statisticsDetails").innerHTML = `<p class="mini-note">${esc(e.message)}</p>`;
  }
}
async function loadStatistics() {
  if (statisticsUI.loading) return statisticsUI.loading;
  statisticsUI.loading = (async () => {
    $("reloadStatistics").disabled = true;
    try {
      state.statistics = await api("/api/statistics/benchmark");
      if (!state.statistics.available) throw new Error(state.statistics.message);
      const record = state.statistics.manifest.llm_record;
      if (!statisticsUI.candidateId && record?.candidate_id) selectStatisticsCandidate({ candidate_id: record.candidate_id, valid: !record.validation_error, path: `Experiments/stat_protocols/llm/${record.candidate_id}/candidate.json` });
      renderStatistics();
      loadConfigRun();
    } catch (e) {
      $("statisticsInsights").innerHTML = `<p class="mini-note">${esc(e.message)} <button type="button" class="btn secondary" id="retryStatistics">重新读取</button></p>`;
      $("retryStatistics").onclick = loadStatistics;
    } finally {
      $("reloadStatistics").disabled = false;
      statisticsUI.loading = null;
    }
  })();
  return statisticsUI.loading;
}
function setStatisticsJobBusy(busy) {
  $("runStatisticsSuite").disabled = busy;
  $("generateStatisticsBaseline").disabled = busy;
  $("importStatisticsCandidate").disabled = busy;
}
function selectStatisticsCandidate(result) {
  statisticsUI.candidateId = result.candidate_id;
  $("statisticsCandidate").textContent = `候选 ${result.candidate_id} · ${result.valid ? '白名单编译通过' : '编译失败，作为负结果保留'} · ${result.path}`;
}
async function pollStatisticsJob(id, kind) {
  setStatisticsJobBusy(true);
  try {
    const { job } = await api(`/api/selfopt/job?id=${encodeURIComponent(id)}`);
    $("statisticsJobLog").textContent = `${job.status} · ${job.elapsed_s}s\n${job.logs.join('\n')}${job.error ? '\n' + job.error : ''}`;
    if (job.status === "success") {
      if (kind === "statistics_llm") selectStatisticsCandidate(job.result);
      else await loadStatistics();
    } else if (job.status !== "failed") {
      statisticsUI.job = setTimeout(() => pollStatisticsJob(id, kind), 1800);
      return;
    }
  } catch (e) { $("statisticsJobLog").textContent = e.message; }
  statisticsUI.job = null;
  setStatisticsJobBusy(false);
}
async function submitStatisticsJob(kind) {
  setStatisticsJobBusy(true);
  try {
    const body = kind === "statistics_llm" ? llmPayload() : { seed_count: 20, sizes: [1000, 10000, 100000], candidate_id: statisticsUI.candidateId || undefined };
    const endpoint = kind === "statistics_llm" ? "generate-baseline" : "run";
    $("statisticsJobLog").textContent = "正在提交后台任务…";
    const { job } = await api(`/api/statistics/${endpoint}`, { method: "POST", body: JSON.stringify(body) });
    pollStatisticsJob(job.id, kind);
  } catch (e) {
    $("statisticsJobLog").textContent = e.message;
    setStatisticsJobBusy(false);
  }
}
function bindStatistics() {
  $("runEngineeringPipeline").onclick = submitEngineeringPipeline;
  $("reloadEngineeringPipeline").onclick = () => loadEngineeringPipeline();
  $("pipelineRunSelect").onchange = () => loadEngineeringPipeline($("pipelineRunSelect").value);
  $("runConfigExperiment").onclick = () => submitConfigJob(false);
  $("rebuildConfigEvidence").onclick = () => submitConfigJob(true);
  $("reloadConfigRun").onclick = () => loadConfigRun();
  $("configRunSelect").onchange = () => loadConfigRun($("configRunSelect").value);
  ["statisticsMode", "statisticsTask", "statisticsDistribution", "statisticsScale", "statisticsScenario", "statisticsCostAxis"].forEach((id) => $(id).onchange = () => {
    renderStatistics();
    showStatisticsDetails(statisticsUI.protocol, 1);
  });
  $("reloadStatistics").onclick = loadStatistics;
  $("openStatisticsMain").onclick = () => {
    const chart = state.statistics?.modes?.main?.artifacts?.find((a) => a.kind === "png");
    if (chart) renderAssetPreview(chart.path, "statisticsAssetPreview");
  };
  $("runStatisticsSuite").onclick = () => submitStatisticsJob("statistics_suite");
  $("generateStatisticsBaseline").onclick = () => submitStatisticsJob("statistics_llm");
  $("importStatisticsCandidate").onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 100000) throw new Error("候选 JSON 最多 100 KB");
      const record = JSON.parse(await file.text());
      const result = await api("/api/statistics/import-candidate", { method: "POST", body: JSON.stringify({ record }) });
      selectStatisticsCandidate(result);
    } catch (e) { $("statisticsJobLog").textContent = e.message; }
    event.target.value = "";
  };
}

let selectedConfigRun = "";
let configPoller = null;
function configList(id, numeric = false) {
  const values = $(id).value.split(",").map((s) => s.trim());
  if (values.some((v) => !v)) throw new Error("逗号分隔的配置中有空值");
  if (!numeric) return values;
  const numbers = values.map(Number);
  if (numbers.some((n) => !Number.isSafeInteger(n))) throw new Error("seeds 和规模必须填写整数");
  return numbers;
}
async function loadConfigRun(runId = "") {
  try {
    const data = await api(`/api/statistics/config-runs${runId ? '?run_id=' + encodeURIComponent(runId) : ''}`);
    $("configRunSelect").innerHTML = (data.runs || []).map((r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)}</option>`).join("") || '<option value="">暂无记录</option>';
    if (!data.available) { $("configRunEvidence").textContent = "尚无配置化实验记录。"; return; }
    selectedConfigRun = data.run_id;
    $("configRunSelect").value = data.run_id;
    $("rebuildConfigEvidence").disabled = !data.audit.passed || Boolean(configPoller);
    const rows = data.rows;
    $("configRunEvidence").innerHTML = `<p><span class="pill ${data.audit.passed ? 'green' : 'red'}">${data.audit.passed ? '原始产物审计通过' : '原始产物审计失败'}</span> ${esc(data.run_id)}</p>
      <p class="mini-note mono">复现身份 ${esc(data.manifest.reproducibility_key)}</p>
      <p>数据划分检查：${esc(data.manifest.split_audit?.status || '历史记录未检查')} · 重叠 ${data.manifest.split_audit?.overlap_count ?? '—'}</p>
      <details><summary>完整配置、TaskSpec、运行命令和审计</summary><pre class="console">${esc(JSON.stringify({ config: data.manifest.config, task_specs: data.manifest.task_specs, command: data.manifest.command, split_audit: data.manifest.split_audit, reference_artifact_hashes: data.manifest.reference_artifact_hashes, environment: { python: data.manifest.python_version, packages: data.manifest.package_versions }, source_hashes: data.manifest.source_hashes, audit: data.audit }, null, 2))}</pre></details>
      <div class="scroll"><table><thead><tr><th>任务 / 场景</th><th>协议</th><th>执行 / 质量达标</th><th>误差均值 / 95% CI / P95</th><th>通信 bits</th><th>Pareto / 排除原因</th></tr></thead><tbody>${rows.slice(0, 200).map((r) => `<tr><td>${esc(statLabel(r.task))}<small>${esc(statLabel(r.distribution))} / ${r.num_tags}<br>缺失 ${statPercent(r.missing_rate)} / 噪声 ${statPercent(r.noise_rate)} / 异常 ${statPercent(r.extreme_rate)} / ${esc(r.split)}</small></td><td>${esc(statLabel(r.protocol))}</td><td>${statPercent(r.success_rate)} / ${statPercent(r.quality_pass_rate)}<small>${r.runs} seeds / ${r.failed_runs} 失败</small></td><td>${statPercent(r.mean_error)}<small>CI ${statPercent(r.error_ci95_low)} – ${statPercent(r.error_ci95_high)}<br>P95 ${statPercent(r.p95_error)}</small></td><td>${fmtStat(r.mean_communication_bits, 0)}</td><td>${r.is_pareto ? '◇ 前沿' : r.pareto_eligible ? '可比较' : esc(r.pareto_exclusion_reasons.map(statLabel).join(' / '))}<small>${esc(r.failure_reasons.join(' / '))}</small></td></tr>`).join('')}</tbody></table></div>
      <p class="mini-note">共 ${rows.length} 行，页面最多显示 200 行；完整数据见 CSV/JSON。Top-k 误差为 1−F1；其他任务为相对误差。</p>
      <div class="statistics-artifacts">${statAssetButtons(data.artifacts)}</div>`;
    $("configRunEvidence").querySelectorAll("[data-stat-artifact]").forEach((el) => { el.onclick = () => renderAssetPreview(el.dataset.statArtifact, "configAssetPreview"); });
  } catch (e) { $("configRunEvidence").textContent = e.message; }
}
async function pollConfigJob(id) {
  try {
    const { job } = await api(`/api/selfopt/job?id=${encodeURIComponent(id)}`);
    $("configJobLog").textContent = `${job.status} · ${job.progress}% · ${job.elapsed_s}s\n${job.logs.join('\n')}${job.error ? '\n' + job.error : ''}`;
    if (job.status !== "success" && job.status !== "failed") { configPoller = setTimeout(() => pollConfigJob(id), 1500); return; }
    configPoller = null;
    await loadConfigRun(job.result?.run_id || '');
  } catch (e) { $("configJobLog").textContent = e.message; configPoller = null; }
  $("runConfigExperiment").disabled = false;
}
async function submitConfigJob(rebuild) {
  $("runConfigExperiment").disabled = true;
  $("rebuildConfigEvidence").disabled = true;
  try {
    const config = rebuild ? null : { name: "web-reproducible", profiles: configList("configProfiles"), protocols: configList("configProtocols"),
      seeds: configList("configSeeds", true), sizes: configList("configSizes", true), distributions: configList("configDistributions"), split: $("configSplit").value,
      price_min: Number($("configMin").value), price_max: Number($("configMax").value), missing_rate: Number($("configMissing").value),
      noise_rate: Number($("configNoise").value), extreme_rate: Number($("configExtreme").value) };
    const { job } = await api(`/api/statistics/${rebuild ? 'rebuild' : 'run-config'}`, { method: "POST", body: JSON.stringify(rebuild ? { run_id: selectedConfigRun } : { config }) });
    configPoller = true;
    pollConfigJob(job.id);
  } catch (e) { $("configJobLog").textContent = e.message; $("runConfigExperiment").disabled = false; $("rebuildConfigEvidence").disabled = !selectedConfigRun; }
}

async function loadEngineeringPipeline(runId = "") {
  try {
    const data = await api(`/api/statistics/pipelines${runId ? '?run_id=' + encodeURIComponent(runId) : ''}`);
    $("pipelineRunSelect").innerHTML = (data.runs || []).map((r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)}</option>`).join('') || '<option value="">暂无流水线</option>';
    if (!data.available) { $("pipelineEvidence").textContent = '尚无流水线记录。'; return; }
    $("pipelineRunSelect").value = data.run_id;
    const manifest = data.manifest;
    $("pipelineEvidence").innerHTML = `<p><span class="pill ${data.audit.passed ? 'green' : 'red'}">${esc(manifest.status)} · 产物审计${data.audit.passed ? '通过' : '未通过'}</span> ${esc(data.run_id)}</p><p>盲测双次复现：${manifest.replay?.passed ? '通过' : '未通过 / 尚未执行'}</p><div class="scroll"><table><thead><tr><th>阶段</th><th>状态</th><th>原始结果数</th><th>划分审计 / 重叠</th></tr></thead><tbody>${(manifest.stages || []).map((s) => `<tr><td>${esc(s.name)}</td><td>${esc(s.status)}</td><td>${s.run_count ?? '—'}</td><td>${esc(s.split_audit?.status || '等待执行')} / ${s.split_audit?.overlap_count ?? '—'}</td></tr>`).join('')}</tbody></table></div><details><summary>完整流水线配置、种子组、阶段审计与确定性核对</summary><pre class="console">${esc(JSON.stringify({ manifest, audit: data.audit }, null, 2))}</pre></details><details><summary>原始结果、图表与源码快照下载（${data.artifacts.length} 项）</summary><div class="statistics-artifacts">${data.artifacts.map((a) => `<a class="statistics-artifact" href="/api/selfopt/download?path=${encodeURIComponent(a.path)}">${esc(a.name)}</a>`).join('')}</div></details>`;
  } catch (e) { $("pipelineEvidence").textContent = e.message; }
}

async function submitEngineeringPipeline() {
  $("runEngineeringPipeline").disabled = true;
  try {
    const config = { name: 'web-engineering-pipeline', profiles: configList('configProfiles'), protocols: configList('configProtocols'),
      seeds: configList('configSeeds', true), sizes: configList('configSizes', true), distributions: configList('configDistributions'),
      split: 'development', price_min: Number($("configMin").value), price_max: Number($("configMax").value),
      missing_rate: Number($("configMissing").value), noise_rate: Number($("configNoise").value), extreme_rate: Number($("configExtreme").value) };
    const payload = { config };
    if ($('pipelineValidationSeeds').value.trim()) payload.validation_seeds = configList('pipelineValidationSeeds', true);
    if ($('pipelineBlindSeeds').value.trim()) payload.blind_seeds = configList('pipelineBlindSeeds', true);
    const { job } = await api('/api/statistics/run-pipeline', { method: 'POST', body: JSON.stringify(payload) });
    const poll = async () => {
      try {
        const { job: latest } = await api(`/api/selfopt/job?id=${encodeURIComponent(job.id)}`);
        $('pipelineJobLog').textContent = `${latest.status} · ${latest.stage} · ${latest.elapsed_s}s\n${latest.logs.join('\n')}${latest.error ? '\n' + latest.error : ''}`;
        if (!['success', 'failed'].includes(latest.status)) { setTimeout(poll, 1500); return; }
        await loadEngineeringPipeline(latest.result?.run_id || '');
      } catch (e) { $('pipelineJobLog').textContent = e.message; }
      $('runEngineeringPipeline').disabled = false;
    };
    poll();
  } catch (e) { $('pipelineJobLog').textContent = e.message; $('runEngineeringPipeline').disabled = false; }
}
