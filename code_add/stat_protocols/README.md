# 统计协议与可追溯通信成本

此目录对应分工方案 §5.3 与 W1–W10 中刘滕飞负责的算法部分。Web 展示见 [统计基准](http://127.0.0.1:8765/#statistics)，完整验收清单见 [任务对照](../../docs/刘滕飞任务逐项验收.md)。

## 实现

| 文件 | 职责 |
| --- | --- |
| `contracts.py` | TaskSpec / ProtocolCandidate / RunResult / EvaluationReport 与 CostModel；校验类型、值域、随机 seed、通信计数 |
| `channel.py` | 标签侧一位匿名 OR 槽模拟；逐帧产生占用观测及通信事件计数 |
| `sketches.py` | 由占用频次共享计算均值、总体方差、在场基数、Top-k；边界不确定性启发式 |
| `protocols.py` | 轮询、全识别、单任务、单任务串联、共享、固定/自适应复核、随机参数对照、真实 LLM 候选适配 |
| `genotype.py` | 面向统计模块的有限白名单编译接口，拒绝任意代码及非法轮数/帧参数 |

`run_many(tags, spec, channel)` **只采集一次**，输出各任务的 RunResult。每一任务行记录同一个 collection_id 和 bundle_cost；不能把同一次采集的四行费用再相加。single_serial 才逐任务独立收费。

## 测量与统计语义

标签根据自己的公开 ID 哈希到所属属性值的匿名槽位，同槽响应按 OR 聚合；读写器只接收占用观测。对每个值的空槽数 Z，以 `log(Z/m) / log(1-1/m)` 估计群体计数。共享直方图支持四任务；不再用“读取全部属性得到精确真值，再套上低费用公式”作为共享算法。

基数指在场标签数量，不是不同属性值数量；方差为总体方差；Top-k 在并列时按数值升序确定恰好 k 个真值。缺失场景评价在场集合。均匀分布和并列边界难以识别、长尾热点可能饱和，均保留误差和饱和轨迹。

`reader_bits` 为每次广播 160 bits，`tag_bits` 为每标签每帧一位实际发射量（包括碰撞贡献），`slots` 包含全部预留空槽，`frames`/`rounds` 按测量事件累加。字段语义及假设见 [实验规格](../../docs/统计实验规格v1.md)。

这是整数值域下的软件通道，不是 EPC C1G2 硬件实现。匿名直方图不等于差分隐私或信息隔离证明。全识别采用无碰撞乐观下界；CPU 时间受仿真缓存影响，仅供诊断。物理约束与运行时隔离需要和严枭验证模块对接。

## 真实 LLM 对照

`random_sketch_control` 仅随机选择帧参数，明确不称作 LLM 生成。

`llm_random` 必须提供真实候选记录（模型、Prompt hash、响应 hash、白名单基因）。没有记录时输出 `missing_real_llm_candidate`，不伪造结果，不参与 Pareto。Web 可复用模型配置生成一次候选，或导入记录；失败响应也保存为负结果。这个对照研究受限统计算子空间中的单次生成，不等同自由 Python 交叉或陈涛负责的 H3 演化研究。

## 复现

```powershell
cd code_add
python -m benchmark.suite --output-dir Experiments/stat_protocols
```

正式规格为 20 个独立 seed、5 分布、3 规模，另有复合任务、固定/自适应复核、阈值敏感性、缺失/噪声/极端值和独立盲测。默认不调用外部 API。
