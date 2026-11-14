# RFID 协议自优化研究平台 · 应用目录

项目名称为“基于大模型的RFID射频识别通讯协议自优化研究平台开发”。团队与项目信息、11 个 Vue 页面截图、完整安装步骤和实验边界以[仓库首页](../README.md)为准。

## 构建与启动

首次安装在仓库根目录执行 `python -m venv .venv` 和 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。然后构建新版前端：

```powershell
# 仓库根目录
Set-Location .\code_add\course_project\frontend
npm ci
npm run build
Set-Location ..\..
..\.venv\Scripts\python.exe -m course_project.web_app --host 127.0.0.1 --port 8765
```

Node.js 版本要求为 20.x ≥ 20.19，或 ≥ 22.12。浏览器访问 `http://127.0.0.1:8765`，`Ctrl+C` 停止服务；缺少前端构建时根路径兼容显示旧版。新版路由如 `/#/workspaces`、`/#/research`、`/#/statistics`，旧版入口为 `/legacy`。

## 应用边界

| 模块 | 用途 |
| --- | --- |
| `course_project/frontend/` | Vue 页面、公共组件、状态管理和浏览器测试 |
| `course_project/web_app.py` | HTTP 路由与工作流 |
| `course_project/workspace_service.py` | 配置版本、初始基线及研究运行记录 |
| `course_project/research_service.py` | 候选冻结、跨场景验证 / 盲测 |
| `course_project/evolution_selection.py` | 精度约束、Pareto 分层、拥挤度与结构去重 |
| `generic_tasks/` | 任务画像、真值与语义范围 |
| `generic_protocol/` | 协议适配、平台计量通道和受限子进程 |
| `stat_protocols/` | 四任务共享草图、复核策略和基线 |
| `benchmark/` | 配置实验、完整套件、独立拆分与审计 |
| `backend_tools/` | 模型 HTTP 客户端及指标归一化 |
| `base_protocol/`、`test/`、`multi_mode/` 等 | 基线、场景、世代协议和 Prompt |
| `Experiments/` | 参考归档、原始数据及绘图 |

## 模型与自定义任务

在“协议设计”填写 URL、Key 和模型名，不需要模型环境变量或接口路径，修改后下一次提交生效。预览不调用模型；真实生成和自动迭代使用当前提交配置。Key 不写入浏览器持久存储或公开任务记录，刷新页面后重新填写。

选择“任意自定义任务”并填写输入、目标、返回格式与约束；说明会传入 Prompt。无专用真值和评价器时仅检查执行与输出契约，不能将其标记为已验证业务准确率。严格子进程属于软件受控环境，不等同完整操作系统沙箱。

## 验证与复现

以下 Python 命令在本目录执行：

```powershell
python -m tools.verify
python -m benchmark.run_config --config benchmark/configs/smoke.json --output-dir Experiments/smoke_new
python -m benchmark.suite --output-dir Experiments/reproduction_20261114 --seed-count 20 --sizes 1000,10000,100000 --llm-candidate Experiments/stat_protocols/llm/290537f3a8314c0b933ce4063b95f79f/candidate.json
python -m benchmark.audit Experiments/reproduction_20261114
```

当前记录为 190 项 Python 测试、9 项前端单元测试、15 项浏览器用例；环境、边界和正式复跑摘要见[验证文件](../docs/verification/2026-11-14.json)。新输出写入独立目录，保留参考归档与一次真实模型调用记录。

[平台 API 与安全模式](course_project/README.md) · [前端构建和测试](course_project/frontend/README.md) · [统计协议](stat_protocols/README.md) · [基准工程](benchmark/README.md) · [融合版结题报告](../docs/reports/README.md)
