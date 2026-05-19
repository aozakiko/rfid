from __future__ import annotations

import html
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _fmt_modes(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _avg_gateway_latency(report: dict[str, Any]) -> float:
    values = [
        float(row.get("avg_protocol_time_ms", 0.0))
        for row in report.get("gateway_summary", {}).values()
    ]
    return round(mean(values), 2) if values else 0.0


def _metric_card(label: str, value: Any, tone: str) -> str:
    return f"""
    <section class="metric {tone}">
      <span>{_esc(label)}</span>
      <strong>{_esc(value)}</strong>
    </section>
    """


def _hot_price_rows(report: dict[str, Any]) -> str:
    hot_prices = report.get("global_hot_prices", [])
    max_count = max([int(item.get("count", 0)) for item in hot_prices] or [1])
    rows: list[str] = []
    for item in hot_prices:
        count = int(item.get("count", 0))
        width = max(8, int(count / max_count * 100))
        rows.append(
            f"""
            <tr>
              <td>{_esc(item.get("price"))}</td>
              <td><div class="bar"><span style="width:{width}%"></span></div></td>
              <td>{_esc(count)}</td>
            </tr>
            """
        )
    return "\n".join(rows) or "<tr><td colspan='3'>暂无热点价格</td></tr>"


def _gateway_rows(report: dict[str, Any]) -> str:
    rows: list[str] = []
    for gateway_id, item in report.get("gateway_summary", {}).items():
        rows.append(
            f"""
            <tr>
              <td>{_esc(gateway_id)}</td>
              <td>{_esc(item.get("windows"))}</td>
              <td>{_esc(item.get("avg_protocol_time_ms"))}</td>
              <td>{_esc(item.get("avg_frame_size"))}</td>
              <td>{_esc(item.get("hit_rate"))}</td>
            </tr>
            """
        )
    return "\n".join(rows) or "<tr><td colspan='5'>暂无网关数据</td></tr>"


def _stream_rows(result: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in result.get("stream_snapshots", [])[-24:]:
        rows.append(
            f"""
            <tr>
              <td>{_esc(item.get("latest_batch"))}</td>
              <td>{_esc(item.get("latest_gateway"))}</td>
              <td>{_esc(item.get("latest_device"))}</td>
              <td>{_esc(_fmt_modes(item.get("latest_modes", [])))}</td>
              <td>{_esc(item.get("recent_hit_rate"))}</td>
              <td>{_esc(item.get("recent_avg_protocol_time_ms"))}</td>
              <td>{_esc(item.get("recent_alert_count"))}</td>
            </tr>
            """
        )
    return "\n".join(rows) or "<tr><td colspan='7'>暂无流处理数据</td></tr>"


def _alert_rows(report: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in report.get("alerts", []):
        rows.append(
            f"""
            <tr>
              <td>{_esc(item.get("gateway_id"))}</td>
              <td>{_esc(item.get("device_id"))}</td>
              <td>{_esc(item.get("batch_index"))}</td>
              <td>{_esc(item.get("alert"))}</td>
            </tr>
            """
        )
    return "\n".join(rows) or "<tr><td colspan='4'>暂无异常告警</td></tr>"


def _benchmark_rows(rows: list[dict[str, Any]] | None) -> str:
    if not rows:
        return "<tr><td colspan='7'>未运行基准实验</td></tr>"
    rendered: list[str] = []
    for row in rows:
        rendered.append(
            f"""
            <tr>
              <td>{_esc(row.get("devices"))}</td>
              <td>{_esc(row.get("gateways"))}</td>
              <td>{_esc(row.get("batches"))}</td>
              <td>{_esc(row.get("num_tags"))}</td>
              <td>{_esc(row.get("average_hit_rate"))}</td>
              <td>{_esc(row.get("avg_gateway_protocol_time_ms"))}</td>
              <td>{_esc(row.get("wall_clock_s"))}</td>
            </tr>
            """
        )
    return "\n".join(rendered)


def generate_dashboard(
    result: dict[str, Any],
    output_path: Path,
    security_result: dict[str, Any] | None = None,
    benchmark_rows: list[dict[str, Any]] | None = None,
) -> Path:
    scenario = result["scenario"]
    report = result["batch_report"]
    security_label = "未运行"
    if security_result is not None:
        security_label = "通过" if security_result.get("passed") else "未通过"

    metrics = "".join(
        [
            _metric_card("终端读写器", scenario.get("device_count"), "teal"),
            _metric_card("边缘网关", scenario.get("gateway_count"), "blue"),
            _metric_card("处理窗口", report.get("total_windows"), "green"),
            _metric_card("识别命中率", report.get("average_hit_rate"), "orange"),
            _metric_card("平均协议耗时 ms", _avg_gateway_latency(report), "violet"),
            _metric_card("安全测试", security_label, "red"),
        ]
    )

    raw_json = html.escape(json.dumps(result, ensure_ascii=False, indent=2))

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RFID 云边端协同系统看板</title>
  <style>
    :root {{
      color-scheme: light;
      --text: #17202a;
      --muted: #667085;
      --line: #d7dde8;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --teal: #0f766e;
      --blue: #2563eb;
      --green: #2f855a;
      --orange: #c05621;
      --violet: #6d28d9;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      padding: 24px 32px 18px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 8px; font-size: 26px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    .sub {{ color: var(--muted); line-height: 1.6; }}
    main {{ padding: 24px 32px 40px; max-width: 1280px; margin: 0 auto; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-left: 5px solid var(--blue);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px 16px;
      min-height: 88px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    .metric strong {{ font-size: 24px; line-height: 1.2; word-break: break-word; }}
    .metric.teal {{ border-left-color: var(--teal); }}
    .metric.blue {{ border-left-color: var(--blue); }}
    .metric.green {{ border-left-color: var(--green); }}
    .metric.orange {{ border-left-color: var(--orange); }}
    .metric.violet {{ border-left-color: var(--violet); }}
    .metric.red {{ border-left-color: var(--red); }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 0.7fr);
      gap: 16px;
      align-items: start;
    }}
    section.panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 16px;
      overflow: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px 8px; text-align: left; vertical-align: top; }}
    th {{ color: #344054; background: #f9fafb; font-weight: 700; }}
    .bar {{ height: 12px; width: 100%; background: #edf2f7; border-radius: 6px; overflow: hidden; }}
    .bar span {{ display: block; height: 100%; background: var(--teal); }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #101828;
      color: #f9fafb;
      border-radius: 8px;
      padding: 14px;
      max-height: 360px;
      overflow: auto;
    }}
    @media (max-width: 860px) {{
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      .grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 22px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>智慧仓储 RFID 云边端协同系统</h1>
    <div class="sub">协议: {_esc(scenario.get("protocol"))} | 场景: {_esc(scenario.get("scene"))} | 价格范围: {_esc(scenario.get("price_min"))} - {_esc(scenario.get("price_max"))}</div>
  </header>
  <main>
    <div class="metrics">{metrics}</div>
    <div class="grid">
      <div>
        <section class="panel">
          <h2>云端流处理窗口</h2>
          <table>
            <thead><tr><th>批次</th><th>网关</th><th>终端</th><th>识别价格</th><th>窗口命中率</th><th>平均耗时</th><th>告警数</th></tr></thead>
            <tbody>{_stream_rows(result)}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>边缘网关汇总</h2>
          <table>
            <thead><tr><th>网关</th><th>窗口数</th><th>平均协议耗时</th><th>平均帧长</th><th>命中率</th></tr></thead>
            <tbody>{_gateway_rows(report)}</tbody>
          </table>
        </section>
      </div>
      <div>
        <section class="panel">
          <h2>全局热点价格</h2>
          <table>
            <thead><tr><th>价格</th><th>热度</th><th>次数</th></tr></thead>
            <tbody>{_hot_price_rows(report)}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>异常告警</h2>
          <table>
            <thead><tr><th>网关</th><th>终端</th><th>批次</th><th>告警</th></tr></thead>
            <tbody>{_alert_rows(report)}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>可扩展性基准</h2>
          <table>
            <thead><tr><th>终端</th><th>网关</th><th>批次</th><th>标签</th><th>命中率</th><th>协议耗时</th><th>实测秒</th></tr></thead>
            <tbody>{_benchmark_rows(benchmark_rows)}</tbody>
          </table>
        </section>
      </div>
    </div>
    <section class="panel">
      <h2>原始结果 JSON</h2>
      <pre>{raw_json}</pre>
    </section>
  </main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return output_path

