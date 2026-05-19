from __future__ import annotations

import csv
import shutil
from pathlib import Path

import win32com.client as win32


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = WORKSPACE_ROOT / "report_assets_iot_final"
OUTPUT_DOCX = WORKSPACE_ROOT / "2023302131145-刘滕飞-课程报告.docx"
BACKUP_DOCX = WORKSPACE_ROOT / "2023302131145-刘滕飞-课程报告-重排版.docx"


WD_ALIGN_LEFT = 0
WD_ALIGN_CENTER = 1
WD_ALIGN_JUSTIFY = 3
WD_BREAK_PAGE = 7
WD_STORY = 6
WD_STYLE_NORMAL = -1
WD_STYLE_HEADING_1 = -2
WD_STYLE_HEADING_2 = -3
WD_STYLE_HEADING_3 = -4
WD_AUTOFIT_WINDOW = 2
WD_FORMAT_DOCX = 16


def cm(value: float) -> float:
    return value * 28.3464567


def protocol_rows() -> list[dict[str, str]]:
    path = PROJECT_ROOT / "test" / "outputs" / "full_12_protocol_comparison.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def setup_styles(doc) -> None:
    normal = doc.Styles(WD_STYLE_NORMAL)
    normal.Font.Name = "Times New Roman"
    normal.Font.NameFarEast = "宋体"
    normal.Font.Size = 12
    normal.ParagraphFormat.LineSpacingRule = 1
    normal.ParagraphFormat.FirstLineIndent = cm(0.74)
    normal.ParagraphFormat.SpaceAfter = 0

    for style_id, size, name in [
        (WD_STYLE_HEADING_1, 16, "黑体"),
        (WD_STYLE_HEADING_2, 14, "黑体"),
        (WD_STYLE_HEADING_3, 12, "黑体"),
    ]:
        style = doc.Styles(style_id)
        style.Font.NameFarEast = name
        style.Font.Name = "Times New Roman"
        style.Font.Size = size
        style.Font.Bold = True
        style.ParagraphFormat.SpaceBefore = 6
        style.ParagraphFormat.SpaceAfter = 6
        style.ParagraphFormat.FirstLineIndent = 0


def apply_font(sel, *, size: float = 12, bold: bool = False, east: str = "宋体", west: str = "Times New Roman") -> None:
    sel.Font.NameFarEast = east
    sel.Font.Name = west
    sel.Font.Size = size
    sel.Font.Bold = bold


def paragraph(sel, text: str = "", *, align: int = WD_ALIGN_JUSTIFY, size: float = 12,
              bold: bool = False, style: int | None = WD_STYLE_NORMAL, first_indent: bool = True,
              before: float = 0, after: float = 0) -> None:
    if style is not None:
        sel.Style = style
    apply_font(sel, size=size, bold=bold, east="黑体" if bold and size >= 14 else "宋体")
    sel.ParagraphFormat.Alignment = align
    sel.ParagraphFormat.LineSpacingRule = 1
    sel.ParagraphFormat.FirstLineIndent = cm(0.74) if first_indent else 0
    sel.ParagraphFormat.SpaceBefore = before
    sel.ParagraphFormat.SpaceAfter = after
    if text:
        sel.TypeText(text)
    sel.TypeParagraph()


def heading(sel, text: str, level: int) -> None:
    style = {1: WD_STYLE_HEADING_1, 2: WD_STYLE_HEADING_2, 3: WD_STYLE_HEADING_3}[level]
    sel.Style = style
    sel.ParagraphFormat.Alignment = WD_ALIGN_LEFT
    sel.ParagraphFormat.FirstLineIndent = 0
    sel.TypeText(text)
    sel.TypeParagraph()
    sel.Style = WD_STYLE_NORMAL


def page_break(sel) -> None:
    sel.InsertBreak(WD_BREAK_PAGE)


def cover_table(doc, sel) -> None:
    labels = [
        ("姓    名 ：", "刘滕飞"),
        ("学    号 ：", "2023302131145"),
        ("专    业 ：", "软件工程（特软班）"),
        ("学    院 ：", "计算机学院"),
        ("指导教师 ：", "朱卫平"),
    ]
    table = doc.Tables.Add(sel.Range, len(labels), 2)
    table.Borders.Enable = False
    table.Columns(1).Width = cm(4.2)
    table.Columns(2).Width = cm(8.8)
    for row_index, (label, value) in enumerate(labels, start=1):
        table.Cell(row_index, 1).Range.Text = label
        table.Cell(row_index, 2).Range.Text = value
        for col in [1, 2]:
            rng = table.Cell(row_index, col).Range
            rng.Font.NameFarEast = "宋体"
            rng.Font.Name = "Times New Roman"
            rng.Font.Size = 14
            rng.ParagraphFormat.Alignment = WD_ALIGN_LEFT
    sel.SetRange(table.Range.End, table.Range.End)
    sel.TypeParagraph()


def table(doc, sel, headers: list[str], rows: list[list[str]], *, font_size: float = 9.5) -> None:
    tbl = doc.Tables.Add(sel.Range, len(rows) + 1, len(headers))
    tbl.Borders.Enable = True
    tbl.AllowAutoFit = True
    tbl.AutoFitBehavior(WD_AUTOFIT_WINDOW)
    for col, header in enumerate(headers, start=1):
        cell = tbl.Cell(1, col)
        cell.Range.Text = header
        cell.Range.Font.Bold = True
        cell.Range.Font.NameFarEast = "黑体"
        cell.Range.Font.Size = font_size
        cell.Shading.BackgroundPatternColor = 0xEDEDED
    for row_index, row in enumerate(rows, start=2):
        for col, value in enumerate(row, start=1):
            cell = tbl.Cell(row_index, col)
            cell.Range.Text = value
            cell.Range.Font.NameFarEast = "宋体"
            cell.Range.Font.Name = "Times New Roman"
            cell.Range.Font.Size = font_size
    sel.SetRange(tbl.Range.End, tbl.Range.End)
    sel.TypeParagraph()


def code_box(doc, sel, code: str) -> None:
    tbl = doc.Tables.Add(sel.Range, 1, 1)
    tbl.Borders.Enable = True
    tbl.AllowAutoFit = True
    tbl.AutoFitBehavior(WD_AUTOFIT_WINDOW)
    cell = tbl.Cell(1, 1)
    cell.Range.Text = code.strip()
    cell.Range.Font.Name = "Consolas"
    try:
        cell.Range.Font.NameFarEast = "Consolas"
    except Exception:
        cell.Range.Font.NameFarEast = "宋体"
    cell.Range.Font.Size = 9
    cell.Shading.BackgroundPatternColor = 0xF2F2F2
    cell.Range.ParagraphFormat.FirstLineIndent = 0
    cell.Range.ParagraphFormat.LineSpacingRule = 0
    sel.SetRange(tbl.Range.End, tbl.Range.End)
    sel.TypeParagraph()


def image(sel, image_name: str, caption: str, *, width_cm: float = 15.8, max_height_cm: float = 10.8) -> None:
    path = ASSET_ROOT / image_name
    if not path.exists():
        paragraph(sel, f"{caption}（图片文件缺失：{image_name}）", align=WD_ALIGN_CENTER, size=10, first_indent=False)
        return
    sel.ParagraphFormat.Alignment = WD_ALIGN_CENTER
    sel.ParagraphFormat.FirstLineIndent = 0
    inline = sel.InlineShapes.AddPicture(str(path), False, True)
    max_w = cm(width_cm)
    max_h = cm(max_height_cm)
    if inline.Width > max_w:
        scale = max_w / inline.Width
        inline.Width = max_w
        inline.Height = inline.Height * scale
    if inline.Height > max_h:
        scale = max_h / inline.Height
        inline.Height = max_h
        inline.Width = inline.Width * scale
    sel.TypeParagraph()
    paragraph(sel, caption, align=WD_ALIGN_CENTER, size=10, first_indent=False)


def add_frontend_section(sel) -> None:
    paragraph(sel, "前端界面围绕“需求建模—父代选择—Prompt 与生成—验证—自动迭代—产物展示—审计追踪”的闭环展开。每一页都提供页面说明与按钮解释，用户不需要回到文件夹手工查看协议、Prompt 或图表产物。")
    image(sel, "平台总览图.png", "图2-2 平台总览页面：展示流程说明、场景统计和主要入口")
    image(sel, "场景与代码图.png", "图2-3 场景与代码页面：按 generation 展示任务文件、父代和子代协议")
    image(sel, "prompt与ai.png", "图2-4 Prompt 与 AI 页面：配置任务画像、父代组合并生成协议")
    image(sel, "验证沙箱.png", "图2-5 验证沙箱页面：检查任务正确性、物理约束和信息隔离")
    image(sel, "自动迭代.png", "图2-6 自动迭代页面：可视化显示每代生成进度与父代池选择")
    image(sel, "outputs.png", "图2-7 Outputs 页面：直接预览 CSV、JSON、协议文件和运行日志")
    image(sel, "experiments.png", "图2-8 Experiments 页面：生成进化图、终稿图和鲁棒性图表")
    image(sel, "审计.png", "图2-9 审计页面：记录模型任务、系统事件和验证结果")


def add_protocol_table(doc, sel) -> None:
    rows = []
    for item in protocol_rows():
        parent = "初代父代" if item["generation"] == "0" else f'{item.get("parent_a", "")} × {item.get("parent_b", "")}'
        verify = "通过" if item["validator_passed"] == "True" and item["information_isolation_passed"] == "True" else "需复核"
        rows.append([
            item["generation"],
            item["role"],
            item["protocol_name"],
            parent,
            f'{float(item["pass_rate"]):.2f}%',
            f'{float(item["avg_time_ms"]) / 1000.0:.2f}',
            verify,
        ])
    table(doc, sel, ["代数", "类型", "协议", "父代来源", "通过率", "耗时(s)", "三重验证"], rows, font_size=8.3)


def build() -> None:
    word = win32.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Add()
        setup_styles(doc)
        section = doc.Sections(1)
        section.PageSetup.TopMargin = cm(2.54)
        section.PageSetup.BottomMargin = cm(2.54)
        section.PageSetup.LeftMargin = cm(3.0)
        section.PageSetup.RightMargin = cm(2.6)
        sel = word.Selection

        paragraph(sel, "物联网技术与应用", align=WD_ALIGN_CENTER, size=24, bold=True, first_indent=False, after=18)
        paragraph(sel, "面向多维统计场景的 RFID 协议自优化 Web 平台设计", align=WD_ALIGN_CENTER, size=20, bold=True, first_indent=False, after=12)
        for _ in range(7):
            paragraph(sel, "", first_indent=False)
        cover_table(doc, sel)
        for _ in range(4):
            paragraph(sel, "", first_indent=False)
        paragraph(sel, "二〇二六年五月", align=WD_ALIGN_CENTER, size=14, first_indent=False)

        page_break(sel)
        paragraph(sel, "目录", align=WD_ALIGN_CENTER, size=18, bold=True, first_indent=False, after=8)
        toc_range = sel.Range
        doc.TablesOfContents.Add(
            Range=toc_range,
            UseHeadingStyles=True,
            UpperHeadingLevel=1,
            LowerHeadingLevel=3,
            UseHyperlinks=True,
            HidePageNumbersInWeb=False,
            UseOutlineLevels=True,
        )
        sel.EndKey(WD_STORY)
        page_break(sel)

        heading(sel, "摘要", 1)
        paragraph(sel, "RFID 是物联网感知层的重要技术，在仓储盘点、生产追踪、物流分拣和资产管理中承担对象识别与状态采集任务。随着应用需求从身份识别扩展到群体统计，协议设计不再只关注读写效率，还需要面向众数、均值、方差、分位数、基数和缺失盘点等任务进行快速适配。传统人工设计方式依赖经验，验证流程分散，难以形成可复现的工程闭环。")
        paragraph(sel, "项目实现了一个统一的 RFID 协议自优化 Web 平台。系统支持场景资产管理、任务画像建模、父代协议复用、大语言模型交叉变异、协议落盘、验证沙箱、自动迭代、实验图表和 SQLite 审计记录。报告采用 test 场景作为主要测试用例，初代父代来自已有协议资产，后续三代共九个子代均通过平台真实生成流程产生，并使用同一组参数完成 12 个协议的统一测试。")
        paragraph(sel, "关键词：物联网；RFID；协议自优化；大语言模型；验证沙箱；自动迭代；Web 平台", first_indent=False)

        heading(sel, "第一章 设计需求分析", 1)
        heading(sel, "1.1 项目背景", 2)
        heading(sel, "1.1.1 物联网统计任务的发展需求", 3)
        paragraph(sel, "物联网系统需要把物理世界中的对象、设备与环境状态映射到数字空间。RFID 具有非接触、低成本、可批量识别等特点，适合在仓储、零售、制造、图书档案和医疗耗材等场景中采集对象属性。随着应用层对群体状态的关注增加，系统需要从“识别有哪些标签”扩展到“估计标签集合呈现什么统计特征”。")
        heading(sel, "1.1.2 现有协议设计方式的不足", 3)
        paragraph(sel, "现有 RFID 查询协议常面向单一任务设计，如基于 ALOHA 的随机竞争协议、基于树结构的确定性识别协议和基于草图的估计协议。这些方法在固定条件下效果较好，但当统计目标、数据分布、标签规模和约束条件变化时，迁移和调参成本较高。课程项目需要的不只是一个协议脚本，而是能够展示设计、生成、验证和比较全过程的平台。")
        heading(sel, "1.1.3 项目定位", 3)
        paragraph(sel, "本项目定位为“面向多维统计场景的 RFID 协议自优化 Web 平台”。用户通过 Web 页面选择场景、任务画像和父代协议，平台构造交叉变异 Prompt，调用大语言模型生成候选子代协议，随后自动执行功能正确性、物理约束和信息隔离验证，并将结果以图表和文件资产形式展示。")
        heading(sel, "1.2 用户需求", 2)
        paragraph(sel, "平台面向物联网课程学习者、RFID 协议研究人员和工业物联网原型开发者。学习者关注流程可视化和报告材料，研究人员关注候选协议的批量生成与对比，工程开发者关注特定统计任务下的快速原型验证。")
        table(doc, sel, ["编号", "功能需求", "实现方式"], [
            ["F1", "统一 Web 自优化平台", "web_app.py 提供 API，selfopt_ui.py 提供交互页面"],
            ["F2", "多场景资产管理", "自动扫描 Task.py、generation 和 prompts 目录"],
            ["F3", "任务画像建模", "支持众数、均值、方差、中位数、基数和缺失盘点"],
            ["F4", "父代协议复用", "从 base_protocol、multi_mode 和场景 generation 中选择父代"],
            ["F5", "交叉变异生成", "ai_protocol_optimizer.py 调用模型生成并修复协议"],
            ["F6", "验证沙箱", "功能正确性、物理约束、信息隔离三类检查"],
            ["F7", "自动迭代与图表", "多代子代生成、父代池选择、CSV/JSON/PNG/PDF 输出"],
        ])
        paragraph(sel, "非功能需求包括可运行、可解释、可扩展、可测试和可追溯。项目应能通过本地命令启动，页面应能解释每个按钮和阶段，新增场景只需补充 Task.py 与 generation 目录，测试结果应能复现，生成过程应保留 Prompt、目标文件、验证结果和审计记录。")

        heading(sel, "第二章 系统功能分析", 1)
        heading(sel, "2.1 总体架构概述", 2)
        paragraph(sel, "平台采用“需求建模层—协议生成层—验证迭代层—结果展示层”的结构。需求建模层负责把业务目标转化为任务画像和约束，协议生成层负责读取父代并构造 Prompt，验证迭代层负责运行协议并筛选候选，结果展示层负责统一呈现代码、Prompt、outputs、实验图表和审计记录。")
        image(sel, "architecture.png", "图2-1 RFID 协议自优化 Web 平台总体架构")
        heading(sel, "2.2 平台前端功能", 2)
        add_frontend_section(sel)
        heading(sel, "2.3 协议生成与验证功能", 2)
        paragraph(sel, "协议生成层由 prompt_generation.py 与 ai_protocol_optimizer.py 组成。前者读取父代协议源代码并生成交叉 Prompt，后者追加任务画像、硬性接口、物理约束和信息隔离要求，调用 OpenAI 兼容接口生成 Python 协议类。验证层加载场景任务数据，动态导入协议类，执行 IProtocol.execute 接口并记录结果。")
        heading(sel, "2.4 结果展示与审计功能", 2)
        paragraph(sel, "Outputs 页面显示生成的协议、Prompt、CSV、JSON 和日志。Experiments 页面调用原项目实验脚本生成进化图、耗时收敛图、准确率收敛图、纠正次数图和终稿对比图。审计页面展示数据库中的 AI 任务、系统事件和验证记录，使平台行为具备可追溯性。")

        heading(sel, "第三章 系统设计", 1)
        heading(sel, "3.1 项目文件结构", 2)
        table(doc, sel, ["路径", "功能说明"], [
            ["course_project/web_app.py", "Web API、异步任务、验证、实验和 outputs 预览入口"],
            ["course_project/selfopt_ui.py", "统一 Web 工作台前端页面"],
            ["ai_protocol_optimizer.py", "大语言模型协议生成、自修复和验证流程"],
            ["prompt_generation.py", "父代协议读取与交叉 Prompt 构造"],
            ["Validator.py", "RFID 协议物理层约束验证"],
            ["test/Task.py", "报告使用的确定性双峰 RFID 统计场景"],
            ["test/generation_0", "由已有协议资产复制得到的 3 个初代父代"],
            ["test/generation_1~3", "平台真实生成的 9 个子代协议"],
            ["Experiments", "进化图、终稿对比图和实验 CSV/JSON/PDF/PNG 产物"],
        ])
        heading(sel, "3.2 test 场景数据设计", 2)
        paragraph(sel, "test 场景模拟具有双峰热点分布的 RFID 库存统计任务。标签价格主要集中在 299 和 699 两个热点值，另有 499 和随机背景值。该设计既能检验协议是否识别多个众数，也便于在不同运行参数下比较协议耗时和稳定性。")
        code_box(doc, sel, """
SIMULATION_CONFIG = {
    "NUM_TAGS": 1500,
    "PRICE_MIN": 100,
    "PRICE_MAX": 900,
    "TOTAL_RUNS": 12,
}
# test/Task.py 生成确定性双峰分布，真实众数为 [299, 699]
""")
        heading(sel, "3.3 初代父代与迭代谱系", 2)
        paragraph(sel, "test 场景的 generation_0 不采用临时手写协议，而是从已有协议资产中复制 3 个父代。第一代由父代两两交叉产生 3 个子代，第二代和第三代继续从上一代候选中组成父代池进行交叉。每个子代均保留 Prompt、目标协议文件和验证结果。")
        table(doc, sel, ["父代协议", "来源", "选取原因"], [
            ["ModeQuerySEM", "base_protocol/ModeQuerySEM.py", "统计估计机制清晰，适合作为稳定基线"],
            ["ModeQueryTKQ", "base_protocol/ModeQueryTKQ.py", "面向 Top-K 和热点查询，符合众数检测目标"],
            ["g2_2", "multi_mode/generation2/g2_2.py", "包含多阶段筛选思想，适合作为复杂父代"],
        ])
        image(sel, "lineage_graph_test.png", "图3-1 test 场景父代池与子代交叉谱系")
        heading(sel, "3.4 数据库与审计设计", 2)
        paragraph(sel, "平台使用 SQLite 保存运行记录、AI 任务、设计案例和系统事件。ai_jobs 表记录父代、子代、目标 generation、Prompt 文件、目标文件和日志；design_cases 表记录需求建模结果；runs、security_checks 和 system_events 表记录测试与审计信息。该设计保证生成流程不是一次性脚本，而是可追溯的平台行为。")

        heading(sel, "第四章 软件设计与关键实现", 1)
        heading(sel, "4.1 Web 服务实现", 2)
        paragraph(sel, "Web 服务采用 ThreadingHTTPServer 实现，入口命令如下。服务启动后访问 127.0.0.1:8765 即可进入平台，标准库服务降低了依赖数量，便于课程提交和复现。")
        code_box(doc, sel, "python -m course_project.web_app --host 127.0.0.1 --port 8765")
        heading(sel, "4.2 任务画像实现", 2)
        paragraph(sel, "任务画像用于决定验证目标，而不是数据文件本身。一个场景可以复用同一份 Task.py，在验证时根据任务画像切换众数、均值、方差、中位数、基数或缺失盘点等目标值。")
        code_box(doc, sel, """
if task_profile == "mode_query":
    return list(true_info.get("modes", []))
if task_profile == "mean_estimation":
    return [sum(prices) / len(prices)]
if task_profile == "cardinality_estimation":
    return [float(len(tags))]
""")
        heading(sel, "4.3 协议生成实现", 2)
        paragraph(sel, "协议生成流程读取父代源代码，构造包含交叉、变异、接口要求、物理约束和信息隔离要求的 Prompt。模型输出后，系统提取 Python 代码、校正类名、写入目标 generation 目录，并立即进入验证。")
        code_box(doc, sel, """
result = run_ai_generation({
    "scene": "test",
    "parent_a": parent_a,
    "parent_b": parent_b,
    "target_generation": f"generation_{generation}",
    "task_profile": "mode_query",
    "dry_run": False,
})
""")
        heading(sel, "4.4 验证沙箱实现", 2)
        paragraph(sel, "验证沙箱先加载 test/Task.py 生成标签集合，再动态导入候选协议类并执行 execute。ProtocolValidator 检查帧长和时隙位宽，信息隔离扫描检查协议是否直接读取真实答案，任务正确性由任务画像对应的目标值判定。")
        code_box(doc, sel, """
validator = ProtocolValidator(protocol_name, protocol_instance)
is_valid, report = validator.validate(tags, price_min, price_max)
information_isolation = _scan_information_isolation(protocol_file)
hit = _profile_hit(estimated_values, target_values, task_profile, payload)
""")
        heading(sel, "4.5 实验图表实现", 2)
        paragraph(sel, "Experiments 模块将验证数据转化为可读图表。报告补充生成了完整 12 协议对比图、分代准确率与耗时图、验证矩阵、父子代谱系图和准确率-耗时二维分布图，并把原实验脚本输出的性能进化、耗时收敛、准确率收敛、纠正次数和终稿图全部转换为报告可插入的 PNG。")

        heading(sel, "第五章 测试与结果分析", 1)
        heading(sel, "5.1 测试流程", 2)
        paragraph(sel, "测试流程分为四步：创建 test 场景并复制 3 个已有父代；通过平台真实生成接口完成 3 代、每代 3 个子代；使用同一组参数对 12 个协议执行统一测试；调用 Experiments 模块生成完整图表并插入报告。生成日志与统一测试结果分开保存，前者证明协议确由平台产生，后者用于横向比较。")
        code_box(doc, sel, """
python -m unittest course_project.test_course_project -v
python -m course_project.web_app --host 127.0.0.1 --port 8765
# test 场景：generation_0 为 3 个父代，generation_1~3 共 9 个平台生成子代
""")
        heading(sel, "5.2 完整 12 协议统一测试结果", 2)
        add_protocol_table(doc, sel)
        paragraph(sel, "统一测试表明，12 个协议均能完成执行并通过物理约束与信息隔离检查。多数协议在当前双峰任务上达到 100% 通过率，ModeQueryTKQ 与 test_g3_2 为 91.67%，test_g3_3 为 83.33%。这说明自动生成能够产生有效候选，同时也需要依靠验证沙箱淘汰或继续修复表现不稳定的子代。")
        image(sel, "full_12_protocol_comparison.png", "图5-1 test 场景 12 个协议统一验证对比", max_height_cm=11.2)
        image(sel, "generation_accuracy_time_summary.png", "图5-2 test 场景分代准确率与耗时变化")
        image(sel, "validation_matrix_12.png", "图5-3 test 场景 12 个协议验证矩阵")
        image(sel, "accuracy_latency_scatter_test.png", "图5-4 准确率-耗时二维分布")
        heading(sel, "5.3 Experiments 图表结果", 2)
        paragraph(sel, "除补充图表外，平台原 Experiments 模块也完成了 test 场景图表生成。性能进化图展示每代候选的准确率与适应度变化；耗时收敛图和准确率收敛图用于观察是否出现稳定趋势；纠正次数图记录模型生成与修复过程；终稿图对当前场景内全部协议进行最终比较。")
        image(sel, "chart_1_performance_evolution_test.png", "图5-5 Experiments 性能进化曲线")
        image(sel, "chart_2_time_convergence_test.png", "图5-6 Experiments 耗时收敛曲线")
        image(sel, "chart_3_accuracy_convergence_test.png", "图5-7 Experiments 准确率收敛曲线")
        image(sel, "chart_4_correction_counts_test.png", "图5-8 Experiments 模型纠正次数")
        image(sel, "final_protocol_performance_test.png", "图5-9 Experiments 终稿协议性能对比", max_height_cm=11.2)
        heading(sel, "5.4 单元测试结果", 2)
        paragraph(sel, "项目保留了课程工程烟测，覆盖平台状态读取、test 场景文件读取、AI 生成预览、自动迭代预览、单协议验证、SQLite AI 作业持久化和实验图表任务。执行 python -m unittest course_project.test_course_project -v，结果为 Ran 6 tests，OK。")

        heading(sel, "第六章 社会价值", 1)
        paragraph(sel, "RFID 协议自优化平台具有教学价值和工程应用价值。在教学场景中，平台把抽象的防碰撞、统计估计、约束验证和自动迭代过程转化为可交互流程，便于理解物联网感知层、网络层和应用层之间的关系。在科研场景中，平台可以作为协议快速原型工具，帮助研究者批量构造候选协议并进行可重复实验。在工业场景中，平台可扩展到仓储盘点、冷链监管、医疗资产追踪、生产质量统计和图书档案管理等业务。")
        paragraph(sel, "平台强调验证与审计，能够降低直接使用大语言模型生成代码的风险。每一次生成都有 Prompt、源代码、验证结果和图表产物，每一个候选协议都必须通过物理约束和信息隔离检查。这种设计有助于把模型辅助设计从演示工具推进到可审查、可复现的软件工程流程。")

        heading(sel, "第七章 总结与讨论", 1)
        paragraph(sel, "项目围绕物联网 RFID 多维统计任务，完成了统一的 Web 自优化平台。系统实现了场景资产管理、任务画像建模、父代协议复用、大语言模型交叉变异、协议验证沙箱、自动迭代、实验图表和数据库审计等功能。与分散脚本或单一 GUI 相比，当前平台形成了从需求输入到协议产物、再到测试结果展示的闭环。")
        paragraph(sel, "课程报告使用的 test 场景严格按照平台流程构造：初代父代来自已有协议资产，后续三代共九个子代均通过平台真实生成接口产生，每代包含多个子代，并依据验证指标进入下一代选择。实验结果表明，平台能够稳定完成协议生成、落盘、验证和图表输出，具备课程大作业所需的完整性。")
        paragraph(sel, "后续工作可从三个方向继续完善：扩展父代协议库，引入更多面向均值、方差和分位数估计的高质量协议；改进评分函数，将准确率、耗时、能耗和鲁棒性统一到多目标优化模型；接入真实 RFID 读写器或 MQTT 消息中间件，使当前本地仿真平台进一步迁移到真实物联网部署环境。")

        heading(sel, "参考文献", 1)
        refs = [
            "[1] Zhu W, Meng X, Peng X, et al. Collisions Are Preferred: RFID-Based Stocktaking with a High Missing Rate. IEEE Transactions on Mobile Computing, 2020, 19(7): 1544-1554.",
            "[2] Qian C, Ngan H, Liu Y, et al. Cardinality estimation for large-scale RFID systems. IEEE Transactions on Parallel and Distributed Systems, 2011, 22(9): 1441-1454.",
            "[3] Liu X, Li K, Liu A X, et al. Multi-Category RFID Estimation. IEEE/ACM Transactions on Networking, 2017, 25(1): 264-277.",
            "[4] Mustafa H, Zhu X, Li Q, et al. Efficient median estimation for large-scale sensor RFID systems. International Journal of Sensor Networks, 2012, 12(3): 171-183.",
            "[5] 面向多维统计场景的 RFID 协议自优化 Web 平台项目源码，rfid/code_add。",
        ]
        for ref in refs:
            paragraph(sel, ref, first_indent=False)

        for field_index in range(1, doc.Fields.Count + 1):
            field = doc.Fields(field_index)
            if str(field.Code.Text).strip().startswith("TOC"):
                field.Code.Text = ' TOC \\o "1-3" \\h \\z \\u '
                field.Update()
        doc.TablesOfContents(1).UseHyperlinks = True
        doc.TablesOfContents(1).Update()
        doc.Fields.Update()
        doc.Repaginate()
        doc.SaveAs(str(OUTPUT_DOCX), FileFormat=WD_FORMAT_DOCX)
        doc.Close(False)
        shutil.copyfile(OUTPUT_DOCX, BACKUP_DOCX)
    finally:
        word.Quit()


if __name__ == "__main__":
    build()
