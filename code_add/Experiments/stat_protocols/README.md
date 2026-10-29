# 统计实验交付目录

查看[算法与实验报告](统计实验报告.md)和[逐项验收](../../../docs/统计实验交付验收.md)。Web：<http://127.0.0.1:8765/#statistics>。

| 目录/文件 | 内容 |
| --- | --- |
| `main/` | 主实验、通信—误差分任务主图 |
| `ablation/` | 单任务/共享与固定/自适应复核消融 |
| `composite/` | H1均值+方差+在场基数三任务共享对比图 |
| `sensitivity/` | H2无复核、固定2/3/4帧与阈值0.5/1/2敏感性图 |
| `robustness/` | 缺失、噪声、极端值结果和图表 |
| `blind/` | 独立seed100–119的组合盲测 |
| `manifest.json` | 执行环境、Git起点、源文件SHA-256、规格与运行行数 |
| `mechanism_inventory.json` | 6类机制来源及历史世代索引；tracked=false标记本地未提交资产 |
| `hypotheses.json` | H1/H2逐条件门槛、配对统计与未达标结果；H3归演化模块 |
| `dashboard.json` | Web可视化用单场景聚合结果，不替代原始逐seed文件 |
| `llm/290537f3a8314c0b933ce4063b95f79f/` | Pixel真实模型单次生成的prompt.txt、response.txt、candidate.json；含hash和网关usage，无密钥 |

各子目录的 `*_cases.csv` 是单一分布/规模/扰动下的20seed统计，含95%CI、P95、worst、执行成功与精度达标率。`*_summary.csv` 是跨场景描述性汇总。逐seed原始数据和失败行位于 `*_reports.json`。

报告有些超过10MB，完整文件可下载；Web通过独立的详情接口分页显示每条结果，不需要在浏览器一次性展开整个JSON。所有图表提供PNG与PDF。

```powershell
cd code_add
python -m benchmark.suite --llm-candidate Experiments/stat_protocols/llm/290537f3a8314c0b933ce4063b95f79f/candidate.json --output-dir Experiments/stat_protocols
```

软件仿真不代表射频硬件实测。共享采集费用只记一次，不跨任务行重复求和；实际发射bits与所有预留slots都须报告。真实LLM缺少记录时明确待实测，不以随机参数替代。Web重跑结果按 `runs/<运行ID>/` 保存，成功后用latest.json发布。

本次已使用真实Pixel记录：模型gpt-6.1-sol，159输入+28输出=187 tokens。一份候选冻结后跨数据seed评价；复现上述命令不再调用外部模型、不需要API Key。网页刷新后保留该候选，可直接点击重跑；另行生成候选才需要配置网关。
