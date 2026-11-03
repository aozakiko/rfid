# RFID 协议自优化 Web 平台

项目总目标为“基于大模型的 RFID 射频识别通讯协议自优化研究平台开发”。目前支持固定开发集质量评价、平台计量的通信成本选择、预算控制、搜索后冻结候选并独立验证，以及候选公平比较。前端已迁移为 Vue 3 + TypeScript + Vite，完整开发、部署和页面说明见 [前端说明](frontend/README.md)。

首次使用请阅读 [仓库图文操作指南](../../README.md)，其中包含各页面截图、上传与预览操作、文件架构。

本项目已统一为一个 Web 自优化平台，定位为“面向多维统计场景的 RFID 协议自优化 Web 平台开发”。平台不再是分散 GUI、脚本或单一众数查询演示，而是围绕“自然语言需求 -> 任务画像/GRDL 思路 -> 父代协议基因库 -> 大模型交叉变异 -> 在线沙箱验证 -> 实验图表与 outputs -> 自动迭代 -> 审计记录”形成闭环。

## 启动

首次使用先在 `rfid/code_add/course_project/frontend` 目录构建前端（需要 Node.js 20.19+ 或 22.12+）：

~~~powershell
npm ci
npm run build
~~~

随后在 `rfid/code_add` 目录运行：

```powershell
python -m course_project.web_app --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

## 工程结构与运行约定

新版前端位于 `frontend/src/`，使用 Vue Router、Pinia、Element Plus 和 ECharts，按页面拆分加载。`frontend_assets.py` 将构建产物接入现有 Python 服务；部署无需独立 Node 运行进程或外部 CDN。原 `static/` 和 `selfopt_ui.py` 保留，旧版入口为 `/legacy`；未构建新版时根路径显示旧版。

- `web_app.py`：路由、场景资产和协议/实验工作流编排。
- `http_support.py`：JSON 请求边界、响应头及统一错误映射；请求体上限 2 MB，非空请求体需使用 `application/json`。
- `validation.py`：数字范围与布尔类型校验。非法值返回 400，不再悄悄截断或替换成默认值。
- `jobs.py`：后台任务管理。最多 2 个任务并行，运行与排队合计最多 12 个；队列满返回 503，已接受任务返回 202。保留最近 80 条任务快照，活动任务不会被清理，完成后的耗时固定。
- `assets.py`：文件导入、类型/路径校验和有界预览；`static/asset_ui.js` 与 `static/assets.css` 提供统一资产视图、上传及分页组件。
- `database.py`：SQLite 持久化 AI 任务、验证报告与审计记录。服务重启后原队列不能继续执行，历史未完成任务标记为 interrupted，可通过新名称重试。

编辑器读取文件时取得 SHA-256 版本号，保存时带回 `revision`；文件发生变化则返回 409，要求重新读取，写入采用临时文件原子替换。各场景 `outputs/` 均支持预览；新版 HTML/PDF 使用 sandbox iframe，SVG 使用图片元素展示。

这仍是单机实验工作台。候选协议在独立子进程运行；严格模式增加 AST、导入/调用白名单、标签访问代理、运行时审计以及超时、内存和输出限制。该实现属于受控的软件验证环境，尚未使用容器或完整的操作系统沙箱。

## 文件导入与资产预览

新版在“场景与代码”集中提供文件导入入口，选择保存位置后逐个导入。每个文件最多 10 MB；文本使用 UTF-8 编码，JSON 上传时校验格式。同名文件返回 409，原文件保持原样。

- 场景页：选择已有场景，将 `.py` 协议保存到指定世代、`.txt` Prompt 保存到 `prompts/`，或将数据和报告保存到场景 `outputs/uploads/`。导入协议仅写入文件，需在验证页手动选择后运行。
- 选择“运行产物”：保存至 `course_project/outputs/uploads/`。
- 选择“Experiments 产物”：保存至 `Experiments/uploads/`；该目录下的嵌套实验产物同样会被扫描。

新版资产列表支持搜索和分页；CSV 每页 20 条、文本预览每页 100 行，JSON 提供可展开的原始内容，图片和 HTML/PDF 可直接预览，所有资产可下载。源码编辑区可直接修改完整文件；读取和保存沿用后端版本校验。超过 10 MB 的文本采用下载查看，超过 1 MB 的 JSON 使用分页文本视图。

## Web 页面

- 总览：项目概况、工作流导航、场景资产统计和世代分布；支持桌面和移动布局。
- 场景与代码：管理 `Task/config`、`generation`、`prompts`、`base_protocol` 资产，提供文件搜索、未保存提示及 Ctrl/⌘ + S 保存。
- 协议设计：单次协议生成页，只选择父代 A/B；支持任务画像、可选自然语言约束、模型探测、异步进度、Prompt 预览和协议代码生成。
- 验证沙箱：保留单协议验证，覆盖任务烟测、物理约束检查、信息隔离静态扫描和运行结果 JSON。
- 自动迭代：支持父代池组合、多轮 Prompt/协议生成、预算与停止条件、独立验证、实时进度、日志、轮次表和本页内协议预览。
- 实验产物：直接展示各场景 `outputs/` 目录以及平台产物目录下的 HTML、CSV、JSON、Markdown、日志、PDF、图片等产物。
- 实验分析：配置化实验、盲测流水线、重建报告，以及原项目敏感度实验和绘图。
- 统计基准：同场景筛选、Pareto 和置信区间、逐种子证据、真实 LLM 基线与候选导入。
- 审计：展示 SQLite 中的运行记录、AI 任务记录、系统计数和模型环境状态。

## 场景与任务画像

场景是可执行资产目录，例如 `multi_mode/Task.py`、各 `generation*` 协议、Prompt 文件和相关配置。任务画像是本次生成或验证的统计目标和评价口径。同一个场景可以切换不同任务画像，生成面向不同统计目标的 RFID 协议。

当前平台已内置多类 RFID/物联网统计任务画像：

- 离散属性众数/多众数查询
- 连续数值均值估计
- 连续数值方差/稳定性估计
- 中位数/分位数估计
- 标签基数/库存规模估计
- 高缺失率库存盘点

现有 `base.py` 接口仍沿用 `execute(tags, price_min, price_max)`，其中 `tag.price` 在新平台中被抽象为“标签携带的数值属性”。后续可以继续扩展为多属性标签和更完整的 GRDL 解释器。

## 大模型接入

平台复用 `backend_tools/openai_compatible.py`，支持 OpenAI 兼容接口。Web 页面直接填写 API URL、API Key 和模型名，不读取模型环境变量，不需要填写接口路径。

每次模型检查、真实生成和自动迭代都只使用当前提交的页面配置，修改后下一次请求立即生效，无需重启。接口自动尝试 Responses，遇到不支持该接口的响应时切换到 Chat Completions；鉴权失败、限流或网络故障不会触发接口切换。成功识别的接口在本次工作流的后续调用中复用。

API Key 仅随请求提交，后台任务公开快照会剔除密钥，密钥不会保存在配置文件或浏览器本地存储；刷新页面后重新填写。预览任务不要求模型配置。

页面中的“预览生成任务”只生成 Prompt 与目标路径，不调用模型，也不会创建 `.py` 协议文件；“生成协议代码”会真实访问模型网关，写入 `.py` 子代协议，并执行验证/自修复流程。AI 生成和自动迭代均走后台任务接口，新版以约 1.5 秒间隔轮询进度，显示排队、校验、连接模型、生成、验证、落盘和完成状态。网络层对 429/5xx、连接重置、超时等异常进行重试。

任务画像会进入三处：Prompt 约束、默认文件命名和验证口径。例如 `mean_estimation` 默认生成 `web_mean_estimation_candidate.py`，自动迭代会生成 `web_mean_estimation_iter_01.py` 这类候选；预览任务的状态会标记 `protocol_exists=false`，真实生成成功后才标记 `protocol_exists=true`。

## 主要 API

2026-11-03 新增后台任务历史、停止与重试：GET /api/selfopt/jobs；POST /api/selfopt/job/cancel（id）；POST /api/selfopt/job/retry（id 与当前模型配置）。任务历史持久化，重启后的未完成任务标记为 interrupted，重试使用新名称重新运行。

自动迭代可选择 selection_objective=precision_constrained_cost，按 minimum_pass_rate 约束选择平台计量的模拟通信成本；兼容 quality_only 质量探索。independent_validation 默认启用，搜索后冻结赢家与初始基线，检查验证数据重叠。可配置 max_model_requests、max_wall_seconds、no_improvement_rounds 和 target_communication_cost_ms，记录实际消耗及停止原因。

新接口、模型口径、限制和旧实验迁移详见[平台通信优化与独立验证迭代记录](../../docs/yanxiao/平台通信优化与独立验证迭代记录20261103.md)。

- `GET /api/selfopt/state`：读取平台状态、场景资产、实验产物、outputs、AI 环境状态。
- `GET /api/selfopt/file?path=...` / `POST /api/selfopt/file`：读取与保存项目文本资产。
- `GET /api/selfopt/raw?path=...`：预览 outputs/Experiments 中的 HTML、PDF、图片等产物。
- `GET /api/selfopt/job?id=...`：读取后台 AI/迭代任务进度。
- `POST /api/selfopt/scene`：创建新场景。
- `POST /api/selfopt/baseline-copy`：复制基线协议到场景世代。
- `POST /api/selfopt/prompts`：生成交叉 Prompt。
- `POST /api/selfopt/ai-models`：探测模型或列出模型。
- `POST /api/selfopt/ai-run-async`：异步预览或真实调用大模型生成协议。
- `POST /api/selfopt/validate`：单协议物理约束验证、任务烟测与信息隔离检查；通过 `security_mode=strict` 启用 AST 和运行时隔离。
- `POST /api/selfopt/validate-async`：后台单协议验证，支持冻结候选 ID、进度和停止。
- `GET /api/selfopt/validation-reports?scene=...&protocol=...&limit=...`：查询验证历史摘要（源码 SHA-256、模式、阶段判定和最终结果）。
- `GET /api/selfopt/validation-report?id=...`：读取一条完整验证报告及原始阶段结果。
- `POST /api/selfopt/auto-iterate-async`：异步自动迭代生成候选协议。
- `POST /api/selfopt/experiments`：运行 Experiments 实验与绘图任务。
- `POST /api/selfopt/experiments-async`：后台场景实验与绘图。
- `GET /api/research/capabilities` / `GET /api/research/candidates`：评测能力、候选版本库和完整性问题。
- `POST /api/research/register` / `POST /api/research/evaluate-async`：冻结候选，按 Python / JSON 类型进入兼容的评测引擎。
- `GET /api/research/runs`（可带 `id`）：跨场景验证与盲测历史、汇总和完整性审计。
- `POST /api/selfopt/upload?destination=...&name=...`：原始文件体上传，使用 `application/octet-stream`；场景资产额外传 `scene` 和可选 `generation`。
- `GET /api/selfopt/preview?path=...&page=1`：按类型返回预览数据，页码从 1 开始。
- `GET /api/selfopt/download?path=...`：以附件形式下载原始资产。

## 关键文件

- `course_project/web_app.py`：Web API、资产安全、AI 生成、验证、实验、outputs 和自动迭代。
- `course_project/frontend/`：Vue 前端源码、锁文件、单元测试和浏览器测试；`frontend_assets.py` 托管构建产物。
- `course_project/static/`：旧工作台；`selfopt_ui.py` 提供兼容导出。
- `ai_protocol_optimizer.py`：真实 AI 协议生成、自修复和多任务验证流程。
- `prompt_generation.py`：父代协议读取与通用交叉 Prompt 生成。
- `Validator.py`：协议物理约束验证。
- `Experiments/`：鲁棒性、敏感度与进化图实验。
- `base_protocol/`：基线协议库。

### 验证安全模式

验证沙箱提供两种模式。`legacy` 兼容历史协议：保留原有仿真执行方式，并将直接标签属性访问作为告警记录；`strict` 使用 AST/危险调用检查、受限子进程和运行时标签代理，阻止读写器逻辑直接读取 `price`、`value` 或 `__dict__`。严格模式下，信息隔离或运行安全失败会使候选验证失败，不能进入正式候选结果。该机制是软件层验证，不等于硬件隐私证明或完整操作系统沙箱。

每次验证都会持久化静态 AST、白名单、运行时隔离、物理约束和任务语义五个阶段；历史协议可在兼容模式运行并显示告警，但不会被标记为严格隔离通过。报告保存逐轮摘要、源码哈希和阶段证据，新版使用表格展示逐次执行，不保存 API Key。
- `multi_mode/`、`single_mode/`、`ManyTags/`：场景与世代协议资产。

## 验证

```powershell
python -m py_compile course_project/web_app.py course_project/selfopt_ui.py course_project/test_course_project.py prompt_generation.py ai_protocol_optimizer.py Test.py Experiments/Test_robust.py Experiments/plot_robust.py Experiments/plot_evolution_results.py
python -m unittest course_project.test_course_project course_project.test_infrastructure -v
```

已覆盖：状态读取、页面加载、文件读取、Prompt 生成、AI 任务预览、单协议验证、自动迭代预览、Experiments 鲁棒性任务和进化图任务。当前 Web 主流程聚焦“生成 -> 验证 -> 迭代 -> 产物预览”的自优化闭环。

新增工程接口：`GET /api/selfopt/candidates` 查询已记录的候选父代关系；`GET /api/statistics/comparison` 返回后端计算的前沿与排除原因；`POST /api/statistics/run-config` 提交配置化实验；`GET /api/statistics/config-runs` 查询结果和历史；`POST /api/statistics/rebuild` 从原始结果重建报告。运行进度继续使用 `/api/selfopt/job`。详细字段和复现命令见[严枭工程集成说明](../../docs/yanxiao/严枭工程集成与复现实验说明.md)。

候选版本、后台任务、跨场景评测和 Vue 操作衔接见[候选评测与前后端闭环完善记录](../../docs/yanxiao/候选评测与前后端闭环完善记录20261103.md)。新版“候选评测”入口为 `/#/research`。
