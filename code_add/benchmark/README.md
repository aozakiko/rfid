# 可复现实验入口

`benchmark/` 负责统一生成 seed、分布和规模，调用 `stat_protocols/` 中的基线，输出主实验、消融实验和通信/误差 Pareto 结果。这里不依赖 Web 手工填写，也不把模拟 CPU 时间当成真实硬件时延。

主实验包括 Polling、全识别、四个单任务协议、共享统计草图和 LLM 随机负基线；消融实验比较单任务串行采集与共享采集。失败候选保留在 `reports.json` 中，并在 `summary.csv` 里统计失败率。
