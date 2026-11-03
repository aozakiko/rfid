# Vue 研究工作台

RFID 平台的 Vue 3 + TypeScript + Vite 前端。使用 Vue Router 管理九个功能页面，Pinia 共享场景、模型配置和后台任务状态，Element Plus 提供表单与表格，ECharts 绘制后端结果。Python 服务和协议、验证、实验接口继续使用现有实现。

## 开发

需要 Node.js 20.19+（20 系列）或 22.12+；本轮使用 Node.js 22.16.0。首次安装使用锁文件：

~~~powershell
# 从仓库根目录进入
Set-Location .\code_add\course_project\frontend
npm ci
npm run dev
~~~

开发页面为 <http://127.0.0.1:5173/ui/>，API 代理到 <http://127.0.0.1:8765>。另一个终端启动 Python：

~~~powershell
# 从仓库根目录进入
Set-Location .\code_add
..\.venv\Scripts\python.exe -m course_project.web_app --host 127.0.0.1 --port 8765
~~~

路由使用 hash，例如 /#/optimization，不需要配置服务端 history 回退。

## 构建与部署

~~~powershell
# 在 frontend 目录
npm run build
~~~

该命令先执行严格 TypeScript 检查，再生成 dist/。Python 服务会将新版提供在 <http://127.0.0.1:8765> 和 /ui/，静态资源位于 /ui/assets/。部署时需包含构建后的 dist/；运行时只需要 Python，无需运行 Node 服务。重新构建后刷新浏览器即可。

dist/、node_modules/、浏览器测试报告不进入 Git。新机器克隆后需先 npm ci 和 npm run build。未构建时根路径兼容显示旧工作台；/ui/ 会返回构建提示。旧工作台始终可通过 /legacy 访问。

## 页面和状态

| 页面 | 主要能力 |
| --- | --- |
| 研究总览 | 场景和任务概览、工作流入口 |
| 场景与代码 | 新建场景、复制基线、搜索、源码编辑、版本冲突保护、文件导入 |
| 协议设计 | 模型配置与探测、父代选择、任务画像、单次预览/生成和自修复 |
| 验证沙箱 | 严格/兼容验证、阶段证据、完整语义、候选公平比较、验证历史 |
| 自动迭代 | 父代池、通信优化、质量门槛、请求/时间预算、停止条件、独立验证和历史结果 |
| 实验分析 | 配置实验、验证/盲测流水线、报告重建、敏感度和演化绘图 |
| 统计基准 | 同场景筛选、后端 Pareto 资格、置信区间、逐 Seed 证据、基准运行和候选导入 |
| 实验产物 | 搜索、分页、CSV/JSON/文本/图片/HTML/PDF 预览和下载 |
| 运行审计 | 持久任务历史、停止/重试、候选谱系、数据库记录 |
| 候选评测 | 统一版本库、冻结源码、跨分布/规模/扰动验证与盲测、同场景对照和证据下载 |

模型密钥只存在当前页面的 Pinia 内存；路由切换共享，刷新即清空，不写入 localStorage/sessionStorage。模型请求仍交给 Python，不在前端直接连接模型。

服务接受的后台任务通过统一轮询器跟踪；切换页面不停止任务，刷新后从服务器历史恢复。网络暂时断开会继续重试；取消任务以服务端最终状态为准。

源码保存携带 revision。409 冲突时保留编辑缓冲，不覆盖磁盘；离开页面或切换文件会提示未保存内容。编辑区支持 Ctrl/⌘+S。文件导入集中在“场景与代码”，可选择平台产物、场景协议、Prompt、场景 Outputs 或 Experiments；沿用服务端 10 MB 和重名拒绝规则。

src/api.ts 统一请求与错误，src/types.ts 定义边界类型，src/stores/ 管理共享状态，src/components/ 管理任务、模型、证据和资产组件，src/views/ 按页拆分并延迟加载。统计资格、质量和通信成本由后端计算，前端负责呈现。

## 验证

~~~powershell
npm test
npm run build
npm run format:check
npx playwright install chromium
# 先启动 8765 上的 Python 服务，且已经构建新版
npm run test:e2e
~~~

可使用 RFID_TEST_URL 指定测试服务地址。12 项浏览器用例包含十个页面、真实严格验证、自动迭代预览、小规模配置实验、冻结候选跨页面验证/评测/下载/历史恢复、移动视口和结构化错误；冲突保存、停止任务和图片/分页边界使用模拟响应，避免改写已有源码或付费调用模型。真实预览会生成带 vue_browser_preview 前缀的 Prompt 和任务历史，评测会保存版本和独立运行目录，请在可用于开发测试的本地服务运行。完整 Python 检查在 code_add 中执行 python -m tools.verify。

“候选评测”从 /api/research/capabilities 读取类型与任务能力。Python 协议和统计 JSON 统一登记、分别运行原生评测引擎；页面之间传递冻结版本 ID，而非使用同名文件的最新内容。历史 Python 评测支持 /#/research?run=<运行ID>；统计流水线支持 /#/experiments?pipeline=<运行ID>。

本轮没有自动访问真实付费模型；真实生成和实验算法沿用现有后端。生产构建仍有组件/图表公共分包超过 Vite 的 500 KB 提示，后续可按实际加载性能进一步拆分。
