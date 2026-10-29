"""One command regenerates Liu Tengfei's protocol/experiment deliverable.

python -m benchmark.suite --output-dir Experiments/stat_protocols
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean

from stat_protocols.contracts import CostModel, TaskSpec
from .experiments import MAIN_PROTOCOLS, ABLATION_PROTOCOLS, SENSITIVITY_PROTOCOLS, pareto_front
from .runner import run_benchmark, write_results, summarize_cases, summarize_reports, mean_ci

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0"


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def save_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["protocol"])
        writer.writeheader()
        writer.writerows(rows)


def source_inventory():
    mechanisms = [
        ("逐值精确计数与静默", "base_protocol/ModeQueryEE.py", "while current_price_tags_dict", "逐价精确计数；很慢；作为历史机制，未将原自报时间直接带入新基准"),
        ("全识别", "base_protocol/ModeQueryID.py", "class ModeQueryID", "保留全识别思路；新基准明确采用无碰撞的乐观下界"),
        ("SEM 位平面并行与零槽估计", "base_protocol/ModeQuerySEM.py", "num_zeros =", "复用匿名占用位图和零槽反演；新通道逐事件记录真实发射 bits 与预留 slots"),
        ("碰撞/占用利用", "base_protocol/ModeQueryTCS.py", "num_non_empty_slots =", "将非空槽排序提升为有限帧计数反演；饱和继续作为负结果"),
        ("SOG 与多轮融合", "base_protocol/ModeQueryTKQ.py", "num_rounds =", "保留多轮独立测量的思想；未声称已实现 SOG 硬件"),
        ("早停、置信差与候选复核", "test/generation_3/test_g3_1.py", "def _select_conservative_candidates", "复用不确定性驱动思想；当前采用全帧复核，定向单值复核留作扩展"),
    ]
    result = []
    for title, relative, marker, decision in mechanisms:
        path = ROOT / relative
        content = path.read_text(encoding="utf-8")
        line = next((i for i, text in enumerate(content.splitlines(), 1) if marker in text), 1)
        result.append(dict(title=title, path=relative, line=line, source_hash=hashlib.sha256(path.read_bytes()).hexdigest(), decision=decision))
    try:
        tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT.parent, text=True, encoding="utf-8").splitlines())
    except (OSError, subprocess.CalledProcessError):
        tracked = set()
    generation_files = []
    for path in sorted(ROOT.glob("*/generation*/*.py")):
        if path.name != "__init__.py":
            source = path.read_text(encoding="utf-8", errors="replace")
            markers = [word for word in ("bitmap", "num_zeros", "early", "verify", "confidence", "SOG", "Frame", "simple_hash") if word.lower() in source.lower()]
            generation_files.append(dict(path=path.relative_to(ROOT).as_posix(), source_hash=hashlib.sha256(path.read_bytes()).hexdigest(), markers=markers, tracked=f"code_add/{path.relative_to(ROOT).as_posix()}" in tracked))
    return {"mechanisms": result, "generation_files": generation_files, "generation_count": len(generation_files), "interpretation": "markers are indexing aids, not proof of correctness or privacy"}


def hypothesis_results(report_sets):
    h1, h2 = [], []
    grouped = defaultdict(list)
    for report in report_sets.get("composite", []):
        m = report.run_result.metadata
        grouped[(m["distribution"], m["num_tags"], report.task)].append(report)
    for (distribution, size, task), values in sorted(grouped.items()):
        shared = {r.seed: r for r in values if r.protocol == "shared_sketch" and r.status == "success"}
        serial = {r.seed: r for r in values if r.protocol == "single_serial" and r.status == "success"}
        seeds = sorted(shared.keys() & serial.keys())
        if not seeds:
            continue
        savings = [1 - shared[s].communication_bits / serial[s].communication_bits for s in seeds]
        errors = [shared[s].relative_error for s in seeds]
        low, high = mean_ci(errors)
        threshold = .1 if task == "variance" else .05
        h1.append(dict(distribution=distribution, num_tags=size, task=task, seeds=len(seeds), communication_saving=mean(savings), mean_error=mean(errors), ci95_low=low, ci95_high=high, target_passed=mean(savings) >= .15 and high <= threshold))
    grouped.clear()
    for report in report_sets.get("sensitivity", []):
        if report.task == "top_k":
            m = report.run_result.metadata
            grouped[(m["distribution"], m["num_tags"])].append(report)
    for (distribution, size), values in sorted(grouped.items()):
        fixed = {r.seed: r for r in values if r.protocol == "fixed_4" and r.status == "success"}
        for strategy in ("shared_sketch", "fixed_2", "fixed_3", "adaptive_t05", "adaptive", "adaptive_t20"):
            other = {r.seed: r for r in values if r.protocol == strategy and r.status == "success"}
            seeds = sorted(fixed.keys() & other.keys())
            if not seeds:
                continue
            loss = [fixed[s].f1 - other[s].f1 for s in seeds]
            savings = [1 - other[s].communication_bits / fixed[s].communication_bits for s in seeds]
            low, high = mean_ci(loss)
            h2.append(dict(distribution=distribution, num_tags=size, protocol=strategy, seeds=len(seeds), f1=mean(other[s].f1 for s in seeds), f1_loss=mean(loss), loss_ci95_low=low, loss_ci95_high=high, communication_saving=mean(savings), target_passed=high <= .01 and mean(savings) >= .2))
    return {"H1": h1, "H2": h2, "H3": {"status": "out_of_scope", "reason": "演化选择、交叉与验证反馈修复不能用本统计基准的随机参数对照代替"}}


def make_dashboard(report_sets, manifest, hypotheses, inventory):
    modes = {}
    for mode, reports in report_sets.items():
        summaries = summarize_cases(reports)
        mode_path = Path(manifest["output_dir"]) / mode
        modes[mode] = {"rows": summaries, "grouped": True, "artifacts": [dict(name=p.name, path=p.relative_to(ROOT).as_posix(), size=p.stat().st_size, kind=p.suffix[1:]) for p in sorted(mode_path.glob("*")) if p.is_file()]}
    return {"available": bool(modes), "schema_version": VERSION, "modes": modes, "manifest": manifest, "hypotheses": hypotheses, "inventory": inventory}


def make_figures(output, report_sets):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .runner import summarize_reports
    colors = plt.get_cmap("tab20")
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for ax, task in zip(axes.flat, ("mean", "variance", "cardinality", "top_k")):
        summaries = [r for r in summarize_reports(report_sets["main"]) if r["task"] == task and r["success_rate"] == 1]
        for i, row in enumerate(summaries):
            ax.scatter(row["mean_communication_bits"], row["mean_error"], color=colors(i), label=row["protocol"], s=40)
        ax.set(xscale="log", title=task, xlabel="Transmitted reader + tag bits", ylabel="1 - F1" if task == "top_k" else "Relative error")
        ax.grid(alpha=.2)
        ax.legend(fontsize=6)
    for extension in ("png", "pdf"):
        fig.savefig(output / "main" / f"communication_error.{extension}", dpi=180)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for ax, metric in zip(axes, ("mean_communication_bits", "mean_slots")):
        rows = [r for r in summarize_reports(report_sets["composite"]) if r["task"] == "mean"]
        ax.bar([r["protocol"] for r in rows], [r[metric] for r in rows], color=["#148b82", "#356ce8"])
        ax.set(title="Mean + variance + cardinality collection", ylabel=metric)
    for ext in ("png", "pdf"):
        fig.savefig(output / "composite" / f"sharing_comparison.{ext}", dpi=180)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    rows = [r for r in summarize_reports(report_sets["sensitivity"]) if r["task"] == "top_k"]
    for ax, metric in zip(axes, ("mean_rounds", "mean_f1")):
        ax.bar([r["protocol"] for r in rows], [r[metric] for r in rows], color="#148b82")
        ax.set(ylabel=metric, title="Fixed/adaptive review & threshold sensitivity")
        ax.tick_params(axis="x", rotation=35)
    for ext in ("png", "pdf"):
        fig.savefig(output / "sensitivity" / f"review_sensitivity.{ext}", dpi=180)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    summaries = summarize_cases(report_sets["robustness"])
    for task, ax in zip(("mean", "top_k"), axes):
        for protocol in ("shared_sketch", "adaptive"):
            rows = [r for r in summaries if r["task"] == task and r["protocol"] == protocol]
            grouped = defaultdict(list)
            for r in rows:
                label = f"m{r['missing_rate']}/n{r['noise_rate']}/e{r['extreme_rate']}"
                grouped[label].append(r["mean_error"])
            ax.plot(list(grouped), [mean(v) for v in grouped.values()], "o-", label=protocol)
        ax.set(title=task, ylabel="Relative error" if task == "mean" else "1-F1")
        ax.tick_params(axis="x", rotation=30)
        ax.legend()
    for ext in ("png", "pdf"):
        fig.savefig(output / "robustness" / f"robustness.{ext}", dpi=180)
    plt.close(fig)
    # Retain expected per-mode figure names used by the Web asset gallery.
    for mode in ("ablation", "blind"):
        from .plot_results import plot_summary
        plot_summary(output / mode / f"{mode}_reports.json", output / mode / "communication_error.png", output / mode / "communication_error.pdf")


def write_report(output, manifest, hypotheses, inventory):
    passed_h1 = sum(r["target_passed"] for r in hypotheses["H1"])
    adaptive_h2 = [r for r in hypotheses["H2"] if r["protocol"] == "adaptive"]
    record = manifest.get("llm_record")
    llm_evidence = "尚未提供实际模型记录；保留 missing_real_llm_candidate，不填造模型效果。"
    if record:
        llm_evidence = f"模型 `{record.get('model')}`，候选 `{record.get('candidate_id')}`，来源 `{record.get('provenance')}`。\n\n"
        llm_evidence += f"Prompt SHA-256：`{record.get('prompt_hash')}`；响应 SHA-256：`{record.get('response_hash')}`。\n\n"
        usage = record.get("usage") or {}
        token_usage = {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens") if k in usage}
        llm_evidence += f"候选参数：`{json.dumps(record.get('genotype'), ensure_ascii=False)}`。网关返回用量：`{json.dumps(token_usage, ensure_ascii=False)}`。\n\n"
        llm_evidence += "仅生成一个候选，在所有场景冻结参数；20个seed衡量数据变化，不代表20次独立模型生成。受限算子基线不等同自由Python协议搜索。跨场景均值仅作描述，单场景CI见main_cases.csv。\n\n"
        llm_evidence += "| 任务 | 执行成功/总数 | 质量达标率 | 平均误差（Top-k为1−F1） | 平均发射bits |\n| --- | --- | --- | --- | --- |\n"
        for row in manifest.get("llm_summary", []):
            error = f"{row['mean_error']:.6f}" if row['mean_error'] is not None else "不可用"
            llm_evidence += f"| {row['task']} | {row['runs'] - row['failed_runs']}/{row['runs']} | {row['quality_pass_rate']:.2%} | {error} | {row['mean_communication_bits']:.1f} |\n"
    text = f'''# 统计实验报告

实验版本：{VERSION}。本报告由 `python -m benchmark.suite` 自动生成，原始结果及每个 seed 均可追溯。

## 方法

标签侧按公开整数值域和稳定 ID 哈希，将一位响应映射到 (value, slot)；同槽碰撞做 OR。
读写器仅接收占用向量，以有限帧零槽反演估计各值频次，再复用直方图计算均值、总体方差、在场标签数和 Top-k。
匿名指“不携带标签 ID”，直方图仍暴露总体属性分布，不能等同差分隐私或安全性证明。
基数定义为在场标签数量；不能解读为不同 price 数量。缺失实验真值也取在场集合。
共享实现通过一次 run_many 采集输出多个 RunResult；各任务行共享 collection_id 和 bundle_cost，不重复相加通信费用。

固定复核执行 2/3/4 次独立哈希帧；无复核执行 1 帧；自适应方法用 Top-k 边界的估计频次差除以 delta-method 不确定性，超过阈值即早停，最多 4 帧。
0.5/1/2 三个阈值在敏感性实验中并列报告。该置信差是启发式，不宣称概率覆盖保证。

## 成本与基线

每个匿名测量帧广播 160 bits；每个响应标签实际发射 1 bit；全部空闲和占用槽都计入 slots，frames/rounds 逐事件累加。
因此 transmitted bits 的优势可能被大量空槽抵消，必须同时查看 slots。CostModel 的系数只产生仿真成本，绝不是真实链路时延。
Polling 使用 32-bit 寻址 + 32-bit 属性；全识别采用 160-bit 广播 + 每标签 32-bit ID 和 32-bit 属性、无碰撞调度，是明确标注的乐观下界。
单任务专用协议只支持自身任务；single_serial 将三个/四个单任务采集串联。不会把“不支持任务”算成算法失败。
random_sketch_control 是随机参数对照，**不是 LLM 生成**。llm_random 仅接收真实模型生成记录，经白名单统计算子编译。
若缺少模型/Prompt/响应证据，则保留 missing_real_llm_candidate 失败行，不伪造调用、token 或成功率。

## 真实模型基线证据

{llm_evidence}

## 正式实验规格

- 正式主实验、共享消融、固定/自适应复核和阈值敏感性：{len(manifest['seeds'])} 个独立 seed、5 个分布、1k/10k/100k 三个规模。
- 鲁棒性：缺失率 0.1/0.3、随机噪声 0.1、极端值 0.1；两类分布、三个规模。
- 盲测：独立 seed 100 起、未用于调参的缺失/噪声/极端值组合，参数冻结后直接评估。
- 每个单场景结果报告均值、Student-t 95% CI、P95、最坏值、失败数。跨场景平均只作描述，不冒充单场景置信区间。
- Top-k 使用完整 precision/recall/F1/Jaccard，估计子集不会记为满分。

## H1 / H2 观察值

H1 是 mean + variance + cardinality 的独立三任务实验；共享一次采集相对三个独立采集节省 2/3 的通信与 slots，属于当前模型下的复用结果，不能推导真实硬件节省比例。
在“通信节省至少 15%、误差 CI 上界满足 5%/10%/5%”口径下，{passed_h1}/{len(hypotheses['H1'])} 个分布×规模×任务条件通过。原始逐条件表见 hypotheses.json。

H2 使用逐 seed 配对的 F1 损失和通信节省。默认自适应阈值 1 相对 fixed_4，在“F1 损失 95% CI 上界 ≤ 1 个百分点、通信节省 ≥ 20%”下通过 {sum(r['target_passed'] for r in adaptive_h2)}/{len(adaptive_h2)} 个条件。
未达到门槛的条件保留，不作“显著更优”的统一结论。H3 的选择/修复实验属于独立演化实验；本报告提供统计编译和评价接口，不模拟其结果。

## 局限与最终集成边界

1. 软件 OR 通道不包含射频误码、捕获效应、同步、调制或 EPC C1G2 防碰撞过程；无硬件验证。
2. 均匀分布近似并列频次、密集长尾桶饱和会导致 Top-k 错误或标量偏差；报告均保留 saturation 和质量不达标结果。
3. 当前采用全帧复核；候选定向复核是后续扩展。整数值域已冻结为 1..1000，连续物理数据必须先声明量化规则。
4. LLM对照只覆盖一份冻结候选；模型生成随机性、不同提示词和多候选统计仍未评估。缺少实际记录时明确待实测。
5. 现有自由 Python 协议的成本未通过新通道插桩，不能凭其自报时间混入新 Pareto 图。
6. AST/运行时沙箱、信息隔离证明和演化选择需要独立模块验证；本统计实验不替代这些验证。

## 机制基因表

| 机制 | 源文件与行号 | 复用决策 |
| --- | --- | --- |
'''
    for row in inventory["mechanisms"]:
        text += f"| {row['title']} | `{row['path']}:{row['line']}` | {row['decision']} |\n"
    text += f"\n另已索引 {inventory['generation_count']} 个历史世代文件；完整 SHA-256 与机制关键词在 mechanism_inventory.json。tracked=false 为本地尚未提交的历史资产，不是随实验交付的执行依赖。关键词索引不等于正确性证明。\n"
    (output / "统计实验报告.md").write_text(text, encoding="utf-8")


def run_suite(output, seed_count=20, sizes=(1000, 10000, 100000), llm_candidate=None, progress=print):
    started = time.perf_counter()
    output = output.resolve()
    seeds = list(range(seed_count))
    distributions = ("single_peak", "multi_peak", "gaussian", "long_tail", "uniform")
    spec = TaskSpec(split="main")
    report_sets = {}
    plans = {
        "main": [(spec, seeds, distributions, MAIN_PROTOCOLS)],
        "ablation": [(spec, seeds, distributions, ABLATION_PROTOCOLS)],
        "composite": [(TaskSpec(tasks=("mean", "variance", "cardinality"), split="H1"), seeds, distributions, ("single_serial", "shared_sketch"))],
        "sensitivity": [(spec, seeds, distributions, SENSITIVITY_PROTOCOLS)],
        "robustness": [(TaskSpec(split="robustness", **rates), seeds, ("multi_peak", "long_tail"), ("full_identification", "single_serial", "shared_sketch", "fixed_4", "adaptive")) for rates in ({"missing_rate": .1}, {"missing_rate": .3}, {"noise_rate": .1}, {"extreme_rate": .1})],
        "blind": [(TaskSpec(split="blind", missing_rate=.2, noise_rate=.05, extreme_rate=.05), list(range(100, 100 + seed_count)), ("uniform", "long_tail"), ("full_identification", "shared_sketch", "adaptive"))],
    }
    for mode, cases in plans.items():
        progress(f"Running {mode} ({seed_count} seeds, sizes={sizes})")
        reports = []
        for task_spec, case_seeds, dist, protocols in cases:
            reports.extend(run_benchmark(task_spec, seeds=case_seeds, sizes=sizes, distributions=dist, protocol_names=protocols, llm_candidate=llm_candidate))
        report_sets[mode] = reports
        folder = output / mode
        folder.mkdir(parents=True, exist_ok=True)
        write_results(reports, folder / f"{mode}_reports.json", folder / f"{mode}_summary.csv")
        cases_summary = summarize_cases(reports)
        save_csv(folder / f"{mode}_cases.csv", cases_summary)
        save_json(folder / f"{mode}_pareto.json", pareto_front(reports))
    progress("Generating figures, hypothesis checks, provenance and delivery report")
    make_figures(output, report_sets)
    sources = sorted({*ROOT.glob("stat_protocols/*.py"), *ROOT.glob("benchmark/*.py")})
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    manifest = dict(schema_version=VERSION, seeds=seeds, blind_seeds=list(range(100, 100 + seed_count)), sizes=list(sizes), distributions=list(distributions), output_dir=str(output), revision=revision, python=sys.version, platform=platform.platform(), cost_model=CostModel().to_dict(), source_hashes={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}, llm_status="record_supplied" if llm_candidate else "not_measured_missing_candidate", elapsed_s=round(time.perf_counter() - started, 2), run_counts={m: len(r) for m, r in report_sets.items()})
    if llm_candidate:
        manifest["llm_record"] = {k: llm_candidate.get(k) for k in ("candidate_id", "model", "prompt_hash", "response_hash", "genotype", "provenance", "created_at", "usage", "successful_requests", "validation_error")}
        manifest["llm_summary"] = summarize_reports(r for r in report_sets["main"] if r.protocol == "llm_random")
    inventory = source_inventory()
    hypotheses = hypothesis_results(report_sets)
    save_json(output / "manifest.json", manifest)
    save_json(output / "mechanism_inventory.json", inventory)
    save_json(output / "hypotheses.json", hypotheses)
    write_report(output, manifest, hypotheses, inventory)
    dashboard = make_dashboard(report_sets, manifest, hypotheses, inventory)
    save_json(output / "dashboard.json", dashboard)
    progress(f"Completed in {manifest['elapsed_s']} s; reports={manifest['run_counts']}")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="Experiments/stat_protocols")
    parser.add_argument("--seed-count", type=int, default=20)
    parser.add_argument("--sizes", default="1000,10000,100000")
    parser.add_argument("--llm-candidate", help="real generated JSON record, never a synthetic replacement")
    args = parser.parse_args()
    if not 2 <= args.seed_count <= 100:
        parser.error("seed-count must be 2..100")
    record = json.loads(Path(args.llm_candidate).read_text(encoding="utf-8")) if args.llm_candidate else None
    run_suite(Path(args.output_dir), args.seed_count, tuple(int(s) for s in args.sizes.split(",")), record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
