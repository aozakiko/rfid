# RFID 协议自优化 Web 平台

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

## Web 页面

- 总览：公告式说明书、平台闭环、场景资产统计和世代分布。
- 场景与代码：按 `Task/config`、`generation`、`prompts`、`base_protocol` 分组管理资产，右侧提供代码/Prompt 编辑器。
- Prompt 与 AI：单次协议生成页，只选择父代 A/B；支持任务画像、可选自然语言约束、模型探测、异步进度、Prompt 预览和协议代码生成。
- 验证沙箱：保留单协议验证，覆盖任务烟测、物理约束检查、信息隔离静态扫描和运行结果 JSON。
- 自动迭代：父代池只在本页出现；支持父代池组合、多轮 Prompt/协议生成、实时进度条、步骤卡片、日志、轮次表和本页内协议预览。
- Outputs：直接展示 `course_project/outputs` 下的 HTML、CSV、JSON、Markdown、日志、PDF、图片等产物。
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

平台复用 `backend_tools/openai_compatible.py`，支持 OpenAI 兼容接口。可通过环境变量或 Web 页面临时配置：

- `AI_API_BASE_URL` / `OPENAI_BASE_URL` / `GEEK_API_BASE_URL`
- `AI_API_MODEL` / `OPENAI_MODEL` / `GEEK_API_MODEL`
- `AI_API_KEY` / `OPENAI_API_KEY` / `GEEK_API_KEY`

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

## 关键文件

- `course_project/web_app.py`：Web API、资产安全、AI 生成、验证、实验、outputs 和自动迭代。
- `course_project/selfopt_ui.py`：统一 Web 工作台和交互界面。
- `ai_protocol_optimizer.py`：真实 AI 协议生成、自修复和多任务验证流程。
- `prompt_generation.py`：父代协议读取与通用交叉 Prompt 生成。
- `Validator.py`：协议物理约束验证。
- `Experiments/`：鲁棒性、敏感度与进化图实验。
- `base_protocol/`：基线协议库。
- `multi_mode/`、`single_mode/`、`ManyTags/`：场景与世代协议资产。

## 验证

```powershell
python -m py_compile course_project/web_app.py course_project/selfopt_ui.py course_project/test_course_project.py prompt_generation.py ai_protocol_optimizer.py Test.py Experiments/Test_robust.py Experiments/plot_robust.py Experiments/plot_evolution_results.py
python -m unittest course_project.test_course_project -v
```

已覆盖：状态读取、页面加载、文件读取、Prompt 生成、AI 任务预览、单协议验证、自动迭代预览、Experiments 鲁棒性任务和进化图任务。当前 Web 主流程聚焦“生成 -> 验证 -> 迭代 -> 产物预览”的自优化闭环。
