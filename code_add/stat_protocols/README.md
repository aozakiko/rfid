# 统计协议与通信成本模型

本目录是统计协议负责人的独立边界，向实验层只暴露四个冻结数据接口：`TaskSpec`、`ProtocolCandidate`、`RunResult`、`EvaluationReport`。

## 已实现协议

- `polling`：逐标签轮询，每个标签一个 round，作为高通信开销基线。
- `full_identification`：一次读取全部标签 ID/属性后本地计算，作为全识别基线。
- `single_mean`、`single_variance`、`single_cardinality`、`single_top_k`：单任务专用协议。
- `shared_sketch`：一次共享采集，同时复用 moments、bitmap/cardinality 和 bounded heavy-hitter 草图。
- `llm_random`：带可复现随机扰动的生成式负基线，失败和较差结果保留在报告中。

每次运行都返回统一 `RunResult`，包括真值、估计值、reader/tag bits、slots、frames、rounds、seed 和 CPU 参考时间。论文性能主指标使用通信 bits 与误差；CPU 时间只作为实现效率参考。

## 复现实验

```powershell
cd code_add
python -m benchmark.run_benchmark --mode main --seeds 0,1,2,3,4 --sizes 1000,10000 --distributions single_peak,multi_peak,gaussian,long_tail,uniform --output-dir Experiments/benchmarks/main
python -m benchmark.run_benchmark --mode ablation --seeds 0,1,2,3,4 --sizes 1000,10000 --output-dir Experiments/benchmarks/ablation
python -m benchmark.plot_results Experiments/benchmarks/main/main_reports.json --png Experiments/benchmarks/main/communication_error.png --pdf Experiments/benchmarks/main/communication_error.pdf
```

JSON 保留每个 seed、每个任务、每个协议的成功或失败行；CSV 是汇总表；Pareto JSON 只标记通信成本和误差上未被支配的候选。更换分布、规模或 seed 后，仍然由同一 `TaskSpec` 生成同一批输入，协议之间不会各自抽样。
