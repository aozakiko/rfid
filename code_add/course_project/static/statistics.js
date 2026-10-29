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
function statAggregate(rows) {
  const grouped = new Map();
  rows.forEach((row) => {
    if (!grouped.has(row.protocol)) grouped.set(row.protocol, []);
    grouped.get(row.protocol).push(row);
  });
  return [...grouped].map(([protocol, values]) => {
    const runs = values.reduce((n, row) => n + row.runs, 0);
    const weighted = (field) => {
      const valid = values.filter((v) => v[field] !== null && Number.isFinite(v[field]));
      const weight = valid.reduce((n, v) => n + v.runs, 0);
      return weight ? valid.reduce((n, v) => n + v[field] * v.runs, 0) / weight : null;
    };
    const output = { protocol, runs, failures: values.reduce((n, v) => n + v.failed_runs, 0), groups: values.length };
    ["mean_error", "mean_f1", "quality_pass_rate", "mean_communication_bits", "mean_reader_bits", "mean_tag_bits", "mean_slots", "mean_frames", "mean_rounds"].forEach((key) => output[key] = weighted(key));
    output.successRate = runs ? 1 - output.failures / runs : 0;
    output.ci = values.length === 1 ? [values[0].error_ci95_low, values[0].error_ci95_high] : null;
    output.p95 = values.length === 1 ? values[0].p95_error : null;
    output.worst = values.length === 1 ? values[0].worst_error : null;
    return output;
  }).sort((a, b) => (b.successRate - a.successRate) || (a.mean_communication_bits - b.mean_communication_bits));
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
  const selected = statSelectedRows(), rows = statAggregate(selected);
  const manifest = state.statistics.manifest;
  const cases = new Set(selected.map((r) => `${r.distribution}:${r.num_tags}:${statScenario(r)}`));
  const failed = rows.reduce((n, r) => n + r.failures, 0);
  $("statisticsMetrics").innerHTML = [
    ["正式实验 seeds", manifest.seeds.length, "tone-teal"],
    ["当前条件 / 协议", `${cases.size} / ${rows.length}`, "tone-green"],
    ["当前逐 seed 结果", rows.reduce((n, r) => n + r.runs, 0), "tone-violet"],
    ["失败 / 待实测", failed, "tone-amber"],
  ].map(([label, value, toneName]) => `<div class="metric ${toneName}"><span>${label}</span><strong>${esc(value)}</strong></div>`).join("");
  $("statisticsFilterNote").textContent = cases.size === 1
    ? "单场景同 seed 配对比较；置信区间来自独立 seed。各任务行共享一次采集费用，不能跨任务重复求和。"
    : "当前为跨场景描述性平均；选择单一分布、规模和扰动条件后显示独立 seed 的 95% CI。";
  drawStatisticsScatter(rows);
  renderStatisticsTable(rows);
  renderStatisticsInsights(selected);
  renderStatisticsArtifacts();
}
function drawStatisticsScatter(rows) {
  const metric = $("statisticsCostAxis").value;
  const valid = rows.filter((r) => r.successRate === 1 && r.mean_error !== null && r[metric] > 0);
  const front = valid.filter((r) => !valid.some((o) => o !== r && o[metric] <= r[metric] && o.mean_error <= r.mean_error && (o[metric] < r[metric] || o.mean_error < r.mean_error)));
  const minX = Math.max(.5, Math.min(...valid.map((r) => r[metric]), 1e12) * .7);
  const maxX = Math.max(minX * 2, ...valid.map((r) => r[metric] * 1.3));
  const maxY = Math.max(.01, ...valid.map((r) => r.mean_error * 1.2));
  const px = (v) => 70 + (Math.log10(v) - Math.log10(minX)) / (Math.log10(maxX) - Math.log10(minX)) * 605;
  const py = (v) => 278 - v / maxY * 250;
  const ticks = [minX, Math.sqrt(minX * maxX), maxX];
  const yTicks = [0, maxY / 2, maxY];
  const label = $("statisticsCostAxis").selectedOptions[0].textContent;
  const coordinates = [...front].sort((a, b) => a[metric] - b[metric]).map((r) => `${px(r[metric])},${py(r.mean_error)}`).join(" ");
  $("statisticsScatter").innerHTML = valid.length ? `<svg viewBox="0 0 720 335" role="img" aria-label="${esc(label)}与误差 Pareto 图">
    ${yTicks.map((v) => `<line x1="70" x2="675" y1="${py(v)}" y2="${py(v)}" stroke="#e5ecf4"/><text x="60" y="${py(v) + 4}" text-anchor="end">${esc(statPercent(v))}</text>`).join("")}
    ${ticks.map((v) => `<text x="${px(v)}" y="304" text-anchor="middle">${esc(fmtStat(v, 0))}</text>`).join("")}
    <polyline points="${coordinates}" fill="none" stroke="#172f4b" stroke-dasharray="5 5" opacity=".55"/>
    ${valid.map((r) => {
      const i = rows.indexOf(r), isFront = front.includes(r);
      return `<circle cx="${px(r[metric])}" cy="${py(r.mean_error)}" r="${isFront ? 8 : 6}" fill="${statisticsColors[i % statisticsColors.length]}" stroke="${isFront ? '#172f4b' : 'white'}" stroke-width="1.5" tabindex="0" role="button" data-stat-protocol="${esc(r.protocol)}" aria-label="查看 ${esc(statLabel(r.protocol))} 详情"><title>${esc(statLabel(r.protocol))} | ${esc(label)} ${esc(fmtStat(r[metric]))} | 误差 ${esc(statPercent(r.mean_error))}${isFront ? ' | Pareto 前沿' : ''}</title></circle>`;
    }).join("")}
    <text x="365" y="330" text-anchor="middle">${esc(label)}（对数轴，越低越好）</text>
    <text x="16" y="160" transform="rotate(-90 16 160)" text-anchor="middle">${$("statisticsTask").value === "top_k" ? "1−F1" : "相对误差"}（越低越好）</text>
  </svg>` : '<div class="mini-note">没有可绘制的成功结果；失败原因可在下方查看。</div>';
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
    return `<tr><td><button class="statistics-protocol-link" type="button" data-stat-protocol="${esc(r.protocol)}">${esc(statLabel(r.protocol))}</button><small class="statistics-ci">${esc(r.protocol)}</small></td><td>${esc(statPercent(r.successRate))}<small class="statistics-ci">${r.runs} 次执行<br>精度达标 ${statPercent(r.quality_pass_rate)}</small></td><td>${quality}${r.p95 !== null && r.p95 !== undefined ? `<small class="statistics-ci">误差 P95 ${statPercent(r.p95)}<br>最坏 ${statPercent(r.worst)}</small>` : ''}</td><td>${r.successRate ? esc(fmtStat(r.mean_communication_bits, 0)) : '—'}</td><td>${r.successRate ? `${fmtStat(r.mean_reader_bits, 0)} / ${fmtStat(r.mean_tag_bits, 0)}` : '—'}</td><td>${r.successRate ? `${fmtStat(r.mean_slots, 0)} / ${fmtStat(r.mean_frames)} / ${fmtStat(r.mean_rounds)}` : '—'}</td><td>${r.failures}</td></tr>`;
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
