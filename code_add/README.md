# RFID 协议自优化 Web 平台

图文操作说明、全部页面截图、项目目录结构见 [仓库操作指南](../README.md)。

当前 `code_add` 已统一为“面向多维统计场景的 RFID 协议自优化 Web 平台”。项目主线不再是旧 GUI 或云边端演示，而是围绕 RFID 统计协议的自动生成、验证、迭代和实验分析形成闭环。

## 启动方式

在 `rfid/code_add` 目录运行：

```powershell
python -m course_project.web_app --host 127.0.0.1 --port 8765
```

浏览器访问：

```text
http://127.0.0.1:8765
```

## 核心流程

1. 在 Web 首页查看项目说明、场景资产、世代分布和平台状态。
2. 在“场景与代码”中查看 `Task.py`、`config.py`、各代协议和 Prompt。
3. 在“Prompt 与 AI”中选择任务画像和父代协议，生成或预览子代协议。
4. 在“验证沙箱”中对单个协议进行任务烟测、物理约束验证和信息隔离检查。
5. 在“自动迭代”中选择父代池，连续生成多代候选协议并实时查看进度。
6. 在“Outputs”和“Experiments”中查看 CSV、JSON、图表和实验结果。
7. 在“审计”中查看 SQLite 记录、AI 任务、系统事件和运行统计。

## 关键目录

- `course_project/`：Web 服务、前端界面、数据库、课程报告生成脚本和单元测试。
- `test/`：课程报告采用的可靠测试场景，包含 3 个父代、3 代共 9 个子代、统一测试结果和图表输入数据。
- `base_protocol/`：基础 RFID 协议库，可作为父代或对照协议。
- `multi_mode/`、`single_mode/`、`ManyTags/`、`ss/`：保留的历史协议资产，可作为父代池和后续扩展场景。
- `Experiments/`：鲁棒性实验、进化图、终稿图和统一对比图生成脚本。
- `backend_tools/`：OpenAI 兼容客户端与协议指标归一化工具。

## 关键文件

- `course_project/web_app.py`：Web API、资产读取、AI 生成、验证沙箱、自动迭代和实验调度。
- `course_project/selfopt_ui.py`：Web HTML 导出的兼容入口。
- `course_project/static/`：独立 HTML、CSS 与 JavaScript，包含响应式工作台、文件搜索和编辑器交互。
- `course_project/http_support.py`、`jobs.py`、`validation.py`：HTTP 边界、有界后台任务队列与参数校验。
- `ai_protocol_optimizer.py`：调用大模型完成协议交叉变异、自修复和落盘。
- `prompt_generation.py`：读取父代协议并生成交叉 Prompt。
- `Validator.py`：协议物理约束验证。
- `Test.py`：命令行协议批量测试器。

## 验证命令

```powershell
python -m py_compile course_project/web_app.py course_project/selfopt_ui.py course_project/test_course_project.py ai_protocol_optimizer.py prompt_generation.py Validator.py
python -m unittest course_project.test_course_project course_project.test_infrastructure -v
```

课程报告生成脚本：

```powershell
python course_project/build_iot_course_report_v2.py
```

输出文档位于大创目录根部：

```text
2023302131145-刘滕飞-课程报告.docx
```
## 任意自定义任务

在“Prompt 与 AI”“验证沙盒”“自动迭代”和“Experiments”页面的“任务画像”下拉框中选择“任意自定义任务”，填写自然语言任务说明。建议同时写清：输入属性、统计或决策目标、返回值格式、误差/置信度要求，以及通信资源约束。例如：`估计标签 price 的 90 分位数，返回 [estimated_p90]，误差不超过 5%`。

自定义说明会原样写入 AI Prompt，并随生成任务、迭代任务和实验记录保存。系统仍会检查 Python 协议接口、物理帧约束、执行是否成功以及 `estimated_values` 是否为列表；由于任意业务目标没有统一真值计算器，验证结果会标记为“执行与输出契约”，不会把它误算成众数、均值或固定精度指标。若需要业务准确率，请在 `Task.py` 中补充该任务的真值字段和对应验证逻辑。

## 统计协议与通信成本实验

刘滕飞负责的统计协议实现位于 [`stat_protocols/`](stat_protocols/README.md)，可复现实验位于 [`benchmark/`](benchmark/README.md)。统一 `RunResult` 会记录 reader/tag bits、slots、frames、rounds、seed、误差和失败原因；主实验、共享草图消融与通信/误差 Pareto 结果可由固定命令一键生成。

启动 Web 服务后，打开 [统计基准页](http://127.0.0.1:8765/#statistics) 可直接筛选并预览这些结果，页面同时提供与分工文档逐项对照的验收清单。

完整交付和协作边界见[刘滕飞任务逐项验收](../docs/刘滕飞任务逐项验收.md)，软件实验口径见[统计实验规格v1](../docs/统计实验规格v1.md)。整套正式实验用 `python -m benchmark.suite --output-dir Experiments/stat_protocols` 复现，默认不调用外部API；真实LLM候选生成/导入后可随套件重跑。
