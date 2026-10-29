# RFID 协议自优化 Web 平台

首次使用请阅读 [仓库图文操作指南](../../README.md)，其中包含各页面截图、上传与预览操作、文件架构。

本项目已统一为一个 Web 自优化平台，定位为“面向多维统计场景的 RFID 协议自优化 Web 平台开发”。平台不再是分散 GUI、脚本或单一众数查询演示，而是围绕“自然语言需求 -> 任务画像/GRDL 思路 -> 父代协议基因库 -> 大模型交叉变异 -> 在线沙箱验证 -> 实验图表与 outputs -> 自动迭代 -> 审计记录”形成闭环。

## 启动

在 `rfid/code_add` 目录运行：

```powershell
python -m course_project.web_app --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

## 工程结构与运行约定

Web 页面、样式和交互分别位于 `static/index.html`、`static/app.css`、`static/app.js`，无需 Node 构建或外部 CDN。`selfopt_ui.py` 保留 HTML 导出的兼容接口。

- `web_app.py`：路由、场景资产和协议/实验工作流编排。
- `http_support.py`：JSON 请求边界、响应头及统一错误映射；请求体上限 2 MB，非空请求体需使用 `application/json`。
- `validation.py`：数字范围与布尔类型校验。非法值返回 400，不再悄悄截断或替换成默认值。
- `jobs.py`：后台任务管理。最多 2 个任务并行，运行与排队合计最多 12 个；队列满返回 503，已接受任务返回 202。保留最近 80 条任务快照，活动任务不会被清理，完成后的耗时固定。
- `assets.py`：文件导入、类型/路径校验和有界预览；`static/asset_ui.js` 与 `static/assets.css` 提供统一资产视图、上传及分页组件。
- `database.py`：SQLite 持久化 AI 任务与审计记录。后台队列本身保存在进程内，服务重启后原队列不可继续轮询。

编辑器读取文件时取得 SHA-256 版本号，保存时带回 `revision`；文件发生变化则返回 409，要求重新读取，写入采用临时文件原子替换。各场景 `outputs/` 均支持预览；HTML/SVG 预览受浏览器 sandbox 策略约束。

这仍是单机实验工作台：协议 Python 代码的执行未引入容器/进程级隔离。若后续接入多人使用或部署到公网，需要补充身份认证、执行隔离和持久化任务队列。

## 文件导入与资产预览

“场景与代码”“实验产物”“实验分析”页面顶部提供可展开的本地上传入口，支持选择文件或拖放。每个文件最多 10 MB，每批最多 10 个；文本使用 UTF-8 编码，JSON 上传时校验格式。同名文件返回 409，原文件保持原样。

- 场景页：选择已有场景，将 `.py` 协议保存到指定世代、`.txt` Prompt 保存到 `prompts/`，或将数据和报告保存到场景 `outputs/uploads/`。导入协议仅写入文件，需在验证页手动选择后运行。
- 实验产物页：保存至 `course_project/outputs/uploads/`。
- 实验分析页：保存至 `Experiments/uploads/`；该目录下的嵌套实验产物同样会被扫描。

资产列表可搜索并按 8 / 16 / 32 项翻页，不再截断为最近 20 份 Prompt 或 80 / 240 个产物。CSV 提供每页 20 条数据、字段切换趋势图和表格；JSON 提供惰性展开结构；Markdown/Prompt 提供转义后的阅读视图；文本与代码按 100 行翻页，代码附行号与关键字高亮。图片支持适应容器和原始尺寸，HTML/PDF 提供文档预览，资产均可下载原文件。超过 10 MB 的文本采用下载查看，超过 1 MB 的 JSON 使用分页文本视图。

## Web 页面

- 总览：项目概况、工作流导航、场景资产统计和世代分布；支持桌面和移动布局。
- 场景与代码：按 `Task/config`、`generation`、`prompts`、`base_protocol` 分组管理资产，提供文件搜索、未保存提示及 Ctrl/⌘ + S 保存。
- Prompt 与 AI：单次协议生成页，只选择父代 A/B；支持任务画像、可选自然语言约束、模型探测、异步进度、Prompt 预览和协议代码生成。
- 验证沙箱：保留单协议验证，覆盖任务烟测、物理约束检查、信息隔离静态扫描和运行结果 JSON。
- 自动迭代：父代池只在本页出现；支持父代池组合、多轮 Prompt/协议生成、实时进度条、步骤卡片、日志、轮次表和本页内协议预览。
- Outputs：直接展示各场景 `outputs/` 目录以及平台产物目录下的 HTML、CSV、JSON、Markdown、日志、PDF、图片等产物。
- Experiments：迁移原项目鲁棒性/敏感度实验、进化图和终稿图生成能力。
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

页面中的“预览生成任务”只生成 Prompt 与目标路径，不调用模型，也不会创建 `.py` 协议文件；“生成协议代码”会真实访问模型网关，写入 `.py` 子代协议，并执行验证/自修复流程。AI 生成和自动迭代均走后台任务接口，浏览器每秒轮询进度，显示排队、校验、连接模型、生成、验证、落盘和完成状态。网络层对 429/5xx、连接重置、超时等异常进行重试。

任务画像会进入三处：Prompt 约束、默认文件命名和验证口径。例如 `mean_estimation` 默认生成 `web_mean_estimation_candidate.py`，自动迭代会生成 `web_mean_estimation_iter_01.py` 这类候选；预览任务的状态会标记 `protocol_exists=false`，真实生成成功后才标记 `protocol_exists=true`。

## 主要 API

- `GET /api/selfopt/state`：读取平台状态、场景资产、实验产物、outputs、AI 环境状态。
- `GET /api/selfopt/file?path=...` / `POST /api/selfopt/file`：读取与保存项目文本资产。
- `GET /api/selfopt/raw?path=...`：预览 outputs/Experiments 中的 HTML、PDF、图片等产物。
- `GET /api/selfopt/job?id=...`：读取后台 AI/迭代任务进度。
- `POST /api/selfopt/scene`：创建新场景。
- `POST /api/selfopt/baseline-copy`：复制基线协议到场景世代。
- `POST /api/selfopt/prompts`：生成交叉 Prompt。
- `POST /api/selfopt/ai-models`：探测模型或列出模型。
- `POST /api/selfopt/ai-run-async`：异步预览或真实调用大模型生成协议。
- `POST /api/selfopt/validate`：单协议物理约束验证、任务烟测与信息隔离静态扫描。
- `POST /api/selfopt/auto-iterate-async`：异步自动迭代生成候选协议。
- `POST /api/selfopt/experiments`：运行 Experiments 实验与绘图任务。
- `POST /api/selfopt/upload?destination=...&name=...`：原始文件体上传，使用 `application/octet-stream`；场景资产额外传 `scene` 和可选 `generation`。
- `GET /api/selfopt/preview?path=...&page=1`：按类型返回预览数据，页码从 1 开始。
- `GET /api/selfopt/download?path=...`：以附件形式下载原始资产。

## 关键文件

- `course_project/web_app.py`：Web API、资产安全、AI 生成、验证、实验、outputs 和自动迭代。
- `course_project/static/`：Web 工作台、样式与交互逻辑；`selfopt_ui.py` 提供兼容导出。
- `ai_protocol_optimizer.py`：真实 AI 协议生成、自修复和多任务验证流程。
- `prompt_generation.py`：父代协议读取与通用交叉 Prompt 生成。
- `Validator.py`：协议物理约束验证。
- `Experiments/`：鲁棒性、敏感度与进化图实验。
- `base_protocol/`：基线协议库。
- `multi_mode/`、`single_mode/`、`ManyTags/`：场景与世代协议资产。

## 验证

```powershell
python -m py_compile course_project/web_app.py course_project/selfopt_ui.py course_project/test_course_project.py prompt_generation.py ai_protocol_optimizer.py Test.py Experiments/Test_robust.py Experiments/plot_robust.py Experiments/plot_evolution_results.py
python -m unittest course_project.test_course_project course_project.test_infrastructure -v
```

已覆盖：状态读取、页面加载、文件读取、Prompt 生成、AI 任务预览、单协议验证、自动迭代预览、Experiments 鲁棒性任务和进化图任务。当前 Web 主流程聚焦“生成 -> 验证 -> 迭代 -> 产物预览”的自优化闭环。
