from __future__ import annotations


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RFID 协议自优化云边端平台</title>
  <style>
    :root {
      --bg: #f3f6f8;
      --surface: #ffffff;
      --ink: #17202a;
      --muted: #64748b;
      --line: #d7e0ea;
      --nav: #102033;
      --blue: #2563eb;
      --teal: #0f766e;
      --green: #2f855a;
      --amber: #b45309;
      --red: #b42318;
      --violet: #6d28d9;
      --shadow: 0 14px 32px rgba(15, 23, 42, .08);
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Arial, "Microsoft YaHei", sans-serif; color: var(--ink); background: var(--bg); }
    button, input, select, textarea { font: inherit; }
    .shell { display: grid; grid-template-columns: 286px minmax(0, 1fr); min-height: 100vh; }
    aside { background: var(--nav); color: #eef6ff; padding: 22px 16px; position: sticky; top: 0; height: 100vh; overflow: auto; }
    .brand { padding: 6px 8px 20px; border-bottom: 1px solid rgba(255,255,255,.16); margin-bottom: 12px; }
    .brand strong { display: block; font-size: 19px; line-height: 1.35; letter-spacing: 0; }
    .brand span { display: block; color: #bdd0e2; font-size: 12px; line-height: 1.55; margin-top: 6px; }
    nav button {
      width: 100%; min-height: 44px; border: 0; border-radius: 8px; padding: 0 12px; margin: 5px 0;
      background: transparent; color: #dce9f8; display: flex; align-items: center; gap: 10px; cursor: pointer; text-align: left;
    }
    nav button.active, nav button:hover { background: #203148; color: #fff; }
    nav svg { width: 18px; height: 18px; flex: 0 0 auto; }
    main { padding: 26px 32px 48px; min-width: 0; }
    .topbar { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
    h1 { margin: 0 0 8px; font-size: 28px; line-height: 1.2; letter-spacing: 0; }
    h2 { margin: 0; font-size: 18px; letter-spacing: 0; }
    h3 { margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }
    .muted { color: var(--muted); line-height: 1.55; font-size: 13px; }
    .status { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); min-width: 230px; padding: 12px 14px; }
    .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--green); margin-right: 8px; }
    .dot.busy { background: var(--amber); } .dot.error { background: var(--red); }
    .view { display: none; } .view.active { display: block; }
    .grid-2 { display: grid; grid-template-columns: minmax(390px, .95fr) minmax(0, 1.45fr); gap: 16px; align-items: start; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
    .grid-4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); margin-bottom: 16px; overflow: hidden; }
    .pad { padding: 16px; }
    .panel-head { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .metric { background: var(--surface); border: 1px solid var(--line); border-left: 5px solid var(--blue); border-radius: 8px; padding: 13px 14px; min-height: 86px; box-shadow: var(--shadow); }
    .metric span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .metric strong { display: block; font-size: 24px; line-height: 1.15; word-break: break-word; }
    .tone-teal { border-left-color: var(--teal); } .tone-green { border-left-color: var(--green); } .tone-amber { border-left-color: var(--amber); } .tone-red { border-left-color: var(--red); } .tone-violet { border-left-color: var(--violet); }
    label { display: block; color: #475467; font-size: 12px; margin-bottom: 6px; }
    input, select, textarea { width: 100%; border: 1px solid #cbd5e1; border-radius: 8px; background: #fff; color: var(--ink); padding: 9px 10px; min-height: 40px; }
    textarea { min-height: 138px; resize: vertical; line-height: 1.55; }
    input[type="checkbox"] { width: 16px; min-height: 16px; padding: 0; }
    input[type="range"] { padding: 0; min-height: 24px; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; align-items: center; }
    .btn { min-height: 40px; border: 1px solid transparent; border-radius: 8px; padding: 8px 13px; background: #172033; color: #fff; cursor: pointer; display: inline-flex; gap: 8px; align-items: center; }
    .btn.blue { background: var(--blue); } .btn.teal { background: var(--teal); } .btn.secondary { background: #fff; color: var(--ink); border-color: var(--line); }
    .btn:disabled { opacity: .58; cursor: progress; }
    .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 9px; border-radius: 999px; background: #eef3f8; color: #334155; font-size: 12px; white-space: nowrap; }
    .pill.blue { background: #dbeafe; color: #1d4ed8; } .pill.green { background: #dcfce7; color: #166534; } .pill.amber { background: #fef3c7; color: #92400e; } .pill.red { background: #fee2e2; color: #991b1b; }
    .scenario-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .scenario { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 12px; cursor: pointer; text-align: left; color: var(--ink); min-height: 108px; }
    .scenario.active { border-color: var(--blue); box-shadow: inset 0 0 0 1px var(--blue); }
    .scenario strong { display: block; margin-bottom: 6px; }
    .task-grid, .priority-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .check-card { display: flex; align-items: flex-start; gap: 8px; border: 1px solid var(--line); border-radius: 8px; padding: 9px; background: #fbfdff; min-height: 74px; }
    .check-card b { display: block; font-size: 13px; margin-bottom: 3px; }
    .range-row { display: grid; grid-template-columns: 108px minmax(0, 1fr) 66px; gap: 10px; align-items: center; padding: 8px 0; border-bottom: 1px solid #eef2f6; }
    .range-row:last-child { border-bottom: 0; }
    .range-row span { font-size: 12px; color: var(--muted); }
    .split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
    .scroll { overflow: auto; max-height: 430px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }
    th { background: #f8fafc; color: #344054; font-weight: 700; }
    tbody tr:hover { background: #f8fbff; }
    pre { white-space: pre-wrap; word-break: break-word; background: #111827; color: #f8fafc; border-radius: 8px; padding: 13px; font-size: 12px; line-height: 1.55; max-height: 450px; overflow: auto; }
    .flow { display: grid; grid-template-columns: repeat(6, minmax(126px, 1fr)); gap: 10px; }
    .stage { border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-height: 112px; background: #fff; position: relative; }
    .stage b { display: block; margin: 6px 0 8px; font-size: 14px; }
    .stage small { color: var(--muted); line-height: 1.45; }
    .stage::before { content: attr(data-step); display: inline-flex; width: 24px; height: 24px; border-radius: 50%; align-items: center; justify-content: center; background: #e8f1ff; color: var(--blue); font-weight: 700; font-size: 12px; }
    .arch-map { display: grid; grid-template-columns: 1fr 44px 1fr 44px 1fr 44px 1fr; gap: 10px; align-items: stretch; }
    .arch-col { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 14px; min-height: 230px; }
    .arch-col h3 { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .arch-item { border: 1px solid #e5edf5; border-left: 4px solid var(--teal); border-radius: 8px; padding: 9px; margin-top: 9px; background: #fbfdff; }
    .arch-item.edge { border-left-color: var(--amber); }
    .arch-item.cloud { border-left-color: var(--blue); }
    .arch-item.feedback { border-left-color: var(--violet); }
    .arch-arrow { display: flex; align-items: center; justify-content: center; color: #667085; font-size: 26px; min-height: 230px; }
    .sequence { display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 10px; }
    .seq-step { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fff; min-height: 132px; position: relative; }
    .seq-step::before { content: attr(data-step); display: inline-flex; width: 24px; height: 24px; border-radius: 50%; align-items: center; justify-content: center; background: #e8f1ff; color: var(--blue); font-weight: 700; font-size: 12px; margin-bottom: 8px; }
    .seq-step b { display: block; margin-bottom: 7px; }
    .kpi-strip { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; }
    .kpi { border: 1px solid var(--line); border-radius: 8px; padding: 11px; background: #fbfdff; min-height: 82px; }
    .kpi span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 7px; }
    .kpi b { font-size: 18px; word-break: break-word; }
    .topology { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; align-items: stretch; }
    .lane { border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #fff; min-height: 300px; }
    .lane h3 { display: flex; justify-content: space-between; gap: 10px; }
    .node { border: 1px solid var(--line); border-left: 4px solid var(--teal); border-radius: 8px; padding: 10px; margin-top: 10px; background: #fbfdff; }
    .node.edge { border-left-color: var(--amber); } .node.cloud { border-left-color: var(--blue); }
    .bar-chart { display: grid; gap: 9px; }
    .bar-row { display: grid; grid-template-columns: 116px minmax(0, 1fr) 46px; gap: 9px; align-items: center; font-size: 12px; }
    .bar { height: 11px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }
    .bar span { display: block; height: 100%; background: var(--teal); }
    .stack { display: flex; height: 22px; border-radius: 999px; overflow: hidden; background: #e5e7eb; }
    .stack span { display: block; height: 100%; }
    .chart-pair { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 14px; align-items: center; }
    .radar svg { width: 240px; height: 240px; display: block; margin: 0 auto; }
    .tag-list { display: flex; flex-wrap: wrap; gap: 7px; }
    .run-list button { width: 100%; border: 0; border-bottom: 1px solid var(--line); padding: 12px 14px; background: #fff; color: var(--ink); text-align: left; cursor: pointer; }
    .run-list button:hover { background: #f3f7fb; }
    .check-row { display: flex; align-items: center; gap: 8px; color: #344054; font-size: 13px; padding-top: 20px; }
    @media (max-width: 1180px) {
      .shell { grid-template-columns: 1fr; } aside { position: static; height: auto; } main { padding: 18px; }
      .grid-2, .split, .topology, .chart-pair, .arch-map, .sequence { grid-template-columns: 1fr; }
      .arch-arrow { min-height: 30px; transform: rotate(90deg); }
      .grid-3, .grid-4, .flow, .scenario-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
    }
    @media (max-width: 720px) {
      .grid-3, .grid-4, .flow, .form-grid, .scenario-grid, .task-grid, .priority-grid { grid-template-columns: 1fr; }
      .topbar { flex-direction: column; }
      .status { width: 100%; }
      h1 { font-size: 22px; }
      .range-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <strong>RFID 协议自优化平台</strong>
        <span>GRDL · AHP · LLM Prompt · 云边端协同 · 流处理/批处理 · SQLite 审计</span>
      </div>
      <nav id="nav"></nav>
    </aside>
    <main>
      <header class="topbar">
        <div>
          <h1>面向云边端部署的 RFID 协议自优化平台</h1>
          <div class="muted">从自然语言需求出发，完成建模、匹配、Prompt、验证、部署、运行和反馈闭环。</div>
        </div>
        <div class="status"><span id="statusDot" class="dot"></span><span id="statusText">就绪</span></div>
      </header>

      <section id="view-overview" class="view active">
        <div class="grid-4" id="overviewMetrics"></div>
        <section class="panel pad">
          <h2>自优化闭环</h2>
          <div class="flow" id="closedLoop"></div>
        </section>
        <section class="panel pad">
          <div class="panel-head" style="padding:0 0 12px;border-bottom:0">
            <h2>云边端网络闭环架构</h2>
            <span class="pill blue">端侧采集 → 边缘推理 → 云端分析 → 策略回写</span>
          </div>
          <div class="arch-map" id="networkLoop"></div>
        </section>
        <div class="grid-2">
          <section class="panel">
            <div class="panel-head"><h2>最近设计案例</h2><span class="pill" id="caseCount">0</span></div>
            <div class="scroll"><table><thead><tr><th>时间</th><th>场景</th><th>推荐协议</th><th>得分</th></tr></thead><tbody id="caseRows"></tbody></table></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>最近运行</h2><span class="pill" id="runCount">0</span></div>
            <div class="run-list" id="runList"></div>
          </section>
        </div>
      </section>

      <section id="view-design" class="view">
        <div class="grid-2">
          <div>
            <section class="panel pad">
              <h2>需求建模</h2>
              <div class="scenario-grid" id="scenarioGrid"></div>
              <div style="margin-top:12px">
                <label>自然语言需求</label>
                <textarea id="naturalLanguage"></textarea>
              </div>
              <div class="form-grid" style="margin-top:12px">
                <div><label>协议场景目录</label><select id="designScene"></select></div>
                <div><label>部署规模</label><div class="form-grid"><input id="designDevices" type="number" min="1" max="48"><input id="designGateways" type="number" min="1" max="12"></div></div>
              </div>
            </section>
            <section class="panel pad">
              <h2>任务与优先级</h2>
              <h3 style="margin-top:12px">任务算子</h3>
              <div class="task-grid" id="taskGrid"></div>
              <h3 style="margin-top:16px">优化目标</h3>
              <div class="priority-grid" id="priorityGrid"></div>
            </section>
            <section class="panel pad">
              <h2>约束配置</h2>
              <div id="constraintBox"></div>
              <div class="actions">
                <button class="btn blue" id="analyzeDesign" type="button">解析需求并生成 Prompt</button>
                <button class="btn secondary" id="syncRun" type="button">同步到运行配置</button>
              </div>
            </section>
          </div>
          <div>
            <div class="grid-4" id="designMetrics"></div>
            <section class="panel pad">
              <h2>自然语言解析</h2>
              <div class="tag-list" id="parseSummary"></div>
            </section>
            <section class="panel pad">
              <h2>GRDL 形式化模型</h2>
              <pre id="grdlBlock">等待生成设计方案</pre>
            </section>
            <section class="panel pad">
              <div class="panel-head" style="padding:0 0 12px;border-bottom:0"><h2>LLM 优化 Prompt</h2><button class="btn secondary" id="copyPrompt" type="button">复制</button></div>
              <pre id="promptBlock">等待自然语言需求解析</pre>
            </section>
          </div>
        </div>
      </section>

      <section id="view-analysis" class="view">
        <section class="panel pad">
          <div class="panel-head" style="padding:0 0 12px;border-bottom:0">
            <h2>方案分析总览</h2>
            <span class="pill" id="analysisCaseLabel">等待方案</span>
          </div>
          <div class="kpi-strip" id="analysisSummary"></div>
        </section>
        <div class="split">
          <section class="panel pad">
            <h2>AHP 协议匹配</h2>
            <div class="chart-pair" style="margin-top:14px">
              <div class="radar" id="radarChart"></div>
              <div>
                <h3>候选协议得分</h3>
                <div class="bar-chart" id="protocolBars"></div>
              </div>
            </div>
          </section>
          <section class="panel pad">
            <h2>云边端任务分配</h2>
            <div id="allocationStack" style="margin-top:14px"></div>
            <h3 style="margin-top:18px">时延预算</h3>
            <div class="bar-chart" id="latencyBars"></div>
            <h3 style="margin-top:18px">流处理 / 一般处理</h3>
            <div id="processingModes" style="margin-top:10px"></div>
            <h3 style="margin-top:18px">风险画像</h3>
            <div class="tag-list" id="riskCards"></div>
          </section>
        </div>
        <section class="panel">
          <div class="panel-head"><h2>协议匹配明细</h2><span class="pill" id="recommendLabel">待分析</span></div>
          <div class="scroll"><table><thead><tr><th>协议</th><th>家族</th><th>综合得分</th><th>适配原因</th><th>风险</th></tr></thead><tbody id="matchRows"></tbody></table></div>
        </section>
      </section>

      <section id="view-optimize" class="view">
        <div class="split">
          <section class="panel">
            <div class="panel-head"><h2>优化编排</h2></div>
            <div class="scroll"><table><thead><tr><th>策略</th><th>层级</th><th>目标</th><th>方法</th><th>状态</th></tr></thead><tbody id="playbookRows"></tbody></table></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>三重验证矩阵</h2></div>
            <div class="scroll"><table><thead><tr><th>验证门</th><th>检查项</th><th>阈值</th><th>状态</th></tr></thead><tbody id="validationRows"></tbody></table></div>
          </section>
        </div>
        <section class="panel">
          <div class="panel-head"><h2>协议代码画像</h2></div>
          <div class="scroll"><table><thead><tr><th>协议</th><th>路径</th><th>代码行</th><th>函数</th><th>循环</th><th>帧模型</th><th>验证数据</th></tr></thead><tbody id="profileRows"></tbody></table></div>
        </section>
        <section class="panel pad">
          <h2>技术覆盖</h2>
          <div class="grid-3" id="techCoverage" style="margin-top:12px"></div>
        </section>
      </section>

      <section id="view-deploy" class="view">
        <section class="panel pad">
          <div class="panel-head" style="padding:0 0 12px;border-bottom:0">
            <h2>网络消息时序</h2>
            <span class="pill green">签名校验 · 摘要上传 · 流批处理 · 反馈调参</span>
          </div>
          <div class="sequence" id="messageSequence"></div>
        </section>
        <section class="panel pad">
          <h2>云边端部署清单</h2>
          <div class="topology" id="deployTopology"></div>
        </section>
        <section class="panel pad">
          <h2>部署 Manifest</h2>
          <pre id="manifestBlock">等待生成部署清单</pre>
        </section>
      </section>

      <section id="view-run" class="view">
        <section class="panel pad">
          <div class="panel-head" style="padding:0 0 12px;border-bottom:0">
            <h2>本次运行闭环链路</h2>
            <span class="pill" id="runFlowLabel">等待运行</span>
          </div>
          <div class="arch-map" id="runFlow"></div>
        </section>
        <div class="grid-2">
          <section class="panel pad">
            <h2>闭环运行验证</h2>
            <form id="runForm">
              <div class="form-grid">
                <div><label>场景</label><select id="scene"></select></div>
                <div><label>边缘协议</label><select id="protocol"></select></div>
                <div><label>端侧读写器</label><input id="devices" type="number" min="1" max="48"></div>
                <div><label>边缘网关</label><input id="gateways" type="number" min="1" max="12"></div>
                <div><label>采集批次</label><input id="batches" type="number" min="1" max="20"></div>
                <div><label>每批标签数</label><input id="numTags" type="number" min="20" max="3000"></div>
                <div><label>价格下限</label><input id="priceMin" type="number" min="1"></div>
                <div><label>价格上限</label><input id="priceMax" type="number" min="2"></div>
                <div><label>流窗口</label><input id="streamWindow" type="number" min="1" max="50"></div>
                <div><label>签名密钥</label><input id="sharedSecret" type="password" value="rfid-course-demo-secret"></div>
                <div class="check-row"><input id="includeSecurity" type="checkbox" checked><span>安全测试</span></div>
                <div class="check-row"><input id="includeBenchmark" type="checkbox" checked><span>可扩展性基准</span></div>
              </div>
              <div class="actions">
                <button class="btn blue" id="startRun" type="submit">启动云边端流程</button>
                <button class="btn secondary" id="refreshState" type="button">刷新</button>
              </div>
            </form>
          </section>
          <div>
            <div class="grid-4" id="runMetrics"></div>
            <section class="panel">
              <div class="panel-head"><h2>流处理窗口</h2><span class="pill" id="currentRunLabel">未选择</span></div>
              <div class="scroll"><table><thead><tr><th>批次</th><th>网关</th><th>终端</th><th>识别价格</th><th>命中率</th><th>耗时</th><th>告警</th></tr></thead><tbody id="streamRows"></tbody></table></div>
            </section>
          </div>
        </div>
        <div class="split">
          <section class="panel">
            <div class="panel-head"><h2>边缘网关汇总</h2></div>
            <div class="scroll"><table><thead><tr><th>网关</th><th>窗口数</th><th>平均协议耗时</th><th>平均帧长</th><th>命中率</th></tr></thead><tbody id="gatewayRows"></tbody></table></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>云端批处理结果</h2></div>
            <div class="scroll"><table><thead><tr><th>价格</th><th>热度</th><th>次数</th></tr></thead><tbody id="hotRows"></tbody></table></div>
          </section>
        </div>
      </section>

      <section id="view-audit" class="view">
        <div class="grid-4" id="dbMetrics"></div>
        <section class="panel pad"><h2>数据库</h2><pre id="dbPath"></pre></section>
        <div class="split">
          <section class="panel">
            <div class="panel-head"><h2>系统事件</h2></div>
            <div class="scroll"><table><thead><tr><th>ID</th><th>时间</th><th>类型</th><th>对象</th><th>消息</th></tr></thead><tbody id="eventRows"></tbody></table></div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>AI 与设计记录</h2></div>
            <div class="scroll"><table><thead><tr><th>ID</th><th>类型</th><th>时间</th><th>内容</th><th>状态/得分</th></tr></thead><tbody id="auditRows"></tbody></table></div>
          </section>
        </div>
      </section>
    </main>
  </div>

  <script>
    const navItems = [
      ["overview", "驾驶舱", "M4 13h6V5H4z M14 19h6V5h-6z M4 19h6v-4H4z"],
      ["design", "需求建模", "M4 5h16 M4 12h16 M4 19h10"],
      ["analysis", "分析图表", "M4 19h16 M7 16V8 M12 16V5 M17 16v-4"],
      ["optimize", "优化验证", "M12 3l2.5 5 5.5.8-4 3.8 1 5.4-5-2.7-5 2.7 1-5.4-4-3.8 5.5-.8z"],
      ["deploy", "云边端部署", "M12 3v6 M6 21v-6h12v6 M6 15l6-6 6 6"],
      ["run", "运行闭环", "M5 12h14 M12 5l7 7-7 7"],
      ["audit", "数据库审计", "M4 6c0-2 16-2 16 0v12c0 2-16 2-16 0z M4 6v6c0 2 16 2 16 0 M4 12v6"],
    ];
    const state = {
      catalog: null,
      runs: [],
      db: null,
      topology: null,
      currentRun: null,
      currentCase: null,
      selectedPreset: "fresh_cold_chain",
      selectedTasks: [],
      selectedPriorities: [],
    };
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    const icon = (path) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"></path></svg>`;
    const empty = (cols, text="暂无数据") => `<tr><td colspan="${cols}">${esc(text)}</td></tr>`;
    function setStatus(text, mode="ready") {
      $("statusText").textContent = text;
      $("statusDot").className = "dot" + (mode === "busy" ? " busy" : mode === "error" ? " error" : "");
    }
    async function api(path, options={}) {
      const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "请求失败");
      return data;
    }
    function mountNav() {
      $("nav").innerHTML = navItems.map(([id,label,path],i) => `<button class="${i===0 ? "active" : ""}" data-view="${id}">${icon(path)}<span>${label}</span></button>`).join("");
      document.querySelectorAll("nav button").forEach(btn => {
        btn.onclick = () => switchView(btn.dataset.view);
      });
    }
    function switchView(id) {
      document.querySelectorAll("nav button").forEach(x => x.classList.toggle("active", x.dataset.view === id));
      document.querySelectorAll(".view").forEach(x => x.classList.toggle("active", x.id === "view-" + id));
    }
    function fillSelect(el, values, selected) {
      el.innerHTML = values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
      if (selected && values.includes(selected)) el.value = selected;
    }
    function tone(index) {
      return ["tone-teal","tone-green","tone-amber","tone-violet","tone-red",""][index % 6];
    }
    function renderOverview() {
      const counts = state.db?.counts || {};
      const latestRun = state.runs[0];
      const latestCase = state.db?.design_cases?.[0];
      const metrics = [
        ["设计案例", counts.design_cases || 0, "tone-teal"],
        ["运行闭环", counts.runs || 0, "tone-green"],
        ["流窗口", counts.stream_snapshots || 0, "tone-amber"],
        ["部署清单", counts.deployment_manifests || 0, "tone-violet"],
      ];
      $("overviewMetrics").innerHTML = metrics.map(([k,v,t]) => `<div class="metric ${t}"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("");
      const stages = [
        ["自然语言需求", latestCase ? latestCase.title : "等待输入"],
        ["GRDL 建模", "集合/算子/约束"],
        ["AHP 匹配", latestCase ? latestCase.recommended_protocol : "协议画像库"],
        ["LLM Prompt", "交叉/变异/修复"],
        ["三重验证", "功能/物理/隔离"],
        ["云边端反馈", latestRun ? `运行 #${latestRun.id}` : "待运行"],
      ];
      $("closedLoop").innerHTML = stages.map((s,i) => `<div class="stage" data-step="${i+1}"><b>${esc(s[0])}</b><small>${esc(s[1])}</small></div>`).join("");
      renderCases();
      renderRuns();
      renderNetworkLoop(state.currentCase, state.currentRun);
    }
    function renderNetworkLoop(caseData, runData) {
      const manifest = caseData?.deployment || null;
      const report = runData?.result?.batch_report || {};
      const deviceCount = manifest?.devices?.length || runData?.devices || Number($("devices")?.value || 4);
      const gatewayCount = manifest?.gateways?.length || runData?.gateways || Number($("gateways")?.value || 2);
      const protocol = caseData?.summary?.recommended_protocol || runData?.protocol || state.catalog?.defaults?.protocol || "-";
      const windowCount = report.total_windows || runData?.total_windows || 0;
      const hitRate = report.average_hit_rate ?? runData?.average_hit_rate ?? "-";
      $("networkLoop").innerHTML = `
        <div class="arch-col">
          <h3>端侧 <span class="pill">${esc(deviceCount)} 个读写器</span></h3>
          <div class="arch-item"><b>RFID/温湿度采集</b><div class="muted">标签批次、热点价格、环境数据</div></div>
          <div class="arch-item"><b>端侧签名</b><div class="muted">scan payload + HMAC-SHA256</div></div>
        </div>
        <div class="arch-arrow">→</div>
        <div class="arch-col">
          <h3>边缘 <span class="pill amber">${esc(gatewayCount)} 个网关</span></h3>
          <div class="arch-item edge"><b>签名校验</b><div class="muted">拒绝篡改、错密钥、缺失签名</div></div>
          <div class="arch-item edge"><b>协议推理</b><div class="muted">主协议 ${esc(protocol)}，异常即时告警</div></div>
        </div>
        <div class="arch-arrow">→</div>
        <div class="arch-col">
          <h3>云端 <span class="pill blue">${esc(windowCount)} 个窗口</span></h3>
          <div class="arch-item cloud"><b>流处理</b><div class="muted">滑动窗口、近期热点、命中率、耗时</div></div>
          <div class="arch-item cloud"><b>一般处理</b><div class="muted">批报表、网关汇总、SQLite 审计</div></div>
        </div>
        <div class="arch-arrow">↺</div>
        <div class="arch-col">
          <h3>反馈 <span class="pill green">自优化</span></h3>
          <div class="arch-item feedback"><b>AHP 重排</b><div class="muted">根据 ${esc(hitRate)} 命中率、时延、告警更新画像</div></div>
          <div class="arch-item feedback"><b>LLM Prompt</b><div class="muted">生成交叉/变异/修复任务并回写边缘策略</div></div>
        </div>`;
    }
    function renderCases() {
      const rows = state.db?.design_cases || [];
      $("caseCount").textContent = rows.length;
      $("caseRows").innerHTML = rows.length ? rows.map(c => `<tr><td>${esc(c.created_at)}</td><td>${esc(c.title)}</td><td>${esc(c.recommended_protocol)}</td><td>${esc(c.recommended_score)}</td></tr>`).join("") : empty(4);
    }
    function renderRuns() {
      $("runCount").textContent = state.runs.length;
      $("runList").innerHTML = state.runs.length ? state.runs.map(r => `<button data-run="${r.id}"><strong>#${r.id} ${esc(r.scene)} / ${esc(r.protocol)}</strong><div class="muted">${esc(r.created_at)} · ${r.devices}端 · ${r.gateways}边 · 命中率 ${r.average_hit_rate}</div></button>`).join("") : `<div class="muted" style="padding:14px">暂无运行记录</div>`;
      document.querySelectorAll("[data-run]").forEach(btn => btn.onclick = () => loadRun(Number(btn.dataset.run)));
    }
    function setPreset(key, overwriteText=true) {
      state.selectedPreset = key;
      const preset = state.catalog.scenario_presets[key];
      state.selectedTasks = Array.from(preset.task_ids || []);
      state.selectedPriorities = Array.from(preset.priorities || []);
      if (overwriteText) $("naturalLanguage").value = preset.requirement;
      $("designDevices").value = $("devices").value || state.catalog.defaults.devices;
      $("designGateways").value = $("gateways").value || state.catalog.defaults.gateways;
      setConstraintValues(preset.constraints);
      renderScenarios();
      renderTasks();
      renderPriorities();
    }
    function renderScenarios() {
      const presets = state.catalog.scenario_presets || {};
      $("scenarioGrid").innerHTML = Object.entries(presets).map(([key,item]) => `<button class="scenario ${key===state.selectedPreset ? "active" : ""}" data-preset="${key}"><strong>${esc(item.title)}</strong><div class="muted">${esc(item.domain)} · ${esc((item.task_ids || []).length)} 项任务</div><div class="muted">${esc(item.requirement)}</div></button>`).join("");
      document.querySelectorAll("[data-preset]").forEach(btn => btn.onclick = () => setPreset(btn.dataset.preset, true));
    }
    function renderTasks() {
      const tasks = state.catalog.task_library || {};
      $("taskGrid").innerHTML = Object.entries(tasks).map(([key,item]) => `
        <label class="check-card"><input type="checkbox" data-task="${esc(key)}" ${state.selectedTasks.includes(key) ? "checked" : ""}>
          <span><b>${esc(item.title)}</b><span class="muted">${esc(item.layer)} / ${esc(item.mode)} · ${esc(item.operator)}</span></span>
        </label>`).join("");
      document.querySelectorAll("[data-task]").forEach(box => {
        box.onchange = () => {
          const key = box.dataset.task;
          state.selectedTasks = box.checked ? Array.from(new Set([...state.selectedTasks, key])) : state.selectedTasks.filter(x => x !== key);
        };
      });
    }
    function renderPriorities() {
      const priorities = state.catalog.optimization_priorities || {};
      $("priorityGrid").innerHTML = Object.entries(priorities).map(([key,item]) => `
        <label class="check-card"><input type="checkbox" data-priority="${esc(key)}" ${state.selectedPriorities.includes(key) ? "checked" : ""}>
          <span><b>${esc(item.title)}</b><span class="muted">${esc(item.description)}</span></span>
        </label>`).join("");
      document.querySelectorAll("[data-priority]").forEach(box => {
        box.onchange = () => {
          const key = box.dataset.priority;
          state.selectedPriorities = box.checked ? Array.from(new Set([...state.selectedPriorities, key])) : state.selectedPriorities.filter(x => x !== key);
        };
      });
    }
    const constraintDefs = [
      ["latency_ms", "时延 SLA", 50, 5000, 10, "ms"],
      ["accuracy", "准确率", 0.5, 0.999, 0.001, ""],
      ["loss_rate", "漏读/丢失", 0, 0.8, 0.001, ""],
      ["mobility", "移动强度", 0, 1, 0.01, ""],
      ["interference", "环境干扰", 0, 1, 0.01, ""],
      ["energy_budget", "能耗压力", 0, 1, 0.01, ""],
    ];
    function renderConstraintBox() {
      $("constraintBox").innerHTML = constraintDefs.map(([key,label,min,max,step,unit]) => `
        <div class="range-row">
          <span>${esc(label)}</span>
          <input id="range_${key}" type="range" min="${min}" max="${max}" step="${step}">
          <input id="value_${key}" type="number" min="${min}" max="${max}" step="${step}">
        </div>`).join("");
      constraintDefs.forEach(([key]) => {
        const range = $("range_" + key);
        const value = $("value_" + key);
        range.oninput = () => { value.value = range.value; };
        value.oninput = () => { range.value = value.value; };
      });
    }
    function setConstraintValues(c) {
      constraintDefs.forEach(([key]) => {
        if ($("range_" + key)) $("range_" + key).value = c[key];
        if ($("value_" + key)) $("value_" + key).value = c[key];
      });
    }
    function constraintPayload() {
      const data = {};
      constraintDefs.forEach(([key]) => data[key] = Number($("value_" + key).value));
      return data;
    }
    function renderDesignCase(c) {
      state.currentCase = c;
      const summary = c.summary || {};
      $("designMetrics").innerHTML = [
        ["推荐协议", summary.recommended_protocol || "-", "tone-teal"],
        ["匹配得分", summary.recommended_score || 0, "tone-green"],
        ["任务数", c.requirement.task_ids.length, "tone-amber"],
        ["Prompt 字数", c.llm_prompt.length, "tone-violet"],
      ].map(([k,v,t]) => `<div class="metric ${t}"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("");
      const parsed = c.requirement.natural_language_parse || {};
      const keywords = Object.values(parsed.matched_keywords || {}).flat().slice(0, 10);
      $("parseSummary").innerHTML = [
        `<span class="pill blue">场景 ${esc(c.requirement.title)}</span>`,
        `<span class="pill green">置信度 ${esc(parsed.confidence || "-")}</span>`,
        ...c.requirement.priorities.map(p => `<span class="pill">${esc(state.catalog.optimization_priorities[p]?.title || p)}</span>`),
        ...keywords.map(k => `<span class="pill amber">${esc(k)}</span>`),
      ].join("");
      $("grdlBlock").textContent = c.grdl.dsl + "\n\n" + JSON.stringify({ task_graph: c.grdl.task_graph, constraints: c.grdl.constraints }, null, 2);
      $("promptBlock").textContent = c.llm_prompt;
      $("recommendLabel").textContent = summary.recommended_protocol ? `${summary.recommended_protocol} · ${summary.recommended_score}` : "待分析";
      $("matchRows").innerHTML = c.matches.length ? c.matches.map(m => `<tr><td><b>${esc(m.name)}</b><div class="tag-list">${m.function_tags.map(t => `<span class="pill">${esc(t)}</span>`).join("")}</div></td><td>${esc(m.family)}</td><td>${esc(m.score)}</td><td>${(m.fit_reason || []).map(esc).join("<br>")}</td><td>${(m.risk_notes || []).map(esc).join("<br>")}</td></tr>`).join("") : empty(5);
      $("playbookRows").innerHTML = c.playbook.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.layer)}</td><td>${esc(p.target)}</td><td>${esc(p.method)}<div class="muted">${esc(p.expected_gain)}</div></td><td><span class="pill ${p.enabled ? "green" : "amber"}">${p.enabled ? "启用" : "待触发"}</span></td></tr>`).join("");
      $("validationRows").innerHTML = c.validation.map(v => `<tr><td>${esc(v.gate)}</td><td>${esc(v.check)}</td><td>${esc(v.threshold)}</td><td><span class="pill">${esc(v.status)}</span></td></tr>`).join("");
      $("profileRows").innerHTML = c.protocol_profiles.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.path || "未定位")}</td><td>${esc(p.lines || "-")}</td><td>${esc(p.functions || "-")}</td><td>${esc(p.loops || "-")}</td><td>${p.uses_frame ? "是" : "否"}</td><td>${p.returns_verification_data ? "是" : "否"}</td></tr>`).join("");
      $("techCoverage").innerHTML = c.tech_coverage.map((x,i) => `<div class="metric ${tone(i)}"><span>${esc(x.layer)}</span><strong style="font-size:15px;line-height:1.45">${Array.from(x.items).map(esc).join("<br>")}</strong></div>`).join("");
      renderAnalysisSummary(c);
      renderCharts(c.charts);
      renderDeployment(c.deployment);
      renderNetworkLoop(c, state.currentRun);
      renderRunFlow(state.currentRun, c);
    }
    function renderAnalysisSummary(c) {
      const constraints = c.requirement?.constraints || {};
      $("analysisCaseLabel").textContent = `${c.requirement?.title || "方案"} · ${c.summary?.recommended_protocol || "-"}`;
      const streamTasks = (c.requirement?.tasks || []).filter(t => t.mode === "stream").length;
      const batchTasks = (c.requirement?.tasks || []).filter(t => t.mode === "batch").length;
      const values = [
        ["场景", c.requirement?.domain || "-"],
        ["推荐协议", c.summary?.recommended_protocol || "-"],
        ["流处理任务", streamTasks],
        ["一般处理任务", batchTasks],
        ["SLA", `${constraints.latency_ms || "-"} ms`],
      ];
      $("analysisSummary").innerHTML = values.map(x => `<div class="kpi"><span>${esc(x[0])}</span><b>${esc(x[1])}</b></div>`).join("");
    }
    function renderCharts(charts) {
      renderRadar(charts?.ahp_dimensions || []);
      renderBars("protocolBars", charts?.protocol_scores || [], "score", 1);
      renderBars("latencyBars", charts?.latency_budget || [], "value", Math.max(1, ...(charts?.latency_budget || []).map(x => x.value)));
      const alloc = charts?.task_allocation || [];
      const total = Math.max(1, alloc.reduce((s,x) => s + Number(x.value || 0), 0));
      const colors = ["#0f766e", "#b45309", "#2563eb"];
      $("allocationStack").innerHTML = `<div class="stack">${alloc.map((x,i) => `<span title="${esc(x.layer)}" style="width:${Math.max(4, x.value / total * 100)}%;background:${colors[i % colors.length]}"></span>`).join("")}</div><div class="tag-list" style="margin-top:10px">${alloc.map((x,i) => `<span class="pill" style="border-left:4px solid ${colors[i % colors.length]}">${esc(x.layer)} ${esc(x.value)}</span>`).join("")}</div>`;
      const modes = charts?.processing_modes || [];
      const modeTotal = Math.max(1, modes.reduce((s,x) => s + Number(x.value || 0), 0));
      $("processingModes").innerHTML = modes.length ? `<div class="stack">${modes.map((x,i) => `<span title="${esc(x.name)}" style="width:${Math.max(5, Number(x.value || 0) / modeTotal * 100)}%;background:${i ? "#2563eb" : "#0f766e"}"></span>`).join("")}</div><div class="tag-list" style="margin-top:10px">${modes.map((x,i) => `<span class="pill ${i ? "blue" : "green"}">${esc(x.name)} ${esc(x.value)}</span>`).join("")}</div>` : `<div class="muted">暂无处理模式数据</div>`;
      $("riskCards").innerHTML = (charts?.risk_cards || []).map(r => `<span class="pill ${r.level === "high" ? "red" : r.level === "medium" ? "amber" : "green"}">${esc(r.name)} ${esc(r.value)}</span>`).join("");
    }
    function renderBars(id, rows, key, maxValue) {
      const max = maxValue || Math.max(1, ...rows.map(x => Number(x[key] || 0)));
      $(id).innerHTML = rows.length ? rows.map(x => {
        const v = Number(x[key] || 0);
        return `<div class="bar-row"><span>${esc(x.name)}</span><div class="bar"><span style="width:${Math.max(3, v / max * 100)}%"></span></div><span>${esc(v)}</span></div>`;
      }).join("") : `<div class="muted">暂无图表数据</div>`;
    }
    function renderRadar(items) {
      if (!items.length) { $("radarChart").innerHTML = `<div class="muted">暂无雷达图数据</div>`; return; }
      const cx = 120, cy = 120, r = 82;
      const points = items.map((item, i) => {
        const angle = -Math.PI / 2 + i * Math.PI * 2 / items.length;
        const value = Number(item.value || 0);
        return [cx + Math.cos(angle) * r * value, cy + Math.sin(angle) * r * value];
      });
      const axes = items.map((item, i) => {
        const angle = -Math.PI / 2 + i * Math.PI * 2 / items.length;
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r;
        const tx = cx + Math.cos(angle) * (r + 27);
        const ty = cy + Math.sin(angle) * (r + 27);
        return `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#d7e0ea"/><text x="${tx}" y="${ty}" text-anchor="middle" dominant-baseline="middle" font-size="11" fill="#64748b">${esc(item.label)}</text>`;
      }).join("");
      const rings = [0.33,0.66,1].map(v => {
        const ring = items.map((item,i) => {
          const angle = -Math.PI / 2 + i * Math.PI * 2 / items.length;
          return `${cx + Math.cos(angle) * r * v},${cy + Math.sin(angle) * r * v}`;
        }).join(" ");
        return `<polygon points="${ring}" fill="none" stroke="#e5e7eb"/>`;
      }).join("");
      $("radarChart").innerHTML = `<svg viewBox="0 0 240 240">${rings}${axes}<polygon points="${points.map(p => p.join(",")).join(" ")}" fill="rgba(37,99,235,.24)" stroke="#2563eb" stroke-width="2"/><circle cx="${cx}" cy="${cy}" r="3" fill="#2563eb"/></svg>`;
    }
    function renderDeployment(manifest) {
      $("manifestBlock").textContent = JSON.stringify(manifest || {}, null, 2);
      if (!manifest) {
        $("deployTopology").innerHTML = `<div class="muted">等待生成部署清单</div>`;
        $("messageSequence").innerHTML = `<div class="muted">等待生成消息链路</div>`;
        return;
      }
      const devices = (manifest.devices || []).slice(0, 10).map(d => `<div class="node"><b>${esc(d.device_id)}</b><div class="muted">${esc(d.gateway_id)} · ${esc(Array.from(d.sensors || []).join("/"))}</div><div class="tag-list" style="margin-top:8px">${Array.from(d.tasks || []).map(t => `<span class="pill">${esc(t)}</span>`).join("")}</div></div>`).join("");
      const edges = (manifest.gateways || []).map(g => `<div class="node edge"><b>${esc(g.gateway_id)}</b><div class="muted">主协议 ${esc(g.primary_protocol)} · 回退 ${esc(g.fallback_protocol)}</div><div class="tag-list" style="margin-top:8px">${Array.from(g.tasks || []).slice(0,6).map(t => `<span class="pill">${esc(t)}</span>`).join("")}</div></div>`).join("");
      const cloudTasks = Array.from(manifest.cloud?.tasks || []);
      $("deployTopology").innerHTML = `
        <div class="lane"><h3>端侧 <span class="pill">${(manifest.devices || []).length}</span></h3>${devices}</div>
        <div class="lane"><h3>边缘 <span class="pill">${(manifest.gateways || []).length}</span></h3>${edges}</div>
        <div class="lane"><h3>云端 <span class="pill">反馈闭环</span></h3><div class="node cloud"><b>${esc(manifest.cloud.stream_processor)}</b><div class="muted">${esc(manifest.cloud.batch_processor)}</div></div><div class="node cloud"><b>${esc(manifest.cloud.database)}</b><div class="muted">${esc(manifest.cloud.feedback)}</div></div><div class="tag-list" style="margin-top:10px">${cloudTasks.map(t => `<span class="pill blue">${esc(t)}</span>`).join("")}</div></div>`;
      renderMessageSequence(manifest);
    }
    function renderMessageSequence(manifest) {
      const primary = manifest?.gateways?.[0]?.primary_protocol || "-";
      const fallback = manifest?.gateways?.[0]?.fallback_protocol || "-";
      const steps = [
        ["端侧采集", "RFID 标签、温湿度、读写器窗口被封装成批次消息。", "DeviceScanBatch"],
        ["端边安全传输", "端侧消息使用共享密钥生成 HMAC，边缘先验签再处理。", "HMAC-SHA256"],
        ["边缘推理告警", `边缘执行 ${primary}，异常时本地告警并保留 ${fallback} 回退。`, "EdgeGateway"],
        ["边云摘要上传", "只上传热点、命中率、帧长、耗时和告警摘要，降低云端压力。", "signed summary"],
        ["云端反馈下发", "云端做流批分析、AHP 重排和 Prompt 修复，再回写策略。", "policy/tuning"],
      ];
      $("messageSequence").innerHTML = steps.map((s,i) => `<div class="seq-step" data-step="${i+1}"><b>${esc(s[0])}</b><div class="muted">${esc(s[1])}</div><span class="pill" style="margin-top:10px">${esc(s[2])}</span></div>`).join("");
    }
    function refreshProtocolSelect() {
      const scene = $("scene").value;
      fillSelect($("protocol"), state.catalog.protocols[scene] || [], state.catalog.defaults.protocol);
    }
    async function analyzeDesign() {
      setStatus("正在解析需求并生成方案", "busy");
      const payload = {
        preset: state.selectedPreset,
        scene: $("designScene").value,
        requirement: $("naturalLanguage").value,
        task_ids: state.selectedTasks,
        priorities: state.selectedPriorities,
        devices: Number($("designDevices").value || $("devices").value || 4),
        gateways: Number($("designGateways").value || $("gateways").value || 2),
        constraints: constraintPayload(),
      };
      try {
        const data = await api("/api/design/from-natural-language", { method: "POST", body: JSON.stringify(payload) });
        renderDesignCase(data.case);
        const freshDb = await api("/api/database");
        state.db = freshDb;
        renderOverview();
        renderDb();
        setStatus(`完成设计案例 #${data.case_id}`);
      } catch (e) {
        setStatus(e.message, "error");
        alert(e.message);
      }
    }
    function syncToRun() {
      if (!state.currentCase?.summary?.recommended_protocol) return;
      $("scene").value = $("designScene").value;
      refreshProtocolSelect();
      const protocol = state.currentCase.summary.recommended_protocol;
      if (Array.from($("protocol").options).some(o => o.value === protocol)) $("protocol").value = protocol;
      $("devices").value = $("designDevices").value;
      $("gateways").value = $("designGateways").value;
      switchView("run");
    }
    async function loadRun(id) {
      setStatus(`加载运行 #${id}`, "busy");
      const data = await api(`/api/runs/${id}`);
      renderRun(data.run);
      switchView("run");
      setStatus("就绪");
    }
    function renderRun(run) {
      state.currentRun = run;
      $("currentRunLabel").textContent = run ? `#${run.id}` : "未选择";
      const report = run?.result?.batch_report || {};
      const metrics = [
        ["窗口", report.total_windows || 0, "tone-green"],
        ["命中率", report.average_hit_rate || 0, "tone-teal"],
        ["耗时 ms", run?.avg_gateway_protocol_time_ms || 0, "tone-amber"],
        ["告警", report.alerts?.length || 0, "tone-red"],
      ];
      $("runMetrics").innerHTML = metrics.map(([k,v,t]) => `<div class="metric ${t}"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("");
      const streams = run?.result?.stream_snapshots || [];
      $("streamRows").innerHTML = streams.length ? streams.map(s => `<tr><td>${esc(s.latest_batch)}</td><td>${esc(s.latest_gateway)}</td><td>${esc(s.latest_device)}</td><td>${esc((s.latest_modes || []).join(","))}</td><td>${esc(s.recent_hit_rate)}</td><td>${esc(s.recent_avg_protocol_time_ms)}</td><td>${esc(s.recent_alert_count)}</td></tr>`).join("") : empty(7);
      const gateways = Object.entries(report.gateway_summary || {});
      $("gatewayRows").innerHTML = gateways.length ? gateways.map(([id,g]) => `<tr><td>${esc(id)}</td><td>${esc(g.windows)}</td><td>${esc(g.avg_protocol_time_ms)}</td><td>${esc(g.avg_frame_size)}</td><td>${esc(g.hit_rate)}</td></tr>`).join("") : empty(5);
      const hot = report.global_hot_prices || [];
      const maxCount = Math.max(1, ...hot.map(x => Number(x.count || 0)));
      $("hotRows").innerHTML = hot.length ? hot.map(x => `<tr><td>${esc(x.price)}</td><td><div class="bar"><span style="width:${Math.max(5, Number(x.count || 0) / maxCount * 100)}%"></span></div></td><td>${esc(x.count)}</td></tr>`).join("") : empty(3);
      renderRunFlow(run, state.currentCase);
      renderNetworkLoop(state.currentCase, run);
    }
    function renderRunFlow(run, caseData) {
      const report = run?.result?.batch_report || {};
      const protocol = run?.protocol || caseData?.summary?.recommended_protocol || state.catalog?.defaults?.protocol || "-";
      const devices = run?.devices || caseData?.deployment?.devices?.length || Number($("devices")?.value || 0);
      const gateways = run?.gateways || caseData?.deployment?.gateways?.length || Number($("gateways")?.value || 0);
      const windows = report.total_windows || run?.total_windows || 0;
      const alerts = report.alerts?.length ?? run?.alert_count ?? 0;
      const hitRate = report.average_hit_rate ?? run?.average_hit_rate ?? "-";
      $("runFlowLabel").textContent = run ? `运行 #${run.id} · ${run.scene}/${protocol}` : `预览 · ${protocol}`;
      $("runFlow").innerHTML = `
        <div class="arch-col"><h3>端侧采集 <span class="pill">${esc(devices)}</span></h3><div class="arch-item"><b>批次扫描</b><div class="muted">标签、价格、温湿度进入签名消息。</div></div></div>
        <div class="arch-arrow">→</div>
        <div class="arch-col"><h3>边缘网关 <span class="pill amber">${esc(gateways)}</span></h3><div class="arch-item edge"><b>${esc(protocol)}</b><div class="muted">并发推理、帧约束、局部告警。</div></div></div>
        <div class="arch-arrow">→</div>
        <div class="arch-col"><h3>云端处理 <span class="pill blue">${esc(windows)}</span></h3><div class="arch-item cloud"><b>流批一体</b><div class="muted">命中率 ${esc(hitRate)}，告警 ${esc(alerts)}。</div></div></div>
        <div class="arch-arrow">↺</div>
        <div class="arch-col"><h3>策略反馈 <span class="pill green">闭环</span></h3><div class="arch-item feedback"><b>重排/调参/Prompt</b><div class="muted">运行指标反馈到协议选择和部署策略。</div></div></div>`;
    }
    async function startRun(evt) {
      evt.preventDefault();
      $("startRun").disabled = true;
      setStatus("云边端流程运行中", "busy");
      const payload = {
        scene: $("scene").value,
        protocol: $("protocol").value,
        devices: Number($("devices").value),
        gateways: Number($("gateways").value),
        batches: Number($("batches").value),
        num_tags: Number($("numTags").value),
        price_min: Number($("priceMin").value),
        price_max: Number($("priceMax").value),
        stream_window: Number($("streamWindow").value),
        shared_secret: $("sharedSecret").value,
        include_security: $("includeSecurity").checked,
        include_benchmark: $("includeBenchmark").checked,
        dashboard: true,
      };
      try {
        const data = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
        await loadState(false);
        await loadRun(data.run_id);
        setStatus(`完成运行 #${data.run_id}`);
      } catch (e) {
        setStatus(e.message, "error");
        alert(e.message);
      } finally {
        $("startRun").disabled = false;
      }
    }
    function renderDb() {
      const counts = state.db?.counts || {};
      const keys = ["runs", "design_cases", "deployment_manifests", "stream_snapshots", "security_checks", "ai_jobs", "devices", "gateways"];
      $("dbMetrics").innerHTML = keys.map((k,i) => `<div class="metric ${tone(i)}"><span>${esc(k)}</span><strong>${esc(counts[k] || 0)}</strong></div>`).join("");
      $("dbPath").textContent = state.db?.db_path || "";
      const events = state.db?.recent_events || [];
      $("eventRows").innerHTML = events.length ? events.map(e => `<tr><td>${e.id}</td><td>${esc(e.created_at)}</td><td>${esc(e.event_type)}</td><td>${esc(e.entity_type || "")} ${esc(e.entity_id || "")}</td><td>${esc(e.message)}</td></tr>`).join("") : empty(5);
      const caseRows = (state.db?.design_cases || []).map(c => ({ id: c.id, type: "设计", created_at: c.created_at, content: `${c.title} / ${c.recommended_protocol}`, status: c.recommended_score }));
      const jobRows = (state.db?.ai_jobs || []).map(j => ({ id: j.id, type: "AI", created_at: j.created_at, content: `${j.parent_a}+${j.parent_b} -> ${j.protocol_name}`, status: j.status }));
      $("auditRows").innerHTML = [...caseRows, ...jobRows].length ? [...caseRows, ...jobRows].map(x => `<tr><td>${esc(x.id)}</td><td>${esc(x.type)}</td><td>${esc(x.created_at)}</td><td>${esc(x.content)}</td><td>${esc(x.status)}</td></tr>`).join("") : empty(5);
    }
    async function copyPrompt() {
      const text = $("promptBlock").textContent || "";
      if (!text || text.includes("等待")) return;
      await navigator.clipboard.writeText(text);
      setStatus("Prompt 已复制");
    }
    function hydrateDesignControls(c) {
      if (!c?.requirement) return;
      state.selectedPreset = c.requirement.preset || state.selectedPreset;
      state.selectedTasks = Array.from(c.requirement.task_ids || []);
      state.selectedPriorities = Array.from(c.requirement.priorities || []);
      if (state.catalog?.scenario_presets?.[state.selectedPreset]) {
        renderScenarios();
        renderTasks();
        renderPriorities();
      }
      $("naturalLanguage").value = c.requirement.requirement || $("naturalLanguage").value;
      setConstraintValues(c.requirement.constraints || {});
      $("designDevices").value = c.deployment?.devices?.length || $("designDevices").value;
      $("designGateways").value = c.deployment?.gateways?.length || $("designGateways").value;
    }
    async function loadInitialDesign() {
      if (state.currentCase) {
        renderDesignCase(state.currentCase);
        return;
      }
      const latest = state.db?.design_cases?.[0];
      if (latest?.id) {
        try {
          const detail = await api(`/api/design/cases/${latest.id}`);
          const c = detail.design_case?.case;
          if (c) {
            hydrateDesignControls(c);
            renderDesignCase(c);
            return;
          }
        } catch (e) {
          console.warn(e);
        }
      }
      const preset = state.catalog.scenario_presets[state.selectedPreset];
      const payload = {
        preset: state.selectedPreset,
        scene: $("designScene").value || state.catalog.defaults.scene,
        requirement: $("naturalLanguage").value || preset.requirement,
        task_ids: state.selectedTasks,
        priorities: state.selectedPriorities,
        devices: Number($("designDevices").value || 4),
        gateways: Number($("designGateways").value || 2),
        constraints: constraintPayload(),
      };
      const preview = await api("/api/design/preview", { method: "POST", body: JSON.stringify(payload) });
      hydrateDesignControls(preview.case);
      renderDesignCase(preview.case);
    }
    async function loadState(resetPreset=true) {
      setStatus("同步平台状态", "busy");
      const data = await api("/api/state");
      state.catalog = data.catalog;
      state.runs = data.runs || [];
      state.topology = data.topology;
      state.db = data.database;
      fillSelect($("scene"), state.catalog.scenes, state.catalog.defaults.scene);
      fillSelect($("designScene"), state.catalog.scenes, state.catalog.defaults.scene);
      refreshProtocolSelect();
      $("devices").value = state.catalog.defaults.devices;
      $("gateways").value = state.catalog.defaults.gateways;
      $("batches").value = state.catalog.defaults.batches;
      $("numTags").value = state.catalog.defaults.num_tags;
      $("priceMin").value = state.catalog.defaults.price_min;
      $("priceMax").value = state.catalog.defaults.price_max;
      $("streamWindow").value = state.catalog.defaults.stream_window;
      $("designDevices").value = state.catalog.defaults.devices;
      $("designGateways").value = state.catalog.defaults.gateways;
      renderConstraintBox();
      if (resetPreset || !state.selectedTasks.length) setPreset(state.selectedPreset, true);
      else {
        setConstraintValues(state.currentCase?.requirement?.constraints || state.catalog.scenario_presets[state.selectedPreset].constraints);
        renderScenarios();
        renderTasks();
        renderPriorities();
      }
      renderOverview();
      renderDb();
      await loadInitialDesign();
      if (state.runs[0]) {
        const detail = await api(`/api/runs/${state.runs[0].id}`);
        renderRun(detail.run);
      } else {
        renderRun(null);
      }
      setStatus("就绪");
    }
    function bind() {
      $("scene").onchange = refreshProtocolSelect;
      $("analyzeDesign").onclick = analyzeDesign;
      $("syncRun").onclick = syncToRun;
      $("copyPrompt").onclick = copyPrompt;
      $("runForm").onsubmit = startRun;
      $("refreshState").onclick = () => loadState(false);
    }
    mountNav();
    bind();
    loadState().catch(e => { console.error(e); setStatus(e.message, "error"); });
  </script>
</body>
</html>"""
