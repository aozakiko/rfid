from __future__ import annotations

import shutil
import sys
from pathlib import Path

import win32com.client as win32

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from course_project.build_iot_course_report import (
    ASSET_ROOT,
    BACKUP_DOCX,
    OUTPUT_DOCX,
    WD_ALIGN_CENTER,
    WD_ALIGN_JUSTIFY,
    WD_BREAK_PAGE,
    WD_FORMAT_DOCX,
    WD_STORY,
    add_protocol_table,
    cm,
    code_box,
    cover_table,
    heading,
    image,
    page_break,
    paragraph,
    setup_styles,
    table,
)
from course_project.build_iot_report_assets import main as build_assets


def add_requirement_mapping(doc, sel) -> None:
    table(
        doc,
        sel,
        ["课程要求/评分点", "项目对应实现", "报告呈现位置"],
        [
            ["传感层技术", "以 RFID Tag、价格属性、双峰热点分布和 Task.py 场景生成器模拟感知识别层", "第三章 3.1、第五章 5.1"],
            ["网络层与任务划分", "将端侧采集、边缘验证、云端生成与分析划分为三层职责", "第三章 3.2"],
            ["应用层技术", "Web 自优化工作台、Prompt 编辑器、验证沙箱、自动迭代和图表分析", "第二章、第四章"],
            ["网络安全", "物理约束验证、信息隔离扫描、数据库审计和系统事件记录", "第三章 3.4、第四章 4.4"],
            ["云端处理", "大模型协议生成、实验图表汇总、SQLite 持久化与报告产物归档", "第三章 3.3、第四章 4.6"],
            ["流处理和一般处理", "验证沙箱执行单协议即时验证，Experiments 执行统一批量分析和图表生成", "第五章"],
            ["分布式计算或大模型技术", "调用 OpenAI 兼容接口完成协议交叉变异，并用父代池自动迭代", "第四章 4.3、4.5"],
            ["问题分析与解决能力", "从 RFID 多维统计需求出发，建立场景、任务画像、父代池和验证闭环", "第一章至第五章"],
            ["技术开发能力", "完整 Web 平台、数据库、AI 生成、验证脚本、图表与单元测试均可运行", "第四章、第五章"],
        ],
        font_size=8.6,
    )


def add_cloud_edge_table(doc, sel) -> None:
    table(
        doc,
        sel,
        ["层级", "实体/模块", "主要任务", "对应文件或页面"],
        [
            ["端侧", "RFID 标签与读写器模拟", "生成标签集合、价格属性和真实统计量，模拟感知层数据来源", "test/Task.py"],
            ["边缘侧", "协议执行与验证沙箱", "执行候选协议，检查帧长、时隙位宽、信息隔离和任务命中率", "Validator.py、验证沙箱页"],
            ["云端", "大模型与实验分析", "生成交叉变异协议，保存 Prompt、代码和统一测试图表", "ai_protocol_optimizer.py、Experiments"],
            ["应用层", "Web 自优化平台", "组织场景资产、自动迭代、outputs 预览和审计记录", "web_app.py、selfopt_ui.py"],
        ],
        font_size=8.8,
    )


def add_cover_and_toc(doc, sel) -> None:
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


def add_abstract(sel) -> None:
    heading(sel, "摘要", 1)
    paragraph(sel, "射频识别技术是物联网感知层的重要组成部分，能够在不接触目标对象的情况下完成批量识别和属性采集。随着仓储、制造、物流和资产管理场景逐渐从单纯识别转向群体统计，RFID 系统需要支持众数、均值、方差、分位数、基数和缺失盘点等多类统计任务。传统协议设计依赖人工经验，往往需要反复修改脚本、手动运行测试、再整理结果，难以形成可复现、可审计、可交互的课程项目闭环。")
    paragraph(sel, "本项目设计并实现了一个面向多维统计场景的 RFID 协议自优化 Web 平台。系统围绕“需求建模—父代协议选择—大模型交叉变异—协议落盘—验证沙箱—自动迭代—实验图表—审计追踪”形成完整流程。平台从物联网课程要求出发，将传感层、网络/边缘层、云端智能处理、应用层展示、安全验证、流处理和一般处理统一到一个可运行的软件系统中。")
    paragraph(sel, "报告采用新建 test 场景作为主要测试用例。该场景的 generation_0 由已有协议资产中的 3 个父代组成，后续 3 代共 9 个子代均通过平台真实生成接口产生，每一代包含多个候选协议。最终使用同一测试参数对 12 个协议进行统一验证，并生成完整对比图、验证矩阵、谱系图、进化曲线、耗时收敛图、准确率收敛图和终稿对比图。")
    paragraph(sel, "关键词：物联网；RFID；协议自优化；云边端协同；大语言模型；验证沙箱；自动迭代；Web 平台", first_indent=False)


def build() -> None:
    build_assets()
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

        add_cover_and_toc(doc, sel)
        add_abstract(sel)

        heading(sel, "第一章 设计需求分析", 1)
        heading(sel, "1.1 项目背景", 2)
        heading(sel, "1.1.1 RFID 感知统计任务的发展需求", 3)
        paragraph(sel, "RFID 系统在物联网中承担感知层入口角色。传统系统通常关注标签识别和盘点完成率，但在真实业务中，管理者更关心群体状态，例如库存中最集中的价格区间、某批次标签的平均属性、异常值分布、缺失数量和热点对象变化。因此，面向统计任务的 RFID 协议不只是通信过程，也连接了感知数据、网络传输、边缘计算和应用决策。")
        paragraph(sel, "在本项目中，标签的价格属性被抽象为可统计的感知数据。test 场景使用确定性双峰分布模拟仓储或零售场景中的热点价格，协议需要在有限执行信息中估计众数集合。这种设计能够体现传感层数据采集和应用层统计需求之间的关系。")
        heading(sel, "1.1.2 大模型辅助协议设计趋势", 3)
        paragraph(sel, "大语言模型能够根据自然语言约束和已有代码生成候选程序，但直接生成协议存在不可执行、接口不一致、约束缺失和信息泄露等风险。因此，模型不能脱离工程流程独立工作，必须与父代协议库、Prompt 约束、自动验证、日志审计和图表分析结合，才能成为可靠的软件开发工具。")
        heading(sel, "1.1.3 项目定位", 3)
        paragraph(sel, "本项目定位为一个面向 RFID 多维统计任务的 Web 自优化平台，而不是单个协议脚本。平台把现有协议资产视为父代池，通过大模型生成交叉变异子代，再通过验证沙箱和实验模块筛选结果。该定位既符合课程对物联网综合应用的要求，也体现了项目自身的创新点。")
        heading(sel, "1.2 用户需求", 2)
        heading(sel, "1.2.1 用户群体定位", 3)
        paragraph(sel, "平台面向物联网课程学习者、RFID 协议研究人员和工业物联网原型开发者。学习者需要通过界面理解协议设计和验证流程；研究人员需要批量生成候选协议并比较性能；工程开发者需要将具体业务统计目标快速转化为可验证的软件原型。")
        heading(sel, "1.2.2 课程学习需求", 3)
        paragraph(sel, "课程学习场景强调“看得见的流程”。系统需要展示每个协议来自哪里、Prompt 如何构造、模型生成了什么文件、验证为什么通过或失败、outputs 中有哪些结果，以及实验图表如何支撑结论。平台各页面均提供说明框和按钮解释，降低理解门槛。")
        heading(sel, "1.2.3 研究与工程需求", 3)
        paragraph(sel, "研究和工程场景强调可扩展和可追溯。新增任务应能通过 Task.py、config.py 和 generation 目录接入；协议生成过程应保留 Prompt 和目标文件；实验应能输出 CSV、JSON、PNG 和 PDF；数据库应记录 AI 任务和审计事件。")
        heading(sel, "1.2.4 用户使用场景", 3)
        paragraph(sel, "典型使用流程为：用户打开 Web 平台，选择 test 或其他场景；在场景与代码页查看父代和子代文件；在 Prompt 与 AI 页选择任务画像和父代组合；在自动迭代页启动多代生成；在验证沙箱页检查候选协议；最后在 Outputs 和 Experiments 页查看结果并导出报告材料。")
        add_requirement_mapping(doc, sel)

        heading(sel, "第二章 系统功能分析", 1)
        heading(sel, "2.1 总体架构概述", 2)
        paragraph(sel, "系统采用“感知建模—边缘验证—云端生成—应用展示”的总体架构。感知建模由 Task.py 产生标签集合和真实统计目标；边缘验证负责执行候选协议并检查物理约束；云端生成负责调用大模型完成交叉变异；应用展示由 Web 页面、数据库和图表模块组成。")
        image(sel, "architecture.png", "图2-1 RFID 协议自优化 Web 平台总体架构")
        paragraph(sel, "从运行路径看，平台首先扫描项目中的场景目录，形成可选择的场景、父代协议和历史产物；随后用户在页面中确定任务画像与父代组合，后端根据父代代码生成 Prompt 并调用模型；模型返回的候选协议写入目标 generation 目录后进入验证沙箱；验证通过或失败的结果继续进入 outputs、Experiments 和审计记录。该路径保证系统不是“页面展示 + 后台脚本”的松散组合，而是具有明确数据流和责任边界的工程闭环。")
        image(sel, "平台总览图.png", "图2-2 平台总览页面：展示场景统计、流程入口和平台说明", max_height_cm=9.8)
        heading(sel, "2.2 感知识别层", 2)
        paragraph(sel, "感知识别层对应 RFID 标签集合、标签属性和读写器感知过程。由于课程提交以软件项目为主，项目使用 test/Task.py 对端侧感知过程进行可复现模拟。该文件生成标签 ID、价格属性和真实统计信息，为协议验证提供稳定输入。")
        paragraph(sel, "该层的输入是标签规模、属性取值范围和随机种子，输出是 tags 列表与 true_info 字典。tags 列表模拟读写器可接触到的对象集合，true_info 只在验证阶段作为标准答案使用，不允许候选协议直接读取。这样的隔离方式既便于软件测试，也能体现真实物联网系统中“采集数据”和“评价答案”分离的原则。")
        heading(sel, "2.3 网络与边缘验证层", 2)
        paragraph(sel, "网络与边缘验证层关注协议是否符合 RFID 物理层约束和边缘侧运行要求。候选协议必须继承统一接口，执行时返回估计值、指标、耗时和验证数据。Validator 根据帧长、时隙位宽等信息检查协议行为，信息隔离扫描防止协议直接读取真实答案。")
        paragraph(sel, "边缘验证层承担两类职责：一类是近似真实读写器侧的协议执行，关注时隙、帧结构和查询过程；另一类是平台安全边界，防止模型生成的代码绕过协议过程直接访问测试答案。二者共同决定候选协议是否有资格进入下一代父代池。")
        heading(sel, "2.4 智能生成层", 2)
        paragraph(sel, "智能生成层由父代协议选择、Prompt 构造、大模型调用、代码提取、类名修正和自动修复组成。该层体现课程要求中的大模型技术应用，也使平台能够从已有协议中持续生成新的候选方案。")
        table(doc, sel, ["阶段", "输入", "输出", "设计目的"], [
            ["父代选择", "已有 generation 协议或 base_protocol 协议", "父代 A、父代 B、父代池", "保证生成不是凭空编写，而是继承已有协议机制"],
            ["Prompt 构造", "父代代码、任务画像、自然语言约束", "交叉变异 Prompt", "把业务目标转化为模型可执行的开发约束"],
            ["模型生成", "系统提示词与用户 Prompt", "候选 Python 协议类", "利用大模型进行代码层面的组合与变异"],
            ["落盘验证", "候选代码", "generation 文件、验证摘要", "保证生成结果可运行、可追溯、可比较"],
        ], font_size=8.5)
        heading(sel, "2.5 交互反馈层", 2)
        paragraph(sel, "交互反馈层由统一 Web 工作台承担。页面将代码、Prompt、验证结果、outputs、实验图表和审计信息直接呈现，避免用户只在文件夹中查找结果。为了避免截图堆叠影响阅读，报告不在本节集中放置所有页面，而是在后续软件设计与测试章节中将各页面截图放到对应模块旁边。")

        heading(sel, "第三章 感知层与云边端协同设计", 1)
        heading(sel, "3.1 端侧感知与数据模型", 2)
        heading(sel, "3.1.1 标签数据模型", 3)
        paragraph(sel, "端侧标签数据由 Tag(id, price) 表示。id 用于区分对象，price 用于模拟待统计属性。在真实系统中，该属性可以替换为温度、批次号、货架位置、库存类别或风险等级。项目选择价格属性，是因为它便于构造双峰热点分布并进行多众数验证。")
        table(doc, sel, ["字段", "含义", "在本项目中的作用"], [
            ["id", "标签唯一标识", "模拟 RFID 标签 EPC 或内部编号"],
            ["price", "待统计属性", "构造众数、均值、方差等任务目标"],
            ["modes", "真实众数集合", "仅用于验证，不允许协议直接读取"],
            ["distribution", "数据分布描述", "帮助报告和图表解释测试场景"],
        ], font_size=8.8)
        heading(sel, "3.1.2 test 场景生成器", 3)
        paragraph(sel, "test 场景模拟两个热点价格 299 和 699，并加入 499 及随机尾部分布。该场景既能测试协议是否识别多个众数，也能观察候选协议在非均匀分布下的稳定性。")
        code_box(doc, sel, """
SIMULATION_CONFIG = {
    "NUM_TAGS": 1500,
    "PRICE_MIN": 100,
    "PRICE_MAX": 900,
    "TOTAL_RUNS": 12,
}
# generate_tags 生成确定性双峰分布，真实众数为 [299, 699]
""")
        heading(sel, "3.2 云边端任务划分", 2)
        paragraph(sel, "课程要求关注云边端协同。虽然本项目以本地 Web 软件形态提交，但系统设计中明确划分了端侧、边缘侧、云端和应用层职责。端侧负责数据产生，边缘侧负责低延迟协议执行与约束验证，云端负责大模型生成和全局实验分析，应用层负责交互和审计。")
        add_cloud_edge_table(doc, sel)
        heading(sel, "3.3 父代池与自动迭代设计", 2)
        paragraph(sel, "test 场景的 generation_0 由 3 个已有协议父代组成：ModeQuerySEM、ModeQueryTKQ 和 g2_2。第一代由父代两两交叉得到 3 个子代；第二代和第三代继续根据上一代结果形成父代池。该流程与遗传算法中的选择、交叉和变异思想一致，但每个子代由大模型根据代码和约束生成。")
        table(doc, sel, ["父代协议", "来源", "设计意义"], [
            ["ModeQuerySEM", "base_protocol/ModeQuerySEM.py", "稳定统计估计基线"],
            ["ModeQueryTKQ", "base_protocol/ModeQueryTKQ.py", "面向热点和 Top-K 的查询基线"],
            ["g2_2", "multi_mode/generation2/g2_2.py", "已有演化协议，包含多阶段筛选思想"],
        ], font_size=8.8)
        image(sel, "lineage_graph_test.png", "图3-1 test 场景真实父代池交叉谱系")
        heading(sel, "3.4 数据库与审计设计", 2)
        paragraph(sel, "数据库采用 SQLite，保存 AI 任务、运行记录、设计案例、安全检查和系统事件。该设计的作用是把一次模型调用变成可追踪的平台行为。每个候选协议都可以追溯到父代、Prompt、目标 generation、目标文件和验证结果。")
        paragraph(sel, "审计记录主要解决两个问题：第一，证明子代协议确实由平台流程生成，而不是事后手写；第二，在生成失败、验证失败或结果异常时，能够回看父代组合、Prompt 文件和后端日志。对于引入大模型的开发流程而言，审计不是附属功能，而是保证可解释性和课程报告可信度的关键部分。")
        image(sel, "审计.png", "图3-2 审计页面：记录 AI 任务、系统事件和验证结果", max_height_cm=9.8)

        heading(sel, "第四章 软件设计", 1)
        heading(sel, "4.1 基本框架", 2)
        paragraph(sel, "软件主体位于 rfid/code_add 目录。course_project/web_app.py 提供 HTTP API 和后端任务调度；course_project/selfopt_ui.py 提供单页 Web 工作台；ai_protocol_optimizer.py 负责模型调用和协议生成；Validator.py 负责物理层约束验证；Experiments 目录负责图表和终稿对比。")
        code_box(doc, sel, "python -m course_project.web_app --host 127.0.0.1 --port 8765")
        paragraph(sel, "后端采用标准库 HTTP 服务实现，主要原因是课程提交需要降低环境复杂度。前端页面以单页应用形式组织所有功能，不需要额外构建工具。AI 生成、验证和实验任务由后端 API 调度，页面负责提交参数、展示状态和刷新产物。")
        heading(sel, "4.2 场景资产与文件管理", 2)
        paragraph(sel, "场景资产由 Task.py、config.py、generation_*、prompts 和 outputs 组成。平台启动时扫描所有包含 Task.py 与 generation 目录的场景，将协议文件、Prompt 文件和实验产物整理为页面可查看的资产列表。")
        table(doc, sel, ["路径", "作用"], [
            ["test/Task.py", "生成标签集合和真实统计目标"],
            ["test/generation_0", "保存 3 个初代父代协议"],
            ["test/generation_1~3", "保存平台真实生成的 9 个子代协议"],
            ["test/prompts", "保存每次大模型生成使用的 Prompt"],
            ["test/outputs", "保存生成摘要和 12 协议统一测试结果"],
            ["Experiments", "保存进化图、终稿图和实验数据"],
        ], font_size=8.8)
        paragraph(sel, "场景与代码页的核心作用是把文件系统中的 generation 结构可视化。用户可以直接查看每一代协议文件、Prompt 文件和 outputs 文件，避免在多层目录中手工定位。对于 test 场景，页面能够清楚区分 generation_0 的父代协议和 generation_1 至 generation_3 的平台生成子代。")
        image(sel, "场景与代码图.png", "图4-1 场景与代码页面：按 generation 管理任务文件、父代和子代协议", max_height_cm=10.0)
        heading(sel, "4.3 Prompt 与 AI 生成层", 2)
        paragraph(sel, "Prompt 与 AI 页面负责把用户的任务画像、父代协议和自然语言约束组合为模型输入。后端要求模型输出可执行 Python 代码，且必须继承 IProtocol 并实现 execute 接口。")
        paragraph(sel, "任务画像与场景文件是两个不同概念。Task.py 决定标签数据如何生成，任务画像决定本轮验证要从标签数据中提取什么目标。以 test 场景为例，同一批标签数据既可以验证众数查询，也可以扩展为均值估计、基数估计或缺失盘点。Prompt 页面将这种语义差异写入模型约束中，使生成协议面向具体任务。")
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
        table(doc, sel, ["参数", "含义", "影响"], [
            ["scene", "目标场景名称", "决定读取哪个 Task.py 和 generation 目录"],
            ["parent_a / parent_b", "参与交叉的父代协议", "决定子代继承的算法机制"],
            ["target_generation", "子代落盘目录", "决定生成文件保存到哪一代"],
            ["task_profile", "任务画像", "决定验证目标和 Prompt 约束"],
            ["dry_run", "是否只预览", "False 时会真实调用模型并写入协议文件"],
        ], font_size=8.5)
        image(sel, "prompt与ai.png", "图4-2 Prompt 与 AI 页面：配置任务画像、父代组合并生成协议", max_height_cm=10.0)
        heading(sel, "4.4 验证沙箱层", 2)
        paragraph(sel, "验证沙箱从功能正确性、物理约束和信息隔离三个角度检查协议。功能正确性根据任务画像计算目标值；物理约束检查协议是否输出合理的帧长和时隙数据；信息隔离扫描源代码，避免模型生成的协议直接读取 true_info 等真实答案。")
        paragraph(sel, "验证沙箱既可以作为单协议调试工具，也可以作为自动迭代流程中的质量门禁。单独运行时，用户可以选择任意协议查看样本估计值、目标值、通过率和耗时；自动迭代时，每个新生成的子代都会自动进入相同验证逻辑，只有验证信息完整的候选才会被纳入后续统计。")
        code_box(doc, sel, """
validator = ProtocolValidator(protocol_name, protocol_instance)
is_valid, report = validator.validate(tags, price_min, price_max)
information_isolation = _scan_information_isolation(protocol_file)
hit = _profile_hit(estimated_values, target_values, task_profile, payload)
""")
        table(doc, sel, ["验证项", "检查内容", "失败影响"], [
            ["任务正确性", "估计值是否命中任务画像对应目标", "降低通过率，不进入优先父代池"],
            ["物理约束", "帧长、时隙位宽等是否合理", "判定协议不满足 RFID 执行约束"],
            ["信息隔离", "是否直接读取 true_info 或测试答案", "判定为不可信协议"],
            ["运行稳定性", "是否抛出异常、是否完成所有轮次", "记录 runtime_error 并阻止结果混入统计"],
        ], font_size=8.5)
        image(sel, "验证沙箱.png", "图4-3 验证沙箱页面：展示单协议验证结果和样本输出", max_height_cm=10.0)
        heading(sel, "4.5 自动迭代层", 2)
        paragraph(sel, "自动迭代页将多个父代组成候选池，按代数循环生成子代。每个子代在落盘后立即验证，平台根据通过率、耗时、物理约束和信息隔离结果选择下一代父代池。该功能让用户能够观察协议从父代到子代的演化过程，而不是只看到最终代码文件。")
        paragraph(sel, "在 test 场景中，自动迭代采用“3 个父代 + 3 代 + 每代 3 个子代”的设置。第一代由三个父代两两交叉，后续代根据上一代候选继续组成父代池。平台会把每一步的父代、子代、Prompt 和目标文件写入 real_evolution_summary.json，用于后续生成谱系图和报告表格。")
        image(sel, "自动迭代.png", "图4-4 自动迭代页面：显示父代池、代际生成过程和阶段状态", max_height_cm=10.0)
        heading(sel, "4.6 Outputs 与 Experiments 层", 2)
        paragraph(sel, "Outputs 页面用于查看协议、Prompt、CSV、JSON 和日志。Experiments 页面用于生成课程报告中的图表，包括进化曲线、耗时收敛、准确率收敛、纠正次数、终稿对比和完整 12 协议验证图。")
        paragraph(sel, "Outputs 更偏向“原始产物查看”，用于确认某个文件是否生成、内容是否正确；Experiments 更偏向“分析产物生成”，用于把 CSV 和 JSON 转换为论文式图表。二者配合后，用户既能追踪底层文件，也能获得报告中可直接使用的分析图。")
        image(sel, "outputs.png", "图4-5 Outputs 页面：直接预览协议、CSV、JSON 和运行日志", max_height_cm=10.0)
        heading(sel, "4.7 界面与反馈呈现层", 2)
        paragraph(sel, "平台界面按工作流组织，左侧为稳定导航，右侧为当前页面说明、表单、控制台和预览区。对于耗时较长的 AI 生成和自动迭代，页面会显示任务状态、阶段日志和完成后的产物入口，避免用户误以为系统卡住。")

        heading(sel, "第五章 测试与结果分析", 1)
        heading(sel, "5.1 test 场景构造与真实生成流程", 2)
        paragraph(sel, "按照测试要求，test 场景先删除旧的临时文件，再从已有协议资产中复制 3 个初代父代。随后使用平台真实生成接口完成三代迭代，每一代包含 3 个子代，并保留 Prompt、协议文件和生成摘要。real_evolution_summary.json 记录了每个子代的父代来源、目标文件、通过率、耗时和验证情况。")
        table(doc, sel, ["代数", "协议数量", "生成方式", "结果文件"], [
            ["generation_0", "3", "从已有协议资产复制父代", "ModeQuerySEM.py、ModeQueryTKQ.py、g2_2.py"],
            ["generation_1", "3", "三个父代两两交叉生成", "test_g1_1.py、test_g1_2.py、test_g1_3.py"],
            ["generation_2", "3", "从第一代候选继续交叉", "test_g2_1.py、test_g2_2.py、test_g2_3.py"],
            ["generation_3", "3", "从第二代候选继续交叉", "test_g3_1.py、test_g3_2.py、test_g3_3.py"],
        ], font_size=8.5)
        paragraph(sel, "该流程的关键点在于：父代不是临时编写，子代不是人工伪造，每个生成结果都有 Prompt 文件、目标代码文件和验证摘要。这样既满足课程报告对“完整可执行源代码”的要求，也能说明自动迭代功能确实落到了项目文件结构中。")
        heading(sel, "5.2 完整 12 协议统一测试结果", 2)
        add_protocol_table(doc, sel)
        paragraph(sel, "统一测试表明，12 个协议均可以执行，并全部通过物理约束与信息隔离检查。大多数协议在双峰众数任务中达到 100% 通过率，ModeQueryTKQ 与 test_g3_2 为 91.67%，test_g3_3 为 83.33%。这说明平台能够生成有效候选，也能够暴露需要淘汰或继续修复的子代。")
        paragraph(sel, "表格中的通过率表示协议在多轮测试中命中任务目标的比例，耗时表示协议 execute 接口返回的平均执行时间。由于不同协议采用的候选筛选、计数和确认策略不同，准确率和耗时并不总是同步改善。报告没有简单声称“后代必然优于父代”，而是通过完整图表展示自动生成后需要继续筛选和优化的事实。")
        image(sel, "full_12_protocol_comparison.png", "图5-1 test 场景 12 个协议统一验证对比", max_height_cm=11.2)
        image(sel, "generation_accuracy_time_summary.png", "图5-2 test 场景分代准确率与耗时变化")
        image(sel, "validation_matrix_12.png", "图5-3 test 场景 12 个协议验证矩阵")
        image(sel, "accuracy_latency_scatter_test.png", "图5-4 准确率-耗时二维分布")

        heading(sel, "5.3 遗传迭代机制与非单调结果分析", 2)
        paragraph(sel, "本项目的自动迭代流程借鉴遗传算法思想，但不是简单套用传统数值优化算法。RFID 协议文件被视为一个可执行个体，协议中的帧长设置、采样策略、候选价格筛选、热点确认规则、早停条件和异常处理逻辑可以理解为基因片段。平台将多个父代放入父代池，通过 Prompt 将两个父代的关键机制交叉，再由大模型完成结构化变异和代码落盘，最后由验证沙箱给出适应度评价。")
        table(doc, sel, ["遗传算法概念", "平台中的对应实现", "工程意义"], [
            ["个体", "一个可执行 RFID 协议 Python 文件", "每个协议可以独立运行、验证和比较"],
            ["基因", "帧长、轮次、候选筛选、确认阈值、容错逻辑等协议片段", "决定协议的准确率、耗时和鲁棒性"],
            ["种群", "同一 generation 目录下的多个候选协议", "避免只生成单个结果导致偶然性过强"],
            ["选择", "根据通过率、耗时、验证结果和信息隔离结果选择父代", "把可用候选继续用于下一轮生成"],
            ["交叉", "从父代 A 和父代 B 中抽取机制并组织成新 Prompt", "复用已有协议中的有效结构"],
            ["变异", "由大模型在约束下改变筛选策略、确认规则或代码结构", "探索父代之外的新协议形式"],
            ["适应度", "任务通过率、平均耗时、物理约束、信息隔离和运行稳定性", "把“能运行”与“效果好”分开评价"],
        ], font_size=8.2)
        paragraph(sel, "测试结果中第三代并没有全部优于前两代，这是自动化协议优化中比较典型的非单调现象。原因首先在于交叉并不等价于数学上的凸组合：两个父代各自有效的局部策略在组合后可能产生冲突，例如一个父代偏向快速缩小候选集合，另一个父代偏向多轮确认，合并后若阈值、轮次或候选保留比例不匹配，就可能出现漏检或耗时上升。其次，大模型生成的变异具有探索性，它可能引入新的判断分支或保护逻辑，这些改动有时能提高鲁棒性，有时会破坏原有父代在特定分布上的优势。")
        paragraph(sel, "本次 test 场景中，test_g3_2 在统一测试中通过率为 91.67%，test_g3_3 为 83.33%，而部分父代和前两代候选保持 100%。这并不说明自动迭代失败，而说明平台已经具备区分“可执行候选”和“优质候选”的能力。若没有统一测试和验证矩阵，这类下降会被隐藏在生成成功的表象之下；通过当前平台，下降个体会被标记为需要淘汰、修复或降低选择权重的候选。")
        table(doc, sel, ["现象", "可能原因", "后续优化方式"], [
            ["后代准确率下降", "交叉后候选筛选阈值、确认轮次或价格聚类边界发生不匹配", "引入精英保留策略，保证每代最优父代直接进入下一代候选池"],
            ["耗时明显升高", "子代增加了多轮扫描、重复确认或过多保护分支", "将耗时加入适应度惩罚项，限制低收益的重复确认"],
            ["生成阶段通过而统一测试下降", "生成阶段烟雾测试样本较少，统一测试覆盖更多随机分布", "扩大验证样本，区分快速烟雾测试与最终统一评测"],
            ["部分子代只命中一个热点", "双峰任务中候选保留比例过窄，第二热点被提前过滤", "增加双峰保持约束，要求候选集合保留多个高频簇"],
            ["每代平均效果波动", "探索性变异增加了多样性，但也带来不稳定候选", "采用多目标排序或 Pareto 前沿，平衡准确率、耗时和鲁棒性"],
        ], font_size=8.2)
        paragraph(sel, "因此，自动迭代流程的目标不是保证每一代平均值都单调提升，而是在可复现的验证环境中持续产生、筛选和保留更有价值的候选。对于课程项目而言，这一点比单纯给出一个看似最优的终稿更重要：平台展示了协议生成的不确定性，也提供了把不确定性转化为实验数据、图表和工程决策的闭环。")

        heading(sel, "5.4 Experiments 图表结果", 2)
        paragraph(sel, "Experiments 模块生成的图表用于从不同角度观察迭代过程。性能进化曲线展示每代最优个体变化；耗时收敛和准确率收敛图展示整体趋势；纠正次数图反映模型生成后的修复压力；终稿图对当前场景全部协议进行排序比较。")
        image(sel, "experiments.png", "图5-5 Experiments 页面：生成进化图、终稿图和实验产物", max_height_cm=10.0)
        image(sel, "chart_1_performance_evolution_test.png", "图5-6 Experiments 性能进化曲线")
        image(sel, "chart_2_time_convergence_test.png", "图5-7 Experiments 耗时收敛曲线")
        image(sel, "chart_3_accuracy_convergence_test.png", "图5-8 Experiments 准确率收敛曲线")
        image(sel, "chart_4_correction_counts_test.png", "图5-9 Experiments 模型纠正次数")
        image(sel, "final_protocol_performance_test.png", "图5-10 Experiments 终稿协议性能对比", max_height_cm=11.2)
        paragraph(sel, "从图表可以看出，平台目前更像一个“候选协议生成与验证平台”，而不是保证每代单调提升的自动优化器。它的价值在于能够把候选协议快速生成出来，并用统一指标识别哪些子代值得继续作为父代。后续若要进一步提高优化能力，可以把当前通过率和耗时指标扩展为多目标适应度函数。")
        paragraph(sel, "在工程实现上，后续可以补充两类遗传策略。一类是精英保留策略，即每一代无论大模型生成结果如何，都强制保留当前通过率最高且耗时最低的若干协议，防止优秀父代在交叉过程中丢失。另一类是多样性约束，即在选择父代时不仅看分数，还看协议结构差异，避免多个相似子代反复杂交造成早熟收敛。这样可以使平台从“生成并筛选候选”逐步过渡到更稳定的“自动优化候选”。")
        heading(sel, "5.5 单元测试与可运行性验证", 2)
        paragraph(sel, "项目执行 python -m unittest course_project.test_course_project -v，测试覆盖平台状态读取、test 场景文件读取、AI 生成预览、自动迭代预览、单协议验证、SQLite AI 作业持久化和 Experiments 图表任务，结果为 Ran 6 tests，OK。")

        heading(sel, "第六章 设计难点", 1)
        heading(sel, "6.1 大模型生成代码的可执行性", 2)
        paragraph(sel, "大模型生成代码时可能出现类名不一致、接口缺失、导入错误或直接输出解释文字等问题。项目通过强制接口约束、代码块提取、类名修正、落盘后编译和验证失败反馈等方式降低风险。")
        heading(sel, "6.2 多任务画像的一致验证", 2)
        paragraph(sel, "一个 Task.py 可以产生同一批标签数据，但不同任务画像对应不同目标值。平台将场景数据生成器与任务画像分离，使众数、均值、方差、中位数、基数和缺失盘点能够共用场景，同时在验证时切换判定口径。")
        heading(sel, "6.3 自动迭代结果的可解释性", 2)
        paragraph(sel, "自动迭代容易变成黑盒流程。项目通过父代池列表、Prompt 文件、生成摘要、谱系图、统一测试表和审计记录说明每个子代如何产生、来自哪些父代、验证结果如何，从而提高可解释性。")
        heading(sel, "6.4 云边端扩展的工程边界", 2)
        paragraph(sel, "本项目以本地 Web 平台完成课程提交，但架构上保留了云边端映射关系。端侧数据可以替换为真实读写器采集，边缘验证可以部署到网关，云端生成和图表分析可以迁移到服务器。当前实现重点在软件闭环和可复现测试，不虚构未实现的硬件接入。")

        heading(sel, "第七章 社会价值", 1)
        paragraph(sel, "平台可用于仓储盘点、冷链监管、医疗耗材追踪、生产质量统计、图书档案管理和校园资产管理等领域。在这些场景中，RFID 不仅识别对象，还需要从大量对象中提取统计结论。自优化平台可以减少人工设计协议的重复工作，提高实验效率。")
        paragraph(sel, "从教学角度看，平台把感知层、网络层、应用层、云端处理、边缘验证、安全审计和大模型生成整合为一个可交互系统，能够帮助学生理解物联网软件的完整生命周期。从工程角度看，平台强调验证和审计，有助于降低模型生成代码直接投入使用的风险。")

        heading(sel, "第八章 总结与讨论", 1)
        paragraph(sel, "本项目完成了一个面向 RFID 多维统计任务的 Web 自优化平台，实现了场景管理、任务画像、父代协议选择、大模型交叉变异、验证沙箱、自动迭代、实验图表、outputs 预览和数据库审计等功能。项目不是单一页面或空壳演示，而是形成了从需求到协议、从协议到验证、从验证到图表分析的闭环。")
        paragraph(sel, "test 场景严格按照真实流程构造，初代父代来自已有协议资产，后续三代九个子代均由平台生成。完整 12 协议统一测试和图表结果表明，平台具备良好的可运行性、可验证性和展示效果。")
        paragraph(sel, "后续可以继续扩展父代协议库，引入更多均值、方差和分位数估计协议；改进评分函数，将准确率、耗时、能耗和鲁棒性统一为多目标优化指标；接入真实 RFID 读写器、MQTT 消息中间件和云端数据库，使本地平台进一步迁移到真实物联网部署环境。")

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
        try:
            shutil.copyfile(OUTPUT_DOCX, BACKUP_DOCX)
        except PermissionError:
            pass
    finally:
        word.Quit()


if __name__ == "__main__":
    build()
