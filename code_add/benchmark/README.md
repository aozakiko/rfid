# 可复现统计协议实验

```powershell
cd code_add
python -m benchmark.suite --output-dir Experiments/stat_protocols
# 带真实模型候选的正式实验：
python -m benchmark.suite --llm-candidate Experiments/stat_protocols/llm/<候选ID>/candidate.json --output-dir Experiments/stat_protocols
# 开发烟测，不替代正式结论：
python -m benchmark.suite --seed-count 2 --sizes 1000 --output-dir Experiments/stat_protocols/smoke
```

| 实验集 | 目的 | 默认规格 |
| --- | --- | --- |
| main | Polling、全识别、4 个专用协议、串联、共享、随机参数与真实 LLM 基线 | 20 seeds × 5 分布 × 3 规模 |
| ablation | 去共享、无复核、固定2/3/4轮、自适应复核 | 同上 |
| composite | H1：均值+方差+在场基数一次采集 vs 三次采集 | 同上 |
| sensitivity | H2：自适应阈值0.5/1/2与固定/无复核 | 同上 |
| robustness | 缺失0.1/0.3、噪声0.1、极端值0.1 | 20 seeds × 2 分布 × 3 规模 × 4 扰动 |
| blind | 参数冻结后的独立组合盲测 | seed100–119 × 2分布 × 3规模 |

每个目录包含 `*_reports.json`（逐 seed 原始结果）、`*_summary.csv`（跨场景描述性汇总）、`*_cases.csv`（单场景 seed 统计与95% CI）、`*_pareto.json`。顶层输出 manifest、机制基因索引、H1/H2逐条件验收、自动算法报告、Web聚合数据。

完整输入尺寸由同一个 TaskSpec 生成。数据 SHA-256、来源代码 SHA-256、随机种子、缺失/噪声/极端值参数、实际在场标签数量都可追溯。每协议的测量调用可复用仿真缓存以减少计算，通信费用仍逐事件独立计量，缓存不会减免费用。

执行失败和精度不达标分开统计；不支持的专用任务不会被当成运行失败。真实 LLM 未接入则对应失败行保留。Pareto **只在同一个任务内**比较成功协议，Web 还要求相同分布、规模和扰动；执行失败不能以0通信量进入前沿。

CPU只是诊断。通信bits、slots、frames、rounds 与误差、F1并列报告；模拟成本不能写成真实时延。正式结果中的条件未达 H1/H2 门槛时，保留并解释，不强行写出统一优势。

Web 的“一键重跑”在独立子进程执行，有30分钟超时和单任务互斥；完整套件成功后才发布新结果，避免页面读取半成品。
