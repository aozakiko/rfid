# RFID Lab · 协议自优化平台操作指南

RFID Lab 用于管理 RFID 场景和协议，通过大模型生成候选协议，再完成验证、迭代和实验分析。应用源代码位于 [`code_add/`](code_add/)，本文件是仓库首页的图文操作指南。

![RFID Lab 平台总览](docs/screenshots/01-overview.png)

## 1. 下载与启动

本次验证环境为 Windows、Python 3.13.5 和 Microsoft Edge。Web 服务、文件预览和模型 HTTP 客户端使用 Python 标准库；实验分析使用 NumPy、pandas 和 Matplotlib。前端无需 npm 构建。

首次下载，在 PowerShell 中执行：

```powershell
git clone https://github.com/aozakiko/rfid.git
Set-Location .\rfid

# 在仓库根目录创建虚拟环境，不需要修改 PowerShell 的脚本执行策略。
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install numpy pandas matplotlib

# 启动命令需要在 code_add 目录运行。
Set-Location .\code_add
..\.venv\Scripts\python.exe -m course_project.web_app --host 127.0.0.1 --port 8765
```

浏览器打开 **<http://127.0.0.1:8765>**。停止服务时，在运行服务的终端按 `Ctrl+C`。

如果当前 Python 环境已安装实验依赖，也可以直接执行：

```powershell
Set-Location "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid\code_add"
python -m course_project.web_app --host 127.0.0.1 --port 8765
```

截图取自实际页面。为便于演示，截图使用独立的临时审计数据库，模型连接参数留空；AI 生成与自动迭代使用预览模式，没有发起模型调用。截图中的本机绝对路径统一简写为 `code_add`。运行时的数量、日期和结果随本地资产变化。

## 2. 建议先走通的流程

1. 打开“场景与代码”，选择 `test` 场景，阅读 `ModeQuerySEM`、`ModeQueryTKQ` 等父代协议。
2. 在“Prompt 与 AI”选择上述两个父代，点击“预览生成任务”，检查生成的 Prompt。
3. 在“验证沙箱”选择 `ModeQuerySEM`，设置 60 个标签、2 次验证，查看检查结果。
4. 在“自动迭代”填入 `ModeQuerySEM, ModeQueryTKQ, g2_2`，勾选“只预览迭代任务，不调用模型”，先体验两轮迭代。
5. 在“实验产物”查看现有 CSV/JSON，在“实验分析”查看现有性能图。
6. 需要真实生成时，在“Prompt 与 AI”配置模型地址、模型名和 API Key，检查模型连接后再生成协议。

`test` 是场景目录名称；“任务画像”是统计目标。同一场景可以选择众数、均值、方差、分位数、基数或缺失盘点等任务画像。

## 3. 各页面操作

### 3.1 总览

查看场景数、协议数量、Prompt 数量、产物和任务统计；下方展示世代分布与场景资产。点击左侧导航进入各工作区，点击“使用指南”查看当前页面说明。

总览截图见本文件开头。

### 3.2 场景与代码

![场景选择、文件搜索和代码阅读](docs/screenshots/02-assets.png)

1. 选择已有场景；需要新场景时，填写合法名称并点击“创建新场景”。
2. 可以选择基线协议并复制到指定世代，例如 `generation_0`。
3. 用关键词搜索文件；列表支持每页 8、16、32 项和上一页/下一页。
4. 点击文件，右侧默认显示阅读预览。协议代码有行号和关键字高亮，长文件按页显示。
5. 点击“编辑源文件”修改内容，再点击“保存当前文件”或按 `Ctrl+S`。有未保存内容时，切换文件会提示确认。
6. 若保存提示文件已被其他操作修改，先重新读取并核对内容，再保存。

### 3.3 Prompt 与 AI

![单次协议生成、进度和 Prompt 预览](docs/screenshots/03-ai.png)

1. 选择场景、任务画像和父代 A/B，必要时补充业务需求。
2. 设置输出协议名、目标世代、修复轮数和验证标签数。
3. **预览生成任务**：生成 Prompt 和目标路径，不调用模型，不创建子代 `.py` 文件。
4. **生成协议代码**：实际调用配置的模型，生成代码并执行验证/自修复流程。
5. 下方查看后台任务进度、日志和历史记录；任务结束后会展示本次生成的 Prompt 或协议。

模型配置可以在页面填写，也可以在启动服务前设置环境变量：

```powershell
$env:AI_API_BASE_URL = "https://your-model-gateway.example"
$env:AI_API_MODEL = "your-model-name"
$env:AI_API_KEY = "your-api-key"
python -m course_project.web_app --host 127.0.0.1 --port 8765
```

以上是占位示例，需替换成实际服务参数。默认 Endpoint Path 为 `/v1/chat/completions`；API Key 环境变量字段应与所使用的变量名一致，例如 `AI_API_KEY`。密钥只填写在本机环境或临时输入框，不写入 Git 文档和代码。

### 3.4 验证沙箱

![协议检查、命中率和样本结果](docs/screenshots/04-validation.png)

选择场景、协议、任务画像，设置标签数量、重复次数、数值范围和误差容忍度后，点击“验证协议”。

结果包括硬约束检查、功能命中率、信息隔离检查和平均耗时；环图展示命中比例，样本卡片展示单次结果。展开“检查报告与原始结果”可进一步查看报告、样本和嵌套数据。

### 3.5 自动迭代

![父代池、迭代进度和逐轮结果](docs/screenshots/05-iteration.png)

1. 选择场景、任务画像、协议前缀、迭代轮数、验证标签数和修复轮数。
2. 填入逗号分隔的父代协议池，或使用“填入推荐父代池”。父代必须存在于当前可用资产中。
3. 初次使用先勾选“只预览迭代任务，不调用模型”，再点击“启动自动迭代”。
4. 查看阶段进度、运行日志和轮次表；通过“查看 Prompt / 查看协议”阅读对应资产。
5. 需要真实生成时，先完成模型配置，再取消预览勾选并启动任务。

预览模式下显示“未生成协议”是预期行为；此时可以查看 Prompt，但没有新的协议源文件。

### 3.6 实验产物

![CSV 表格、趋势图和资产下载](docs/screenshots/06-outputs.png)

左侧集中展示平台和各场景 `outputs/` 中的产物。搜索文件名、路径或类型，点击“预览”后在右侧查看：

| 类型 | 展示方式 |
| --- | --- |
| CSV | 每页 20 条记录、字段选择、当前页趋势图、可横向滚动的宽表 |
| JSON | 按需展开对象和数组，也可以切换为 JSON 源文 |
| Markdown / Prompt | 阅读排版和带行号文本，长文件支持翻页 |
| Python / 日志 / 文本 | 行号与分页，Python 提供关键字高亮 |
| PNG / JPG / WebP / SVG | 适应容器或查看原始尺寸 |
| HTML / PDF | 内嵌报告或文档预览 |
| 其他产物 | 提供原始文件下载 |

点击“下载文件”可保存原文件。CSV 图表只绘制当前页有效数值；超过 1 MB 的 JSON 使用分页文本，超过 10 MB 的文本通过下载查看。

### 3.7 实验分析

![实验参数与性能图预览](docs/screenshots/07-experiments.png)

上方设置来源场景、协议、任务画像和采样参数，可以运行鲁棒性实验、生成鲁棒性图、进化图或终稿图。实验结束后刷新产物列表，控制台显示命令、返回码和输出。

下方的实验资产列表支持搜索和分页；点击“本页预览”查看图表、CSV、JSON 或报告。`Experiments/` 下的嵌套产物也会被扫描。

### 3.8 审计

![模型配置状态与 AI 任务记录](docs/screenshots/08-audit.png)

查看 SQLite 中的运行统计、模型环境配置状态和最近 AI 任务记录。`预览` 表示只生成了 Prompt；实际生成任务需结合状态和协议文件判断结果。

审计数据在本机持久化，后台任务队列保存在服务进程内；重启服务后，原后台队列不能继续执行或轮询。

## 4. 手动上传本地文件

![本地文件导入入口](docs/screenshots/10-upload.png)

“场景与代码”“实验产物”“实验分析”页面顶部均有“导入本地文件”。展开后选择文件或拖放到上传区，点击“开始上传”。

| 页面 / 导入类型 | 保存位置 | 文件要求 |
| --- | --- | --- |
| 场景与代码 / 协议 | 当前场景指定的 `generation*` 目录 | `.py`，名称由英文字母、数字、下划线组成，不能以数字开头 |
| 场景与代码 / Prompt | 当前场景 `prompts/` | `.txt` |
| 场景与代码 / 场景输出 | 当前场景 `outputs/uploads/` | 支持的数据、文本、图片或报告 |
| 实验产物 | `code_add/course_project/outputs/uploads/` | CSV、JSON、MD、TXT、LOG、HTML、PDF、PNG、JPG、JPEG、WebP、SVG |
| 实验分析 | `code_add/Experiments/uploads/` | 同上 |

每个文件最多 **10 MB**，每批最多 **10 个**；文本需使用 **UTF-8** 编码，JSON 需符合格式。同名文件会拒绝覆盖，需重命名后重传。导入协议仅保存文件，后续运行由验证或实验操作触发。

## 5. 手机与窄屏

窄屏自动切换为单列工作区，顶部导航可横向滚动。宽数据表在自己的容器中滚动，文件列表和长文本通过分页查看。

<img src="docs/screenshots/09-mobile.png" alt="390 像素宽度下的实验产物页面" width="390" />

## 6. 项目文件架构

下图省略大部分历史协议和数据文件；`uploads/`、运行数据库等目录或文件按需生成。

```text
rfid/
├─ README.md                         # 本图文操作指南
├─ .gitignore                        # 忽略虚拟环境、缓存、数据库和本地密钥
├─ docs/
│  └─ screenshots/                   # 8 个页面及移动端、上传入口截图
├─ code_add/                         # 应用工作目录
│  ├─ README.md                      # 项目简介与启动说明
│  ├─ base.py                        # Tag / 协议基础接口
│  ├─ Validator.py                   # 物理约束等协议检查
│  ├─ Test.py                        # 命令行批量测试
│  ├─ prompt_generation.py           # 父代协议读取与 Prompt 构造
│  ├─ ai_protocol_optimizer.py       # 模型生成、修复与协议落盘
│  ├─ backend_tools/
│  │  ├─ openai_compatible.py        # 模型 HTTP 客户端
│  │  └─ protocol_metrics.py         # 协议指标归一化
│  ├─ course_project/
│  │  ├─ README.md                   # API、实现和运行约定
│  │  ├─ web_app.py                  # HTTP 路由与工作流编排
│  │  ├─ http_support.py             # 请求/响应边界与统一错误
│  │  ├─ validation.py               # 数值范围与布尔参数校验
│  │  ├─ jobs.py                     # 有界后台队列与进度快照
│  │  ├─ assets.py                   # 上传校验、有界文本与数据预览
│  │  ├─ database.py                 # SQLite 审计与任务记录
│  │  ├─ selfopt_ui.py               # HTML 导出兼容入口
│  │  ├─ test_course_project.py       # 核心工作流测试
│  │  ├─ test_infrastructure.py       # HTTP、上传、编辑与任务边界测试
│  │  ├─ static/
│  │  │  ├─ index.html               # 页面结构
│  │  │  ├─ app.css                  # 工作台视觉样式
│  │  │  ├─ app.js                   # 场景、生成、验证、实验交互
│  │  │  ├─ assets.css               # 资产预览和响应式细节
│  │  │  └─ asset_ui.js              # 分页、分类预览和上传组件
│  │  └─ outputs/                    # 平台运行产物与 uploads/
│  ├─ base_protocol/                # 可复用基础协议
│  ├─ test/                         # 推荐先体验的测试场景
│  │  ├─ Task.py                    # 场景标签生成
│  │  ├─ generation_0/              # 父代协议
│  │  ├─ generation_1/ ...          # 后续世代协议
│  │  ├─ prompts/                   # 生成或导入的 Prompt
│  │  └─ outputs/                   # 对比数据与运行结果
│  ├─ ManyTags/                     # 多标签场景
│  ├─ multi_mode/                   # 多众数场景
│  ├─ single_mode/                  # 单众数场景
│  ├─ rfid_mode_finding_v1/          # 历史场景
│  ├─ ss/                           # 历史场景
│  └─ Experiments/                  # 鲁棒性实验、绘图脚本与结果
├─ 周报1.pdf                         # 既有项目资料
└─ 用户手册3.docx                    # 既有项目资料
```

请求从 `web_app.py` 进入，经参数校验后调度资产、协议或实验逻辑；AI/迭代任务由 `jobs.py` 管理，历史写入 SQLite。前端通过 HTTP API 读取状态和资产，代码不需要打包编译。

更完整的接口和运行约定见 [开发说明](code_add/course_project/README.md)。当前服务面向本机实验使用，协议执行还没有容器级隔离。

## 7. 本地检查

在 `code_add` 目录运行：

```powershell
python -m unittest course_project.test_infrastructure course_project.test_course_project
```

使用上述虚拟环境时，将 `python` 替换为 `..\.venv\Scripts\python.exe`。本次 21 项测试通过，覆盖上传、预览分页、下载、文件冲突、参数验证、后台队列和核心工作流；浏览器检查还覆盖 8 个页面、5 种窗口宽度。

## 8. 如何连接 GitHub 并上传

### 8.1 Git、origin 和分支的关系

本地 `.git/` 保存版本历史及远端配置。`origin` 是远程仓库的本地别名；本地 `main` 是工作分支；`origin/main` 是最近一次获取到的远端分支状态。`git add` 选择本次提交内容，`git commit` 在本地保存版本，`git push` 才把提交上传到 GitHub。

当前工作副本原本已经设置了：

```text
origin  git@github.com:aozakiko/rfid.git
main    跟踪 origin/main
```

SSH 地址 `git@github.com:aozakiko/rfid.git` 与网页地址 <https://github.com/aozakiko/rfid> 指向同一仓库。本次沿用已有 SSH 连接，使用本机已有 SSH 凭据完成认证。

### 8.2 这份本地项目的检查与提交命令

```powershell
Set-Location "D:\HuaweiMoveData\Users\destiny\Desktop\大创\rfid"

# 确认仓库根目录、远端、分支和待提交文件。
git rev-parse --show-toplevel
git remote -v
git branch -vv
git status --short

# 获取远端最新状态，再检查本地/远端是否分叉。
git fetch origin
git log --oneline --left-right HEAD...origin/main

# 选择本次修改的代码、指南和截图。
git add README.md docs/screenshots code_add/README.md code_add/course_project
git diff --cached --stat
git diff --cached --check

# 建立本地提交，再上传到 main。
git commit -m "Improve RFID Lab UI, asset workflows, and screenshot guide"
git push origin main

# 上传后确认远端提交与本地一致，工作区没有遗留修改。
git rev-parse HEAD
git ls-remote origin refs/heads/main
git status -sb
```

`git add` 的范围决定上传哪些改动；本次范围包含已重构的前后端、测试、新操作指南和截图。`.gitignore` 排除本地虚拟环境、缓存、数据库、日志和环境变量文件。

### 8.3 在另一台电脑建立连接

直接 `git clone` 会同时下载历史、创建 `origin` 并设置分支跟踪，通常最方便：

```powershell
git clone https://github.com/aozakiko/rfid.git
Set-Location .\rfid
git remote -v
git branch -vv
```

如果需要把当前 SSH 连接切换为 HTTPS：

```powershell
git remote set-url origin https://github.com/aozakiko/rfid.git
git remote -v
git push origin main
```

HTTPS 上传使用本机 Git 凭据管理器/浏览器授权，或使用具有仓库写入权限的令牌；SSH 上传需要已在 GitHub 配置的 SSH 密钥。不要把令牌写进远端 URL 或提交文件。

对一个已执行过 `git init`、但尚无 `origin` 的本地仓库，才需要添加远端：

```powershell
git remote add origin https://github.com/aozakiko/rfid.git
git fetch origin
```

已有历史的远端应先获取并整合历史。当前项目已有远端和共同历史，无需重新运行 `git init` 或重复添加 `origin`。

### 8.4 之后的常规更新

工作区干净时，先同步远端，再修改文件并提交：

```powershell
git pull --ff-only origin main

# 修改项目文件后检查并选择提交内容。
git status --short
git add <本次修改的文件或目录>
git diff --cached
git commit -m "Describe this update"
git push origin main
```

`<本次修改的文件或目录>` 是需要替换的占位符。若 `pull --ff-only` 或 `push` 提示分支分叉，先查看差异并合并或 rebase，解决冲突后再推送。
