# RFID 协议进化与实验（PE-IDE）项目说明

本仓库包含两条主线：

1. **协议进化/管理（GUI + 场景目录）**：用 GUI 管理 `generation_*` 协议、编辑任务与配置、生成提示词。
2. **离线实验（Experiments）**：对协议做敏感度/鲁棒性测试、汇总进化结果并绘图。

本文档重点把“当前文件夹每一部分的实验内容”和“启动方式/指令”讲清楚，并标注我已实际验证过的命令。

---

## 0. 我们新增了什么（2026-03-07）

相对原始 `code/` 版本，当前 `code_add/` 额外补齐了“实验可重复 + 结果留存”的后端能力（不改变原有使用方式）。

- **SQLite 运行记录（runs.sqlite3）**：自动创建并记录每次运行的 `kind/scene/命令/参数/状态/退出码/输出日志/产物文件列表`。
- **新增 backend_tools/**（可独立使用，也被 GUI 调用）：
  - [backend_tools/run_store.py](backend_tools/run_store.py)：SQLite 表结构与增删查。
  - [backend_tools/artifacts.py](backend_tools/artifacts.py)：产物快照与 diff（用于识别本次运行新增的 CSV/PDF/PNG/JPG/TXT/JSON 等输出）。
  - [backend_tools/run_cmd.py](backend_tools/run_cmd.py)：命令执行包装器（捕获 stdout/stderr + 产物 diff + 落库）。
  - [backend_tools/query_runs.py](backend_tools/query_runs.py)：查询 runs.sqlite3 的 CLI。
- **GUI 自动落库**：在 [protocol_evolution_gui.py](protocol_evolution_gui.py) 里对“批量测试(Test.py)”与“实验自动化(Experiments)”做了无侵入增强：运行结束后自动写入 `runs.sqlite3`；数据库写入失败不会影响 GUI 正常显示输出。

常用命令：

```powershell
cd "E:\RFID\code_add"

# 列出最近的运行记录
python -m backend_tools.query_runs list --limit 10

# 查看某次 run 的详情（含输出与产物）
python -m backend_tools.query_runs show --id 1
```

## 1. 环境要求

- Python：建议 **3.8+**（你当前环境为 conda base 也可用）
- GUI 依赖：`ttkthemes`
- Experiments 依赖：`numpy`、`pandas`、`matplotlib`

一次性安装：

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
python -m pip install -U ttkthemes numpy pandas matplotlib
```

说明：如果你不跑 GUI，可不装 `ttkthemes`。

---

## 2. 目录结构与用途（每部分在做什么）

### 2.1 根目录（核心框架 + 入口）

- [base.py](base.py)
  - 定义 `Tag`、`Frame`、`IProtocol` 接口，以及 `simple_hash()`、`generate_random_seed()` 等协议基础工具。
  - 所有协议类必须实现：`execute(tags, price_min, price_max) -> (modes, metric, time_ms, verification_data)`。

- [Validator.py](Validator.py)
  - `ProtocolValidator`：自动化硬约束验证。
  - 当前检查：`bits_per_slot <= 256`、`frame_size <= 32768`（从协议返回的 `verification_data` 读取）。

- [prompt_generation.py](prompt_generation.py)
  - 生成“交叉提示词”（父代两两组合）到场景的 `prompts/`。
  - 搜索父代代码顺序：先场景各 `generation*`，再 `base_protocol/`。

- [protocol_evolution_gui.py](protocol_evolution_gui.py)
  - PE-IDE GUI：登录/注册、场景创建、协议编辑保存、协议验证+性能测试、提示词生成。
  - 新增可视化模块：`使用向导`、`基线协议库`、`批量测试(Test.py)`、`实验自动化(Experiments)`、`结果中心`。

- [Test.py](Test.py)
  - 命令行批量测试器：自动发现指定场景下所有 `generation*` 协议并测试。
  - 先 `ProtocolValidator.validate()`，再性能测试多轮，最后输出汇总排名。

- [hash.py](hash.py)
  - `simple_hash()` 兼容层（有些协议写 `from hash import simple_hash`）。

- [users.json](users.json)
  - GUI 本地账号存储（密码为 SHA-256 哈希）。

### 2.2 base_protocol/（基础协议库 / baseline）

目录： [base_protocol/](base_protocol/)

包含 5 个基础协议（例如 SEM/TCS/TKQ/EE/ID），用作：
- `generation_0` 初代种群
- 进化时的父代候选
- 实验对照组

### 2.3 场景目录（multi_mode / single_mode / ManyTags / rfid_mode_finding_v1 / ss）

这些目录本质上都是“一个可独立测试的实验场景（scene）”。通常包含：

- `generation*/` 或 `generation_*/`：每一代协议候选（文件名=类名的 `.py`）。
- `prompts/`：由 `prompt_generation.py` 生成、喂给 LLM 的交叉提示词。
- `Task.py`：标签生成逻辑 `generate_tags()`；有的场景还把 `SIMULATION_CONFIG` 放在 Task.py。
- `config.py`：GUI 场景配置用（NUM_TAGS/PRICE_MIN/PRICE_MAX/TOTAL_RUNS）。

场景含义概览：
- [multi_mode/](multi_mode/)：多众数/多峰主场景（代数多）。
- [single_mode/](single_mode/)：单众数/单峰场景。
- [ManyTags/](ManyTags/)：规模更小的多众数场景样例。
- [rfid_mode_finding_v1/](rfid_mode_finding_v1/)：较“规范”的样板场景（带 config）。
- [ss/](ss/)：另一个多众数场景样例。

### 2.4 Experiments/（离线自动化实验与绘图）

目录： [Experiments/](Experiments/)

- [Experiments/Test_robust.py](Experiments/Test_robust.py)
  - 自动化敏感度/鲁棒性测试：对指定协议集合，在不同因素（dominance/scale/density）下跑多轮，输出 CSV。
  - 支持参数化调用：`--protocols`、`--factors`、`--num-tags`、`--total-runs`、`--point-count`、`--output` 等。

- [Experiments/plot_robust.py](Experiments/plot_robust.py)
  - 读取指定 CSV 并画对比曲线（输出 pdf）。
  - 支持参数化调用：`--csv`、`--factors`、`--output-dir`、`--no-show`。

- [Experiments/evolution_data_combined.json](Experiments/evolution_data_combined.json)
  - 汇总的进化结果数据（按场景按代的个体指标）。

- [Experiments/plot_evolution_results.py](Experiments/plot_evolution_results.py)
  - 读取 `evolution_data_combined.json`，绘制进化过程相关图表（输出 pdf/png）。
  - 支持参数化调用：`--scenario`、`--charts`、`--data`、`--output-dir`、`--no-show`。

- [Experiments/plot.py](Experiments/plot.py)
  - 使用写死的数据画“终稿对比图”（输出 pdf）。

---

## 3. 启动方式（含已验证命令）

> 下面命令均以项目根目录（含 base.py 的目录）为当前目录。

### 3.1 命令行批量测试（Test.py）

#### 快速烟囱测试（推荐）

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
python Test.py --scene multi_mode --num-tags 500 --total-runs 1 g2_4
```

- ✅ 已验证：可以正常发现协议、完成验证与 1 次性能测试并输出汇总。
- 说明：`--num-tags/--total-runs` 是为了避免默认配置导致跑太久（例如 `g2_4` 可能非常慢）。

#### 测试整个场景所有协议（耗时，慎用大参数）

```powershell
python Test.py --scene multi_mode --num-tags 2000 --total-runs 3 all
```

#### 按通配符筛选协议

```powershell
python Test.py --scene multi_mode --num-tags 2000 --total-runs 3 g10_*
```

### 3.2 Experiments：自动化敏感度/鲁棒性测试 + 绘图

#### Step A：运行敏感度测试（生成 CSV，参数化）

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
python Experiments\Test_robust.py --protocols g10_4:single_mode,g8_3:multi_mode --factors density --num-tags 10000 --total-runs 10 --point-count 10 --output Experiments\sensitivity_results.csv
```

- ✅ 已验证：参数化命令可运行并输出 CSV（示例中输出到 `Experiments/sensitivity_results.csv`）。

#### Step B：从 CSV 直接绘制鲁棒性图（支持因素选择）

```powershell
python Experiments\plot_robust.py --csv Experiments\sensitivity_results.csv --factors density --output-dir Experiments --no-show
```

- ✅ 已验证：`plot_robust.py` 能读取 CSV 并生成 `sensitivity_comparison_density.pdf`。

> 可选：如果你在无界面环境跑绘图，可加 `MPLBACKEND=Agg`。

```powershell
$env:MPLBACKEND="Agg"; python Experiments\plot_robust.py
```

- ✅ 已验证：可运行，且会提示 `FigureCanvasAgg is non-interactive`（正常）。

### 3.3 Experiments：进化结果绘图（基于 evolution_data_combined.json）

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
$env:MPLBACKEND="Agg"; python Experiments\plot_evolution_results.py --scenario single_peak --charts correction --output-dir Experiments --no-show
```

- ✅ 已验证：可生成例如 `chart_4_correction_counts_single_peak.pdf`。

### 3.4 Experiments：终稿手工图（plot.py）

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
$env:MPLBACKEND="Agg"; python Experiments\plot.py
```

- ✅ 已验证：生成 `final_protocol_performance_final.pdf`。

### 3.5 GUI（协议进化与管理）

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
python protocol_evolution_gui.py
```

- 说明：这是交互式窗口程序，命令本身正确，但“自动化验证”需要人工看到窗口弹出并退出。

### 3.6 GUI 新版操作指南（推荐）

> GUI 现在已按模块拆分，建议严格按下面顺序操作。

#### 模块0：使用向导

- 页签：`使用向导`
- 作用：展示全流程步骤，适合第一次使用快速定位下一步。

#### 模块1：场景准备

- 左侧控制面板：
  - 新场景：输入场景名，点击 `创建`
  - 场景选择：点击列表项后，右侧所有页签切换到该场景
- 推荐先完成：`场景配置` + `任务编辑器`

#### 模块2：协议工作区

- 页签：`协议工作区`
- 操作流程：
  - 树中选择协议 -> 编辑代码 -> 选择目标世代 -> `保存/更新协议`
  - 点击 `开始测试选中协议` 查看完整验证+性能日志

#### 模块3：基线协议库

- 页签：`基线协议库`
- 可做事情：
  - 浏览/编辑 `base_protocol/*.py`
  - `保存基线协议`
  - `复制到当前场景 generation_0`
  - `在当前场景测试此基线协议`

#### 模块4：批量测试（Test.py 可视化）

- 页签：`批量测试 (Test.py)`
- 可设置：
  - 场景、协议模式（`all` / `g10_*` / 多个协议名）
  - 覆盖参数：`NUM_TAGS/TOTAL_RUNS/PRICE_MIN/PRICE_MAX`
- 点击 `运行批量测试`，实时查看日志

#### 模块5：实验自动化（重点出图流程）

- 页签：`实验自动化 (Experiments)`
- 分 3 个子模块：
  - 模块A `鲁棒性测试`：配置协议、因素、采样点、输出CSV后运行
  - 模块B `鲁棒性绘图`：选择 CSV 与因素（dominance/scale/density）出图
  - 模块C `进化图绘制`：选择场景与图类型（performance/time/accuracy/correction）出图
- 所有命令以非缓冲模式运行，日志实时刷新

#### 模块6：结果中心（快速找图）

- 页签：`结果中心`
- 常用操作：
  - `刷新结果列表` 扫描 CSV/PDF/PNG/JPG/TXT/JSON
  - 双击列表项直接打开文件
  - `打开 Experiments 目录` 快速查看全部图表输出

#### 快速出图最短路径（GUI内）

1. 在 `实验自动化` 模块A 运行鲁棒性测试（生成 CSV）
2. 在 模块B 勾选因素并点击生成鲁棒性图
3. 在 模块C 选择场景+图类并点击生成进化图
4. 去 `结果中心` 双击打开图表

### 3.7 云边端协同演示（新增）

新增目录： [cloud_edge_demo/](cloud_edge_demo/)

用途：

- `run_demo.py`：运行一个最小可执行的云边端协同演示。
- `pipeline.py`：实现终端采集、边缘推理、云端流式窗口汇总、历史批处理汇总，以及消息签名校验。

演示内容：

- **端侧**：模拟多个 RFID 读写器采集标签与环境数据（温湿度）。
- **边侧**：多个边缘网关并行调用现有协议（如 `g2_2`）进行热销价格识别。
- **云侧**：对边缘上传结果做滑动窗口统计（流处理）和全局汇总（一般处理）。
- **安全**：端到边、边到云均做 HMAC-SHA256 签名校验，便于在课程报告中说明“基础通信安全设计”。

最小运行命令：

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code_add"
python cloud_edge_demo\run_demo.py --scene multi_mode --protocol g2_2 --devices 2 --gateways 2 --batches 2 --num-tags 60 --stream-window 3
```

- ✅ 已验证：可输出流式处理日志与最终批处理汇总。
- 输出重点：
  - `流式处理输出`：展示每个批次从终端到边缘再到云端的实时识别结果。
  - `一般处理汇总`：展示全局热点价格、网关平均延迟、命中率和告警信息。

推荐作为课程项目原型的应用场景：

- 智慧仓储中的高频货品识别与环境联动告警
- 智慧零售中的热销价格带统计与补货建议

### 3.8 AI 协议自优化（新增）

新增文件：

- [ai_protocol_optimizer.py](ai_protocol_optimizer.py)：通过 OpenAI 兼容 API 自动生成、保存并自修复协议。
- [backend_tools/openai_compatible.py](backend_tools/openai_compatible.py)：通用 OpenAI 兼容接口客户端，支持 `/v1/chat/completions` 与 `/v1/responses`。

用途：

- 根据两个父代协议自动构造提示词。
- 调用兼容大模型 API 生成新的子代协议。
- 自动保存到指定场景的新世代目录中。
- 自动执行约束验证与一次烟囱测试；失败时可按错误报告再次修复。

安全约定：

- **不要把 API Key 写进代码文件。**
- CLI/GUI 都优先从环境变量读取 Key；GUI 也支持“仅本次运行”的临时 Key 输入，但不会写入文件。

命令行 dry-run（不调用 API，仅检查提示词与目标路径）：

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code_add"
python ai_protocol_optimizer.py --scene multi_mode --parent-a g2_1 --parent-b g2_2 --protocol-name g11_ai_demo --dry-run
```

- ✅ 已验证：可正确生成提示词文件，并推断输出到 `multi_mode/generation11/`。

命令行实际调用模板：

```powershell
$env:GEEK_API_KEY="你的中转站Key"
python ai_protocol_optimizer.py --scene multi_mode --parent-a g2_1 --parent-b g2_2 --protocol-name g11_ai_demo --base-url "你的兼容接口Base URL" --model "你的模型名" --endpoint-path "/v1/chat/completions"
```

GUI 入口：

- 打开 [protocol_evolution_gui.py](protocol_evolution_gui.py)
- 进入 `提示词生成` 页签下半部分的 `AI 自优化协议生成`
- 填写父代A、父代B、输出协议名、Base URL、Model
- 若不想提前设置环境变量，可在 `临时 Key` 中只输入本次运行的密钥
- 点击 `运行 AI 自优化` 即可实时查看输出
- 如果不确定模型名，可先点 `列出可用模型`

---

## 4. 常见问题（只列和启动相关的）

- `ModuleNotFoundError: No module named 'base'`
  - 解决：确保从项目根目录运行（含 base.py）。

- `pandas ... numexpr version ...` 警告
  - 不影响运行；想消除可升级：

```powershell
python -m pip install -U "numexpr>=2.10.2"
```

---

## 5. 复现实验的最短指令清单（Experiments 自动化）

只关心 Experiments 的话，最短闭环是：

```powershell
cd "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code"
python Experiments\Test_robust.py --protocols g10_4:single_mode --factors density --num-tags 2000 --total-runs 1 --point-count 3 --output Experiments\sensitivity_results_gui_smoke.csv
python Experiments\plot_robust.py --csv Experiments\sensitivity_results_gui_smoke.csv --factors density --output-dir Experiments --no-show
$env:MPLBACKEND="Agg"; python Experiments\plot_evolution_results.py --scenario single_peak --charts correction --output-dir Experiments --no-show
```
