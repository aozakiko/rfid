import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, Toplevel, Label, Entry, Button
import os
import shutil
import sys
from pathlib import Path
from importlib import import_module, reload
from itertools import combinations
import threading
import queue
import traceback
import json
import hashlib # 用于密码哈希
import subprocess

from backend_tools import artifacts as artifacts_store
from backend_tools.run_store import create_run, finish_run

# 尝试导入UI主题库
try:
    from ttkthemes import ThemedTk
except ImportError:
    messagebox.showerror(
        "缺少依赖库",
        "错误: 无法找到 'ttkthemes' 库。\n\n"
        "请在您的终端中运行 'pip install ttkthemes' 来安装它。"
    )
    sys.exit(1)

# 不再需要 Firebase
# 从重构后的模块导入核心函数
try:
    from prompt_generation import create_crossover_prompts
except ImportError:
     messagebox.showerror("模块缺失", "错误: 无法找到 'prompt_generation.py'。")
     sys.exit(1)

# 尝试导入测试依赖项
try:
    from base import IProtocol, Tag
    from Validator import ProtocolValidator
except ImportError:
    messagebox.showerror(
        "缺少依赖项",
        "错误: 无法找到 'base.py' 或 'Validator.py'。\n\n"
        "请确保这两个文件与GUI程序在同一个目录下。"
    )
    sys.exit(1)


# --- 模板常量 ---
# TASK_PY_TEMPLATE 和 CONFIG_PY_TEMPLATE 保持不变...
TASK_PY_TEMPLATE = """#!/usr/bin/env python
# -*- coding: utf-8 -*-
import random
from collections import Counter
from typing import List, Tuple, Dict

# 确保可以导入Tag类
try:
    from base import Tag
except ImportError:
    # 定义一个备用Tag类，以防独立运行此文件
    class Tag:
        def __init__(self, tag_id: int, price: int):
            self.id = tag_id
            self.price = price

# --- *** 标签生成逻辑 (多峰均势场景) *** ---
def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, any]]:
    \"\"\"生成一个具有多个频率相近的众数的标签列表。\"\"\"
    tags = []
    num_modes = random.randint(3, 5)
    if price_max - price_min + 1 < num_modes:
        num_modes = price_max - price_min + 1
    modes = sorted(random.sample(range(price_min, price_max + 1), num_modes))
    mode_probability = 0.4
    for i in range(num_tags):
        if random.random() < mode_probability:
            price = random.choice(modes)
        else:
            price = random.randint(price_min, price_max)
            while price in modes:
                price = random.randint(price_min, price_max)
        tags.append(Tag(i, price))
    true_info = {'modes': modes}
    return tags, true_info
"""

CONFIG_PY_TEMPLATE = """# --- *** 模拟参数配置 *** ---
SIMULATION_CONFIG = {
    'NUM_TAGS': 100000,    # 模拟的标签数量
    'PRICE_MIN': 1,        # 价格的最小值
    'PRICE_MAX': 1000,     # 价格的最大值
    'TOTAL_RUNS': 100      # 每个协议的重复测试次数
}
"""

# --- 核心功能模块 ---

def setup_base_environment():
    """检查并创建基础协议目录和文件。"""
    base_dir = "base_protocol"
    if not os.path.exists(base_dir):
        print(f"创建基础协议目录: {base_dir}")
        os.makedirs(base_dir)
        for i in range(1, 6):
            protocol_file = os.path.join(base_dir, f"protocol_v{i}.py")
            with open(protocol_file, "w", encoding="utf-8") as f:
                f.write(f"# 基础协议版本 {i}\n")
                f.write("# 该协议现在接收一个 Tag 对象列表\n")
                f.write("from base import IProtocol, Tag\n")
                f.write("from typing import List\n\n")
                f.write(f"class protocol_v{i}(IProtocol):\n")
                f.write("    def execute(self, tag_set: List[Tag], price_min: int, price_max: int):\n")
                f.write(f"        print(f'正在执行基础协议 {i}')\n")
                f.write("        estimated_modes = [price_min]\n")
                f.write("        total_bits_sent_by_reader = 1000\n")
                f.write("        execution_time = 50.0\n")
                f.write("        total_slots = 100\n")
                f.write("        return estimated_modes, total_bits_sent_by_reader, execution_time, total_slots\n")
    else:
        print("基础协议目录已存在。")

def import_module_from_path(module_name: str, path: Path):
    """从特定文件路径可靠地加载模块。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为模块 '{module_name}' 从路径创建规范: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# --- 本地认证辅助函数 ---
USERS_FILE = "users.json"

def _hash_password(password: str) -> str:
    """使用 SHA-256 对密码进行哈希处理。"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def _load_users() -> dict:
    """从 JSON 文件加载用户数据。"""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

def _save_users(users: dict):
    """将用户数据保存到 JSON 文件。"""
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, indent=4)
    except IOError as e:
        print(f"错误: 无法保存用户文件: {e}")

def _verify_password(stored_hash: str, provided_password: str) -> bool:
    """验证提供的密码是否与存储的哈希匹配。"""
    return stored_hash == _hash_password(provided_password)

# --- GUI应用程序 ---

class ProtocolEvoGUI(ThemedTk):
    def __init__(self):
        print("Initializing ProtocolEvoGUI...") # Debug print
        super().__init__()
        print("ThemedTk initialized.") # Debug print
        # self.withdraw() # 不再隐藏主窗口
        self.set_theme("arc")
        self.title("协议进化GUI工具")
        self.geometry("1480x920")
        self.center_window() # 尝试居中主窗口

        self.style = ttk.Style(self)
        self.font_title = ("Segoe UI", 12, "bold")
        self.font_default = ("Segoe UI", 10)
        self.font_subtitle = ("Segoe UI", 10, "bold")
        self.style.configure("TLabel", font=self.font_default)
        self.style.configure("TButton", font=self.font_default)
        self.style.configure("TNotebook.Tab", font=self.font_default)
        self.style.configure("Treeview.Heading", font=self.font_title)
        self.style.configure("GuideTitle.TLabel", font=self.font_subtitle, foreground="#1f4e79")
        self.style.configure("GuideText.TLabel", font=self.font_default, foreground="#333333")
        self.style.configure("Status.TLabel", font=self.font_default, foreground="#1f4e79")

        self.current_scene = tk.StringVar()
        self.selected_protocol_path = None
        self.selected_baseline_path = None
        self.log_queue = queue.Queue()
        self.test_running = False
        self.batch_log_queue = queue.Queue()
        self.batch_test_running = False
        self.exp_log_queue = queue.Queue()
        self.exp_running = False
        self.ai_log_queue = queue.Queue()
        self.ai_running = False

        # 本地认证状态
        self.current_username = None
        self.user_id_var = tk.StringVar(value="未登录")
        self.status_var = tk.StringVar(value="请先登录，登录后按左侧与右侧的步骤操作。")
        self.login_frame = None # 用于登录覆盖层

        # --- 主要UI容器 ---
        self.main_ui_frame = ttk.Frame(self) # 用于容纳登录成功后的主界面
        # self.main_ui_frame.pack(fill=tk.BOTH, expand=True) # 登录成功后再 pack

        # --- 顶部用户状态栏 ---
        user_status_frame = ttk.Frame(self.main_ui_frame, padding=(15, 5, 15, 0))
        user_status_frame.pack(fill=tk.X)
        ttk.Label(user_status_frame, text="当前用户:").pack(side=tk.LEFT)
        ttk.Label(user_status_frame, textvariable=self.user_id_var, font=self.font_default + ('bold',)).pack(side=tk.LEFT, padx=5)
        ttk.Button(user_status_frame, text="登出", command=self._logout).pack(side=tk.RIGHT) # 登出按钮

        # --- 主布局 ---
        main_paned_window = ttk.PanedWindow(self.main_ui_frame, orient=tk.HORIZONTAL)
        main_paned_window.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 15)) # 调整pady

        left_frame = ttk.Frame(main_paned_window, width=350)
        main_paned_window.add(left_frame, weight=1)

        right_frame = ttk.Frame(main_paned_window, width=1050)
        main_paned_window.add(right_frame, weight=4)

        self._create_control_widgets(left_frame)

        # --- 右侧 Notebook ---
        right_notebook = ttk.Notebook(right_frame)
        right_notebook.pack(fill=tk.BOTH, expand=True)

        self.guide_tab = ttk.Frame(right_notebook, padding=10)
        self.workspace_tab = ttk.Frame(right_notebook, padding=10)
        self.config_editor_tab = ttk.Frame(right_notebook, padding=10)
        self.task_editor_tab = ttk.Frame(right_notebook, padding=10)
        self.prompts_tab = ttk.Frame(right_notebook, padding=10)
        self.batch_test_tab = ttk.Frame(right_notebook, padding=10)
        self.experiments_tab = ttk.Frame(right_notebook, padding=10)
        self.baseline_tab = ttk.Frame(right_notebook, padding=10)
        self.artifacts_tab = ttk.Frame(right_notebook, padding=10)

        right_notebook.add(self.guide_tab, text="使用向导")
        right_notebook.add(self.workspace_tab, text="协议工作区")
        right_notebook.add(self.config_editor_tab, text="场景配置 (config.py)")
        right_notebook.add(self.task_editor_tab, text="任务编辑器 (Task.py)")
        right_notebook.add(self.prompts_tab, text="提示词生成")
        right_notebook.add(self.baseline_tab, text="基线协议库")
        right_notebook.add(self.batch_test_tab, text="批量测试 (Test.py)")
        right_notebook.add(self.experiments_tab, text="实验自动化 (Experiments)")
        right_notebook.add(self.artifacts_tab, text="结果中心")

        self._create_guide_widgets(self.guide_tab)
        self._create_workspace_widgets(self.workspace_tab)
        self._create_config_editor_widgets(self.config_editor_tab)
        self._create_task_editor_widgets(self.task_editor_tab)
        self._create_prompt_widgets(self.prompts_tab)
        self._create_baseline_widgets(self.baseline_tab)
        self._create_batch_test_widgets(self.batch_test_tab)
        self._create_experiment_widgets(self.experiments_tab)
        self._create_artifact_widgets(self.artifacts_tab)

        status_bar = ttk.Label(self.main_ui_frame, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status_bar.pack(fill=tk.X, padx=15, pady=(0, 8))

        self.refresh_scene_list()
        self.refresh_baseline_list()
        self.refresh_artifact_list()

        # --- 显示登录覆盖层 ---
        print("Showing login frame...") # Debug print
        self._show_login_frame()
        print("Login frame procedure called.") # Debug print

        print("GUI Initialization complete.") # Debug print

    def center_window(self):
        """将窗口居中显示在屏幕上。"""
        self.update_idletasks()
        # 获取屏幕尺寸
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        # 获取窗口尺寸
        width = self.winfo_width()
        height = self.winfo_height()
        # 如果窗口尺寸无效（例如，窗口尚未完全绘制），则使用默认值或先前设置的值
        if width <= 1 or height <= 1:
            width = 1400 # 默认宽度
            height = 900 # 默认高度

        # 计算居中位置
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')
        print(f"Window centered at {x}, {y}") # Debug print


    def _show_login_frame(self):
        """在主窗口上显示登录覆盖层。"""
        print("Creating login frame...") # Debug print
        if self.login_frame and self.login_frame.winfo_exists():
            self.login_frame.lift() # 如果已存在，提升到最前
            return

        # 创建覆盖整个主窗口的 Frame
        self.login_frame = ttk.Frame(self, style="Login.TFrame") # 使用自定义样式
        self.login_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        # 定义样式，可以设置背景色等
        self.style.configure("Login.TFrame", background="lightgrey") # 示例背景色

        # 登录内容的内部 Frame，用于居中
        content_frame = ttk.Frame(self.login_frame, style="Login.TFrame")
        content_frame.pack(expand=True) # 使其在覆盖层中居中

        ttk.Label(content_frame, text="登录 / 注册", font=self.font_title).pack(pady=(0, 10)) # 减少底部间距

        # 用户名输入
        login_username_entry = ttk.Entry(content_frame, width=25) # 减小宽度
        login_username_entry.pack(pady=3) # 减小垂直间距
        login_username_entry.insert(0, "用户名") # 提示
        login_username_entry.bind("<FocusIn>", lambda e: login_username_entry.delete(0, 'end') if login_username_entry.get() == '用户名' else None)
        login_username_entry.bind("<FocusOut>", lambda e: login_username_entry.insert(0, '用户名') if not login_username_entry.get() else None)

        # 密码输入
        login_password_entry = ttk.Entry(content_frame, show="*", width=25) # 减小宽度
        login_password_entry.pack(pady=3) # 减小垂直间距

        # 按钮 Frame
        button_frame = ttk.Frame(content_frame, style="Login.TFrame")
        button_frame.pack(pady=8) # 减小垂直间距

        ttk.Button(button_frame, text="登录", command=lambda u=login_username_entry, p=login_password_entry: self._login(u, p)).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="注册", command=lambda u=login_username_entry, p=login_password_entry: self._process_registration(u, p)).pack(side=tk.LEFT, padx=5)

        login_username_entry.focus_set()
        self.login_frame.lift() # 确保覆盖层在最上面
        print("Login frame created and lifted.") # Debug print

    def _login(self, username_entry_widget, password_entry_widget):
        """处理登录逻辑。"""
        username = username_entry_widget.get().strip()
        # 移除占位符判断
        if username == '用户名': username = ''
        password = password_entry_widget.get()

        if not username or not password:
            messagebox.showerror("错误", "用户名和密码不能为空。", parent=self.login_frame) # 父窗口改为 login_frame
            return

        users = _load_users()
        if username in users and _verify_password(users[username], password):
            self.current_username = username
            self.user_id_var.set(username)
            # messagebox.showinfo("成功", f"欢迎回来, {username}!", parent=self) # 父窗口改回 self
            if self.login_frame:
                self.login_frame.destroy() # 销毁登录覆盖层
                self.login_frame = None
            print(f"User '{username}' logged in. Showing main UI.") # Debug print
            self.main_ui_frame.pack(fill=tk.BOTH, expand=True) # 显示主UI
            self.status_var.set("已登录。请先在左侧选择场景，再按右侧页签逐步操作。")
        else:
            messagebox.showerror("错误", "用户名或密码错误。", parent=self.login_frame)

    def _process_registration(self, username_entry_widget, password_entry_widget):
        """处理注册逻辑。"""
        username = username_entry_widget.get().strip()
        if username == '用户名': username = ''
        password = password_entry_widget.get()

        if not username or not password:
            messagebox.showerror("错误", "用户名和密码不能为空。", parent=self.login_frame)
            return

        users = _load_users()
        if username in users:
            messagebox.showerror("错误", "用户名已存在。", parent=self.login_frame)
            return

        users[username] = _hash_password(password)
        _save_users(users)
        messagebox.showinfo("成功", f"用户 '{username}' 注册成功，请登录。", parent=self.login_frame)
        print(f"User '{username}' registered.") # Debug print

    def _logout(self):
        """处理用户登出。"""
        print(f"User '{self.current_username}' logging out.") # Debug print
        self.current_username = None
        self.user_id_var.set("未登录")
        self.status_var.set("已登出。请重新登录。")
        self.main_ui_frame.pack_forget() # 隐藏主UI
        self._show_login_frame() # 重新显示登录覆盖层

    # --- 界面创建函数 (大部分不变) ---
    def _add_guide_block(self, parent, title, steps):
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(fill=tk.X, pady=(0, 8))
        for i, step in enumerate(steps, 1):
            ttk.Label(frame, text=f"{i}. {step}", style="GuideText.TLabel", anchor="w").pack(fill=tk.X, padx=8, pady=1)

    def _create_guide_widgets(self, parent):
        wrapper = ttk.Frame(parent)
        wrapper.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(wrapper, text="PE-IDE 全流程操作向导", style="GuideTitle.TLabel")
        title.pack(anchor="w", pady=(0, 8))

        intro = (
            "建议按从上到下顺序操作：先在左侧创建/选择场景，再在右侧按页签执行配置、编辑、测试与实验分析。"
        )
        ttk.Label(wrapper, text=intro, style="GuideText.TLabel", wraplength=1050, justify=tk.LEFT).pack(anchor="w", pady=(0, 10))

        self._add_guide_block(wrapper, "步骤A：准备场景（左侧控制面板）", [
            "输入新场景名并点击“创建”，系统会生成 generation_0、prompts、Task.py、config.py。",
            "在“选择一个场景”中点选场景，右侧所有页签将自动切换到该场景上下文。",
            "如果看不到新场景，点击“刷新场景列表”。",
        ])

        self._add_guide_block(wrapper, "步骤B：在协议工作区进行迭代", [
            "在树形结构中选择协议文件，右侧会自动加载代码。",
            "可直接编辑代码，填写文件名并选择目标世代后“保存/更新”。",
            "点击“开始测试选中协议”执行：加载配置 -> 加载Task -> 约束验证 -> 性能测试。",
        ])

        self._add_guide_block(wrapper, "步骤C：配置测试环境", [
            "在“场景配置”中设置 NUM_TAGS/PRICE_MIN/PRICE_MAX/TOTAL_RUNS。",
            "在“任务编辑器”中定义 generate_tags()，并确保返回 (tags, true_info)。",
            "多众数建议返回 true_info['modes']，可与现有测试逻辑保持一致。",
        ])

        self._add_guide_block(wrapper, "步骤D：生成提示词并产出子代", [
            "在“提示词生成”输入至少2个父代协议名（不含 .py），点击生成。",
            "提示词会保存到当前场景 prompts 目录。",
            "将LLM生成的子代协议代码粘贴回“协议工作区”并保存到新世代。",
        ])

        self._add_guide_block(wrapper, "步骤E：自动化批测与实验绘图", [
            "“批量测试”页签用于可视化运行 Test.py（支持 all / 通配符 / 参数覆盖）。",
            "“实验自动化”页签可一键运行 Test_robust、复制CSV、生成鲁棒性和进化图表。",
            "建议先用小参数快速验证，再扩大 NUM_TAGS 和 TOTAL_RUNS 做正式实验。",
        ])

    def _create_control_widgets(self, parent):
        control_frame = ttk.LabelFrame(parent, text="控制面板", labelwidget=ttk.Label(parent, text="控制面板", font=self.font_title))
        control_frame.pack(fill=tk.BOTH, expand=True, padx=(0, 10), pady=0)
        ttk.Label(
            control_frame,
            text="推荐流程：1) 创建场景 -> 2) 选择场景 -> 3) 按右侧页签完成配置、测试和实验。",
            style="GuideText.TLabel",
            wraplength=300,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=10, pady=(8, 4))
        create_frame = ttk.Frame(control_frame, padding=10)
        create_frame.pack(fill=tk.X)
        ttk.Label(create_frame, text="新场景名:").pack(side=tk.LEFT, padx=(0, 10))
        self.scene_name_entry = ttk.Entry(create_frame)
        self.scene_name_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(create_frame, text="创建", command=self.create_scene).pack(side=tk.LEFT, padx=(10, 0))
        select_frame = ttk.Frame(control_frame, padding=10)
        select_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(select_frame, text="选择一个场景:").pack(anchor="w")
        self.scene_listbox = tk.Listbox(select_frame, exportselection=False, font=self.font_default, relief=tk.SOLID, borderwidth=1)
        self.scene_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        self.scene_listbox.bind("<<ListboxSelect>>", self.on_scene_select)
        ttk.Button(select_frame, text="刷新场景列表", command=self.refresh_scene_list).pack(fill=tk.X)

    def _create_workspace_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（协议工作区）", [
            "先在左侧选择场景，再在树中选择一个协议文件。",
            "编辑代码后设置“文件名 + 目标世代”，点击“保存/更新”。",
            "点击“开始测试选中协议”查看完整验证与性能日志。",
        ])

        ws_pane = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        ws_pane.pack(fill=tk.BOTH, expand=True)
        tree_frame_container = ttk.Frame(ws_pane)
        ws_pane.add(tree_frame_container, weight=1)
        tree_frame = ttk.LabelFrame(tree_frame_container, text="场景内容", labelwidget=ttk.Label(tree_frame_container, text="场景内容", font=self.font_title))
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=(0, 5))
        self.protocol_tree = ttk.Treeview(tree_frame, show="tree headings", columns=("protocol",))
        self.protocol_tree.heading("protocol", text="世代 / 协议")
        self.protocol_tree.pack(fill=tk.BOTH, expand=True, pady=5)
        self.protocol_tree.bind("<<TreeviewSelect>>", self.on_protocol_select)
        tree_btn_frame = ttk.Frame(tree_frame)
        tree_btn_frame.pack(fill=tk.X)
        ttk.Button(tree_btn_frame, text="刷新", command=self.populate_protocol_tree).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,2))
        ttk.Button(tree_btn_frame, text="删除", command=self.delete_selected_protocol).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2,0))
        editor_pane = ttk.PanedWindow(ws_pane, orient=tk.VERTICAL)
        ws_pane.add(editor_pane, weight=3)
        code_frame = ttk.LabelFrame(editor_pane, text="代码编辑器", labelwidget=ttk.Label(editor_pane, text="代码编辑器", font=self.font_title))
        editor_pane.add(code_frame, weight=3)
        save_bar = ttk.Frame(code_frame, padding=5)
        save_bar.pack(fill=tk.X)
        ttk.Label(save_bar, text="文件名:").pack(side=tk.LEFT)
        self.filename_entry = ttk.Entry(save_bar)
        self.filename_entry.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Label(save_bar, text="目标世代:").pack(side=tk.LEFT)
        self.generation_selector = ttk.Combobox(save_bar, state="readonly", width=15, font=self.font_default)
        self.generation_selector.pack(side=tk.LEFT, padx=5)
        ttk.Button(save_bar, text="保存/更新协议", command=self.save_protocol).pack(side=tk.LEFT)
        self.code_text = scrolledtext.ScrolledText(code_frame, wrap=tk.WORD, relief=tk.SOLID, borderwidth=1, font=("Courier New", 10))
        self.code_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        output_frame = ttk.LabelFrame(editor_pane, text="测试输出", labelwidget=ttk.Label(editor_pane, text="测试输出", font=self.font_title))
        editor_pane.add(output_frame, weight=1)
        run_bar = ttk.Frame(output_frame, padding=5)
        run_bar.pack(fill=tk.X)
        self.test_button = ttk.Button(run_bar, text="开始测试选中协议", command=self.start_test_protocol)
        self.test_button.pack(side=tk.LEFT)
        ttk.Label(run_bar, text="执行流程: 配置加载 -> 任务加载 -> 约束验证 -> 性能测试", style="GuideText.TLabel").pack(side=tk.LEFT, padx=12)
        ttk.Button(run_bar, text="清空控制台", command=lambda: self.output_console.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=5)
        self.output_console = scrolledtext.ScrolledText(output_frame, wrap=tk.WORD, state="disabled", bg="#2B2B2B", fg="#A9B7C6", relief=tk.SOLID, borderwidth=1, font=("Courier New", 10))
        self.output_console.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_config_editor_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（场景配置）", [
            "该配置用于控制测试规模和价格范围。",
            "修改参数后点击“保存配置”，随后测试会读取新配置。",
            "建议先用小规模参数验证逻辑，再扩大参数跑正式实验。",
        ])

        config_frame = ttk.Frame(parent)
        config_frame.pack(fill=tk.BOTH, expand=True)
        self.config_file_label = ttk.Label(config_frame, text="当前未选择场景", font=self.font_default)
        self.config_file_label.pack(fill=tk.X, pady=5, anchor="w")
        lf = ttk.LabelFrame(config_frame, text="模拟参数", labelwidget=ttk.Label(config_frame, text="模拟参数", font=self.font_title))
        lf.pack(fill=tk.X, anchor=tk.N, pady=10)
        self.config_entries = {}
        params = ['NUM_TAGS', 'PRICE_MIN', 'PRICE_MAX', 'TOTAL_RUNS']
        descriptions = ['模拟的标签数量', '价格的最小值', '价格的最大值', '每个协议的重复测试次数']
        for i, param in enumerate(params):
            entry_frame = ttk.Frame(lf, padding=5)
            entry_frame.pack(fill=tk.X)
            label_text = f"{param} ({descriptions[i]}):"
            label = ttk.Label(entry_frame, text=label_text, width=40)
            label.pack(side=tk.LEFT, padx=(0, 10))
            entry = ttk.Entry(entry_frame)
            entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
            self.config_entries[param] = entry
        self.save_config_button = ttk.Button(config_frame, text="保存配置", command=self.save_config_file, state="disabled")
        self.save_config_button.pack(pady=20)

    def _create_task_editor_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（任务编辑器）", [
            "在 generate_tags() 中定义你要模拟的标签分布。",
            "函数必须返回 (tags, true_info)，用于后续正确性判断。",
            "保存后建议立刻去“协议工作区”测试一个协议确认任务逻辑。",
        ])

        task_frame = ttk.Frame(parent)
        task_frame.pack(fill=tk.BOTH, expand=True)
        self.task_file_label = ttk.Label(task_frame, text="当前未选择场景", font=self.font_default)
        self.task_file_label.pack(fill=tk.X, pady=5, anchor="w")
        self.task_code_text = scrolledtext.ScrolledText(task_frame, wrap=tk.WORD, relief=tk.SOLID, borderwidth=1, font=("Courier New", 10))
        self.task_code_text.pack(fill=tk.BOTH, expand=True, pady=5)
        self.save_task_button = ttk.Button(task_frame, text="保存 Task.py", command=self.save_task_file, state="disabled")
        self.save_task_button.pack(pady=10)

    def _create_prompt_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（提示词生成）", [
            "输入父代协议名（不带 .py），至少 2 个。",
            "点击生成后，系统会优先在当前场景各世代查找，找不到再查 base_protocol。",
            "生成结果保存到当前场景 prompts 目录。",
        ])

        lf = ttk.LabelFrame(parent, text="指定5个父代生成交叉提示词", labelwidget=ttk.Label(parent, text="指定5个父代生成交叉提示词", font=self.font_title))
        lf.pack(fill=tk.X, anchor=tk.N, pady=10)
        self.parent_entries = []
        for i in range(5):
            entry_frame = ttk.Frame(lf, padding=5)
            entry_frame.pack(fill=tk.X)
            label = ttk.Label(entry_frame, text=f"父代协议 {i+1}:", width=15)
            label.pack(side=tk.LEFT, padx=(0, 10))
            entry = ttk.Entry(entry_frame)
            entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
            self.parent_entries.append(entry)
        ttk.Button(lf, text="生成交叉提示词", command=self.generate_crossover_prompts).pack(pady=15)

        ai_lf = ttk.LabelFrame(parent, text="AI 自优化协议生成", labelwidget=ttk.Label(parent, text="AI 自优化协议生成", font=self.font_title))
        ai_lf.pack(fill=tk.BOTH, expand=True, anchor=tk.N, pady=10)

        ai_tip = ttk.Label(
            ai_lf,
            text="说明：API Key 优先从下方临时输入读取；若留空，则从环境变量读取，不会写入仓库。",
            style="GuideText.TLabel",
        )
        ai_tip.pack(fill=tk.X, padx=5, pady=(5, 0))

        ai_row1 = ttk.Frame(ai_lf, padding=5)
        ai_row1.pack(fill=tk.X)
        ttk.Label(ai_row1, text="父代A:", width=10).pack(side=tk.LEFT)
        self.ai_parent_a_entry = ttk.Entry(ai_row1, width=18)
        self.ai_parent_a_entry.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ai_row1, text="父代B:", width=10).pack(side=tk.LEFT)
        self.ai_parent_b_entry = ttk.Entry(ai_row1, width=18)
        self.ai_parent_b_entry.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ai_row1, text="输出协议名:", width=12).pack(side=tk.LEFT)
        self.ai_protocol_name_entry = ttk.Entry(ai_row1, width=20)
        self.ai_protocol_name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ai_row2 = ttk.Frame(ai_lf, padding=5)
        ai_row2.pack(fill=tk.X)
        ttk.Label(ai_row2, text="目标世代:", width=10).pack(side=tk.LEFT)
        self.ai_target_generation_entry = ttk.Entry(ai_row2, width=18)
        self.ai_target_generation_entry.insert(0, "next")
        self.ai_target_generation_entry.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ai_row2, text="验证标签数:", width=12).pack(side=tk.LEFT)
        self.ai_validate_num_tags_entry = ttk.Entry(ai_row2, width=10)
        self.ai_validate_num_tags_entry.insert(0, "120")
        self.ai_validate_num_tags_entry.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ai_row2, text="修复轮数:", width=10).pack(side=tk.LEFT)
        self.ai_repair_rounds_entry = ttk.Entry(ai_row2, width=10)
        self.ai_repair_rounds_entry.insert(0, "1")
        self.ai_repair_rounds_entry.pack(side=tk.LEFT)

        ai_row3 = ttk.Frame(ai_lf, padding=5)
        ai_row3.pack(fill=tk.X)
        ttk.Label(ai_row3, text="Base URL:", width=10).pack(side=tk.LEFT)
        self.ai_base_url_entry = ttk.Entry(ai_row3)
        self.ai_base_url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        ttk.Label(ai_row3, text="Model:", width=8).pack(side=tk.LEFT)
        self.ai_model_entry = ttk.Entry(ai_row3, width=24)
        self.ai_model_entry.pack(side=tk.LEFT)

        ai_row4 = ttk.Frame(ai_lf, padding=5)
        ai_row4.pack(fill=tk.X)
        ttk.Label(ai_row4, text="接口路径:", width=10).pack(side=tk.LEFT)
        self.ai_endpoint_entry = ttk.Entry(ai_row4, width=24)
        self.ai_endpoint_entry.insert(0, "/v1/chat/completions")
        self.ai_endpoint_entry.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ai_row4, text="Key 环境变量:", width=12).pack(side=tk.LEFT)
        self.ai_key_env_entry = ttk.Entry(ai_row4, width=18)
        self.ai_key_env_entry.insert(0, "GEEK_API_KEY")
        self.ai_key_env_entry.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(ai_row4, text="临时 Key:", width=8).pack(side=tk.LEFT)
        self.ai_key_value_entry = ttk.Entry(ai_row4, width=28, show="*")
        self.ai_key_value_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ai_row5 = ttk.Frame(ai_lf, padding=5)
        ai_row5.pack(fill=tk.X)
        self.ai_run_button = ttk.Button(ai_row5, text="运行 AI 自优化", command=self.start_ai_protocol_optimization)
        self.ai_run_button.pack(side=tk.LEFT)
        self.ai_models_button = ttk.Button(ai_row5, text="列出可用模型", command=self.list_ai_models)
        self.ai_models_button.pack(side=tk.LEFT, padx=8)
        ttk.Button(ai_row5, text="清空输出", command=lambda: self._clear_console(self.ai_output_console)).pack(side=tk.LEFT)

        ai_out = ttk.LabelFrame(ai_lf, text="AI 自优化输出")
        ai_out.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.ai_output_console = scrolledtext.ScrolledText(ai_out, wrap=tk.WORD, state="disabled", bg="#2B2B2B", fg="#A9B7C6", relief=tk.SOLID, borderwidth=1, font=("Courier New", 10), height=14)
        self.ai_output_console.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_baseline_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（基线协议库）", [
            "在左侧选择 base_protocol 中的协议文件，可直接查看和编辑。",
            "保存后可点击“复制到当前场景 generation_0”作为初始基线。",
            "也可点击“在当前场景测试此基线协议”快速验证性能。",
        ])

        pane = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(pane, text="base_protocol 文件列表")
        pane.add(left, weight=1)
        self.baseline_listbox = tk.Listbox(left, exportselection=False, font=self.font_default, relief=tk.SOLID, borderwidth=1)
        self.baseline_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.baseline_listbox.bind("<<ListboxSelect>>", self.on_baseline_select)

        btn_row = ttk.Frame(left, padding=5)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="刷新列表", command=self.refresh_baseline_list).pack(side=tk.LEFT)

        right = ttk.LabelFrame(pane, text="基线协议编辑器")
        pane.add(right, weight=3)

        op_row = ttk.Frame(right, padding=5)
        op_row.pack(fill=tk.X)
        ttk.Button(op_row, text="保存基线协议", command=self.save_baseline_protocol).pack(side=tk.LEFT)
        ttk.Button(op_row, text="复制到当前场景 generation_0", command=self.copy_baseline_to_scene).pack(side=tk.LEFT, padx=8)
        ttk.Button(op_row, text="在当前场景测试此基线协议", command=self.test_baseline_in_scene).pack(side=tk.LEFT, padx=8)

        self.baseline_path_label = ttk.Label(right, text="未选择基线协议", style="GuideText.TLabel")
        self.baseline_path_label.pack(fill=tk.X, padx=5)

        self.baseline_code_text = scrolledtext.ScrolledText(right, wrap=tk.WORD, relief=tk.SOLID, borderwidth=1, font=("Courier New", 10))
        self.baseline_code_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_artifact_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（结果中心）", [
            "点击“刷新结果列表”扫描 CSV/PDF/PNG/JPG/JPEG/TXT/JSON。",
            "双击条目可直接打开文件；也可点击“打开输出目录”。",
            "用于快速定位 Test_robust 结果CSV和各类绘图文件。",
        ])

        top = ttk.Frame(parent, padding=5)
        top.pack(fill=tk.X)
        ttk.Button(top, text="刷新结果列表", command=self.refresh_artifact_list).pack(side=tk.LEFT)
        ttk.Button(top, text="打开 Experiments 目录", command=lambda: self.open_output_folder("Experiments")).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="打开项目根目录", command=lambda: self.open_output_folder("." )).pack(side=tk.LEFT, padx=8)

        self.artifact_listbox = tk.Listbox(parent, exportselection=False, font=self.font_default, relief=tk.SOLID, borderwidth=1)
        self.artifact_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.artifact_listbox.bind("<Double-Button-1>", self.open_selected_artifact)

    def _create_batch_test_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（批量测试）", [
            "选择场景，并设置协议模式（all、g10_* 或多个协议名）。",
            "可选覆盖 NUM_TAGS/TOTAL_RUNS/PRICE_MIN/PRICE_MAX；留空则使用场景默认值。",
            "点击“运行批量测试”，实时查看 Test.py 输出。",
        ])

        cfg = ttk.LabelFrame(parent, text="批量测试参数", labelwidget=ttk.Label(parent, text="批量测试参数", font=self.font_title))
        cfg.pack(fill=tk.X, pady=(0, 10))

        row1 = ttk.Frame(cfg, padding=5)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="场景:", width=10).pack(side=tk.LEFT)
        self.batch_scene_selector = ttk.Combobox(row1, state="readonly", width=28, font=self.font_default)
        self.batch_scene_selector.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row1, text="协议模式:", width=10).pack(side=tk.LEFT)
        self.batch_patterns_entry = ttk.Entry(row1)
        self.batch_patterns_entry.insert(0, "all")
        self.batch_patterns_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        row2 = ttk.Frame(cfg, padding=5)
        row2.pack(fill=tk.X)
        self.batch_num_tags_entry = self._make_labeled_entry(row2, "NUM_TAGS", "2000")
        self.batch_total_runs_entry = self._make_labeled_entry(row2, "TOTAL_RUNS", "3")
        self.batch_price_min_entry = self._make_labeled_entry(row2, "PRICE_MIN", "1")
        self.batch_price_max_entry = self._make_labeled_entry(row2, "PRICE_MAX", "1000")

        row3 = ttk.Frame(cfg, padding=5)
        row3.pack(fill=tk.X)
        self.batch_test_button = ttk.Button(row3, text="运行批量测试", command=self.start_batch_test)
        self.batch_test_button.pack(side=tk.LEFT)
        ttk.Button(row3, text="清空输出", command=lambda: self._clear_console(self.batch_output_console)).pack(side=tk.LEFT, padx=8)

        out = ttk.LabelFrame(parent, text="批量测试输出", labelwidget=ttk.Label(parent, text="批量测试输出", font=self.font_title))
        out.pack(fill=tk.BOTH, expand=True)
        self.batch_output_console = scrolledtext.ScrolledText(out, wrap=tk.WORD, state="disabled", bg="#2B2B2B", fg="#A9B7C6", relief=tk.SOLID, borderwidth=1, font=("Courier New", 10))
        self.batch_output_console.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _create_experiment_widgets(self, parent):
        self._add_guide_block(parent, "本页步骤（Experiments 自动化）", [
            "模块A（鲁棒性测试配置）设置协议、因素与采样参数，执行后生成CSV。",
            "模块B（鲁棒性绘图）从CSV选择因素并生成对应图表。",
            "模块C（进化图绘制）选择场景与图类，一键输出目标图表。",
        ])

        cfg = ttk.LabelFrame(parent, text="模块A：鲁棒性测试 (Test_robust.py)", labelwidget=ttk.Label(parent, text="模块A：鲁棒性测试 (Test_robust.py)", font=self.font_title))
        cfg.pack(fill=tk.X, pady=(0, 8))

        row0 = ttk.Frame(cfg, padding=5)
        row0.pack(fill=tk.X)
        ttk.Label(row0, text="协议列表(name:source,逗号分隔):", width=30).pack(side=tk.LEFT)
        self.exp_protocols_entry = ttk.Entry(row0)
        self.exp_protocols_entry.insert(0, "g10_4:single_mode,g8_3:multi_mode")
        self.exp_protocols_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        row1 = ttk.Frame(cfg, padding=5)
        row1.pack(fill=tk.X)
        self.exp_factor_dominance = tk.BooleanVar(value=False)
        self.exp_factor_scale = tk.BooleanVar(value=False)
        self.exp_factor_density = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="dominance", variable=self.exp_factor_dominance).pack(side=tk.LEFT)
        ttk.Checkbutton(row1, text="scale", variable=self.exp_factor_scale).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(row1, text="density", variable=self.exp_factor_density).pack(side=tk.LEFT)

        row2 = ttk.Frame(cfg, padding=5)
        row2.pack(fill=tk.X)
        self.exp_num_tags_entry = self._make_labeled_entry(row2, "num_tags", "10000")
        self.exp_price_min_entry = self._make_labeled_entry(row2, "price_min", "1")
        self.exp_price_max_entry = self._make_labeled_entry(row2, "price_max", "1000")
        self.exp_total_runs_entry = self._make_labeled_entry(row2, "total_runs", "10")
        self.exp_point_count_entry = self._make_labeled_entry(row2, "point_count", "10")
        self.exp_density_max_entry = self._make_labeled_entry(row2, "density_max", "10000")

        row3 = ttk.Frame(cfg, padding=5)
        row3.pack(fill=tk.X)
        ttk.Label(row3, text="输出CSV:", width=10).pack(side=tk.LEFT)
        self.exp_output_csv_entry = ttk.Entry(row3, width=40)
        self.exp_output_csv_entry.insert(0, "sensitivity_results_2.csv")
        self.exp_output_csv_entry.pack(side=tk.LEFT, padx=(5, 10))
        self.exp_run_robust_button = ttk.Button(row3, text="运行鲁棒性测试", command=self.start_experiment_robust)
        self.exp_run_robust_button.pack(side=tk.LEFT)

        plot_cfg = ttk.LabelFrame(parent, text="模块B：鲁棒性绘图 (plot_robust.py)")
        plot_cfg.pack(fill=tk.X, pady=(0, 8))

        prow1 = ttk.Frame(plot_cfg, padding=5)
        prow1.pack(fill=tk.X)
        ttk.Label(prow1, text="输入CSV:", width=10).pack(side=tk.LEFT)
        self.exp_source_csv_entry = ttk.Entry(prow1, width=40)
        self.exp_source_csv_entry.insert(0, "Experiments/sensitivity_results.csv")
        self.exp_source_csv_entry.pack(side=tk.LEFT, padx=(5, 10))
        ttk.Label(prow1, text="输出目录:", width=10).pack(side=tk.LEFT)
        self.exp_plot_output_dir_entry = ttk.Entry(prow1, width=25)
        self.exp_plot_output_dir_entry.insert(0, "Experiments")
        self.exp_plot_output_dir_entry.pack(side=tk.LEFT)

        prow2 = ttk.Frame(plot_cfg, padding=5)
        prow2.pack(fill=tk.X)
        self.plot_factor_dominance = tk.BooleanVar(value=False)
        self.plot_factor_scale = tk.BooleanVar(value=False)
        self.plot_factor_density = tk.BooleanVar(value=True)
        ttk.Checkbutton(prow2, text="dominance图", variable=self.plot_factor_dominance).pack(side=tk.LEFT)
        ttk.Checkbutton(prow2, text="scale图", variable=self.plot_factor_scale).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(prow2, text="density图", variable=self.plot_factor_density).pack(side=tk.LEFT)
        self.exp_copy_plot_button = ttk.Button(prow2, text="生成鲁棒性图表", command=self.start_experiment_plot_robust)
        self.exp_copy_plot_button.pack(side=tk.LEFT, padx=12)

        evo_cfg = ttk.LabelFrame(parent, text="模块C：进化图绘制 (plot_evolution_results.py)")
        evo_cfg.pack(fill=tk.X, pady=(0, 8))

        erow1 = ttk.Frame(evo_cfg, padding=5)
        erow1.pack(fill=tk.X)
        ttk.Label(erow1, text="场景:", width=10).pack(side=tk.LEFT)
        self.exp_evo_scenario_selector = ttk.Combobox(erow1, state="readonly", width=20, font=self.font_default)
        self.exp_evo_scenario_selector['values'] = ["single_peak", "multi_peak"]
        self.exp_evo_scenario_selector.set("single_peak")
        self.exp_evo_scenario_selector.pack(side=tk.LEFT, padx=(5, 15))
        ttk.Label(erow1, text="数据JSON:", width=10).pack(side=tk.LEFT)
        self.exp_evo_data_entry = ttk.Entry(erow1, width=35)
        self.exp_evo_data_entry.insert(0, "evolution_data_combined.json")
        self.exp_evo_data_entry.pack(side=tk.LEFT, padx=(5, 10))
        ttk.Label(erow1, text="输出目录:", width=10).pack(side=tk.LEFT)
        self.exp_evo_output_dir_entry = ttk.Entry(erow1, width=20)
        self.exp_evo_output_dir_entry.insert(0, "Experiments")
        self.exp_evo_output_dir_entry.pack(side=tk.LEFT)

        erow2 = ttk.Frame(evo_cfg, padding=5)
        erow2.pack(fill=tk.X)
        self.evo_chart_performance = tk.BooleanVar(value=False)
        self.evo_chart_time = tk.BooleanVar(value=False)
        self.evo_chart_accuracy = tk.BooleanVar(value=False)
        self.evo_chart_correction = tk.BooleanVar(value=True)
        ttk.Checkbutton(erow2, text="performance", variable=self.evo_chart_performance).pack(side=tk.LEFT)
        ttk.Checkbutton(erow2, text="time", variable=self.evo_chart_time).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(erow2, text="accuracy", variable=self.evo_chart_accuracy).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(erow2, text="correction", variable=self.evo_chart_correction).pack(side=tk.LEFT, padx=8)
        self.exp_plot_evo_button = ttk.Button(erow2, text="生成进化图表", command=self.start_plot_evolution_results)
        self.exp_plot_evo_button.pack(side=tk.LEFT, padx=12)
        self.exp_plot_final_button = ttk.Button(erow2, text="运行最终手工图(plot.py)", command=self.start_plot_final)
        self.exp_plot_final_button.pack(side=tk.LEFT)
        ttk.Button(erow2, text="清空输出", command=lambda: self._clear_console(self.exp_output_console)).pack(side=tk.LEFT, padx=8)

        out = ttk.LabelFrame(parent, text="Experiments 输出", labelwidget=ttk.Label(parent, text="Experiments 输出", font=self.font_title))
        out.pack(fill=tk.BOTH, expand=True)
        self.exp_output_console = scrolledtext.ScrolledText(out, wrap=tk.WORD, state="disabled", bg="#2B2B2B", fg="#A9B7C6", relief=tk.SOLID, borderwidth=1, font=("Courier New", 10))
        self.exp_output_console.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _make_labeled_entry(self, parent, label_text, default_text):
        ttk.Label(parent, text=f"{label_text}:").pack(side=tk.LEFT)
        entry = ttk.Entry(parent, width=8)
        entry.insert(0, default_text)
        entry.pack(side=tk.LEFT, padx=(4, 10))
        return entry

    def _clear_console(self, console):
        console.config(state="normal")
        console.delete("1.0", tk.END)
        console.config(state="disabled")

    def _append_console(self, console, message):
        console.config(state="normal")
        console.insert(tk.END, message + "\n")
        console.see(tk.END)
        console.config(state="disabled")

    def _build_test_command(self):
        scene_name = self.batch_scene_selector.get().strip()
        if not scene_name:
            raise ValueError("请先选择一个场景。")

        cmd = [sys.executable, "-u", "Test.py", "--scene", scene_name]

        overrides = [
            ("--num-tags", self.batch_num_tags_entry.get().strip()),
            ("--total-runs", self.batch_total_runs_entry.get().strip()),
            ("--price-min", self.batch_price_min_entry.get().strip()),
            ("--price-max", self.batch_price_max_entry.get().strip()),
        ]
        for flag, value in overrides:
            if value:
                if not value.isdigit():
                    raise ValueError(f"{flag} 的值必须是非负整数。")
                cmd.extend([flag, value])

        patterns_text = self.batch_patterns_entry.get().strip()
        if not patterns_text:
            cmd.append("all")
        else:
            patterns = [p.strip() for p in patterns_text.replace(",", " ").split() if p.strip()]
            if not patterns:
                patterns = ["all"]
            cmd.extend(patterns)

        return cmd

    def start_batch_test(self):
        if self.batch_test_running:
            return
        try:
            cmd = self._build_test_command()
        except ValueError as e:
            messagebox.showwarning("参数无效", str(e))
            return

        self.batch_test_running = True
        self.batch_test_button.config(text="批量测试运行中...")
        self.status_var.set("批量测试已启动，正在执行 Test.py。")
        self._clear_console(self.batch_output_console)

        project_root = Path(__file__).resolve().parent
        artifacts_before = artifacts_store.snapshot(project_root)
        run = create_run(kind="batch_test", scene=self.batch_scene_selector.get().strip() or None, command=cmd)

        threading.Thread(
            target=self._run_command_in_thread,
            args=(cmd, self.batch_log_queue, run.id, artifacts_before),
            daemon=True,
        ).start()
        self.after(100, self._process_batch_log_queue)

    def _process_batch_log_queue(self):
        try:
            while True:
                message = self.batch_log_queue.get_nowait()
                if message is None:
                    self.batch_test_running = False
                    self.batch_test_button.config(text="运行批量测试")
                    self.status_var.set("批量测试已完成。")
                    self.refresh_artifact_list()
                    return
                self._append_console(self.batch_output_console, message)
        except queue.Empty:
            self.after(100, self._process_batch_log_queue)

    def _infer_run_kind(self, command: list[str]) -> str:
        if "Test.py" in command:
            return "batch_test"
        if "ai_protocol_optimizer.py" in command:
            return "ai_list_models" if "--list-models" in command else "ai_optimize"
        if "Experiments/Test_robust.py" in command or "Experiments\\Test_robust.py" in command:
            return "exp_robust"
        if "Experiments/plot_robust.py" in command or "Experiments\\plot_robust.py" in command:
            return "exp_plot_robust"
        if "Experiments/plot_evolution_results.py" in command or "Experiments\\plot_evolution_results.py" in command:
            return "exp_plot_evolution"
        if "Experiments/plot.py" in command or "Experiments\\plot.py" in command:
            return "exp_plot_final"
        return "command"

    def _run_command_in_thread(self, command, log_queue, run_id=None, artifacts_before=None, env_overrides=None):
        project_root = Path(__file__).resolve().parent

        def log(message):
            log_queue.put(message)

        log("$ " + " ".join(command))

        output_lines = []
        def log_collect(message):
            output_lines.append(message)
            log(message)

        env = os.environ.copy()
        # Force UTF-8 for child Python processes to avoid GBK Unicode errors
        # when logs contain symbols like '✅' and to prevent mojibake.
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if env_overrides:
            env.update(env_overrides)

        ret_code = None
        try:
            process = subprocess.Popen(
                command,
                cwd=str(project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert process.stdout is not None
            for line in process.stdout:
                log_collect(line.rstrip("\n"))
            ret_code = process.wait()
            log_collect(f"\n--- 命令结束，退出码: {ret_code} ---")
        except Exception as e:
            log_collect(f"\nFATAL ERROR: 执行命令失败: {e}")
            log_collect(f"TRACEBACK:\n{traceback.format_exc()}")
        finally:
            try:
                if run_id is not None and artifacts_before is not None:
                    artifacts_after = artifacts_store.snapshot(project_root)
                    new_artifacts = artifacts_store.diff(artifacts_before, artifacts_after, project_root)
                    status = "success" if (ret_code == 0) else "failed"
                    finish_run(
                        run_id=int(run_id),
                        status=status,
                        exit_code=ret_code,
                        output="\n".join(output_lines),
                        artifacts=new_artifacts,
                    )
            except Exception:
                # 落库失败不影响 GUI 主流程
                pass
            log_queue.put(None)

    def start_experiment_robust(self):
        selected_factors = []
        if self.exp_factor_dominance.get():
            selected_factors.append("dominance")
        if self.exp_factor_scale.get():
            selected_factors.append("scale")
        if self.exp_factor_density.get():
            selected_factors.append("density")
        if not selected_factors:
            messagebox.showwarning("参数无效", "请至少选择一个因素（dominance/scale/density）。")
            return

        command = [
            sys.executable,
            "-u",
            "Experiments/Test_robust.py",
            "--protocols",
            self.exp_protocols_entry.get().strip(),
            "--factors",
            ",".join(selected_factors),
            "--num-tags",
            self.exp_num_tags_entry.get().strip(),
            "--price-min",
            self.exp_price_min_entry.get().strip(),
            "--price-max",
            self.exp_price_max_entry.get().strip(),
            "--total-runs",
            self.exp_total_runs_entry.get().strip(),
            "--point-count",
            self.exp_point_count_entry.get().strip(),
            "--density-max",
            self.exp_density_max_entry.get().strip(),
            "--output",
            self.exp_output_csv_entry.get().strip(),
        ]

        self._start_experiment_action(
            button=self.exp_run_robust_button,
            busy_text="鲁棒性测试运行中...",
            idle_text="运行鲁棒性测试",
            command=command,
            pre_action=None,
        )

    def start_experiment_plot_robust(self):
        factors = []
        if self.plot_factor_dominance.get():
            factors.append("dominance")
        if self.plot_factor_scale.get():
            factors.append("scale")
        if self.plot_factor_density.get():
            factors.append("density")
        if not factors:
            messagebox.showwarning("参数无效", "请至少选择一个绘图因素。")
            return

        command = [
            sys.executable,
            "-u",
            "Experiments/plot_robust.py",
            "--csv",
            self.exp_source_csv_entry.get().strip(),
            "--factors",
            ",".join(factors),
            "--output-dir",
            self.exp_plot_output_dir_entry.get().strip(),
            "--no-show",
        ]

        self._start_experiment_action(
            button=self.exp_copy_plot_button,
            busy_text="鲁棒性绘图运行中...",
            idle_text="生成鲁棒性图表",
            command=command,
            pre_action=None,
            env_overrides={"MPLBACKEND": "Agg"},
        )

    def start_plot_evolution_results(self):
        charts = []
        if self.evo_chart_performance.get():
            charts.append("performance")
        if self.evo_chart_time.get():
            charts.append("time")
        if self.evo_chart_accuracy.get():
            charts.append("accuracy")
        if self.evo_chart_correction.get():
            charts.append("correction")
        if not charts:
            messagebox.showwarning("参数无效", "请至少选择一个进化图表类型。")
            return

        command = [
            sys.executable,
            "-u",
            "Experiments/plot_evolution_results.py",
            "--scenario",
            self.exp_evo_scenario_selector.get().strip(),
            "--charts",
            ",".join(charts),
            "--data",
            self.exp_evo_data_entry.get().strip(),
            "--output-dir",
            self.exp_evo_output_dir_entry.get().strip(),
            "--no-show",
        ]

        self._start_experiment_action(
            button=self.exp_plot_evo_button,
            busy_text="进化图绘制运行中...",
            idle_text="生成进化图表",
            command=command,
            pre_action=None,
            env_overrides={"MPLBACKEND": "Agg"},
        )

    def start_plot_final(self):
        self._start_experiment_action(
            button=self.exp_plot_final_button,
            busy_text="plot.py 运行中...",
            idle_text="运行最终手工图(plot.py)",
            command=[sys.executable, "-u", "Experiments/plot.py"],
            pre_action=None,
            env_overrides={"MPLBACKEND": "Agg"},
        )

    def _start_experiment_action(self, button, busy_text, idle_text, command, pre_action=None, env_overrides=None):
        if self.exp_running:
            return

        self.exp_running = True
        button.config(text=busy_text)
        self.status_var.set("Experiments 自动化任务运行中，请查看输出日志。")

        thread = threading.Thread(
            target=self._run_experiment_action_in_thread,
            args=(command, pre_action, self.exp_log_queue, env_overrides),
            daemon=True,
        )
        thread.start()
        self.after(100, lambda: self._process_exp_log_queue(button, idle_text))

    def _run_experiment_action_in_thread(self, command, pre_action, log_queue, env_overrides):
        def log(message):
            log_queue.put(message)

        try:
            project_root = Path(__file__).resolve().parent
            artifacts_before = artifacts_store.snapshot(project_root)
            kind = self._infer_run_kind(command)
            scene = self.current_scene.get().strip() or None
            run = create_run(kind=kind, scene=scene, command=command)

            if pre_action is not None:
                pre_action(log)
            self._run_command_in_thread(
                command,
                log_queue,
                run.id,
                artifacts_before,
                env_overrides=env_overrides,
            )
        except Exception as e:
            log(f"\nFATAL ERROR: 实验动作执行失败: {e}")
            log(f"TRACEBACK:\n{traceback.format_exc()}")
            log_queue.put(None)

    def _process_exp_log_queue(self, button, idle_text):
        try:
            while True:
                message = self.exp_log_queue.get_nowait()
                if message is None:
                    self.exp_running = False
                    button.config(text=idle_text)
                    self.status_var.set("Experiments 自动化任务已完成。")
                    self.refresh_artifact_list()
                    return
                self._append_console(self.exp_output_console, message)
        except queue.Empty:
            self.after(100, lambda: self._process_exp_log_queue(button, idle_text))

    def _collect_ai_env_overrides(self):
        env_overrides = {}
        key_env = self.ai_key_env_entry.get().strip() or "GEEK_API_KEY"
        key_value = self.ai_key_value_entry.get().strip()
        if key_value:
            env_overrides[key_env] = key_value
        return env_overrides if env_overrides else None

    def _start_ai_action(self, button, busy_text, idle_text, command, env_overrides=None):
        if self.ai_running:
            return

        self.ai_running = True
        button.config(text=busy_text)
        self.status_var.set("AI 自优化任务运行中，请查看本页输出。")
        self._clear_console(self.ai_output_console)

        project_root = Path(__file__).resolve().parent
        artifacts_before = artifacts_store.snapshot(project_root)
        run = create_run(kind=self._infer_run_kind(command), scene=self.current_scene.get().strip() or None, command=command)

        threading.Thread(
            target=self._run_command_in_thread,
            args=(command, self.ai_log_queue, run.id, artifacts_before, env_overrides),
            daemon=True,
        ).start()
        self.after(100, lambda: self._process_ai_log_queue(button, idle_text))

    def _process_ai_log_queue(self, button, idle_text):
        try:
            while True:
                message = self.ai_log_queue.get_nowait()
                if message is None:
                    self.ai_running = False
                    button.config(text=idle_text)
                    self.status_var.set("AI 自优化任务已完成。")
                    self.populate_protocol_tree()
                    self.update_generation_selector()
                    self.refresh_artifact_list()
                    return
                self._append_console(self.ai_output_console, message)
        except queue.Empty:
            self.after(100, lambda: self._process_ai_log_queue(button, idle_text))

    def start_ai_protocol_optimization(self):
        scene_name = self.current_scene.get().strip()
        if not scene_name:
            messagebox.showwarning("操作无效", "请先在左侧选择一个场景。")
            return

        parent_a = self.ai_parent_a_entry.get().strip()
        parent_b = self.ai_parent_b_entry.get().strip()
        protocol_name = self.ai_protocol_name_entry.get().strip()
        if not all([parent_a, parent_b, protocol_name]):
            messagebox.showwarning("信息不全", "请填写父代A、父代B和输出协议名。")
            return

        validate_num_tags = self.ai_validate_num_tags_entry.get().strip() or "120"
        repair_rounds = self.ai_repair_rounds_entry.get().strip() or "1"
        if not validate_num_tags.isdigit() or not repair_rounds.isdigit():
            messagebox.showwarning("参数无效", "验证标签数和修复轮数必须是非负整数。")
            return

        command = [
            sys.executable,
            "-u",
            "ai_protocol_optimizer.py",
            "--scene",
            scene_name,
            "--parent-a",
            parent_a,
            "--parent-b",
            parent_b,
            "--protocol-name",
            protocol_name,
            "--target-generation",
            self.ai_target_generation_entry.get().strip() or "next",
            "--api-key-env",
            self.ai_key_env_entry.get().strip() or "GEEK_API_KEY",
            "--validate-num-tags",
            validate_num_tags,
            "--max-repair-rounds",
            repair_rounds,
        ]

        if self.ai_base_url_entry.get().strip():
            command.extend(["--base-url", self.ai_base_url_entry.get().strip()])
        if self.ai_model_entry.get().strip():
            command.extend(["--model", self.ai_model_entry.get().strip()])
        if self.ai_endpoint_entry.get().strip():
            command.extend(["--endpoint-path", self.ai_endpoint_entry.get().strip()])

        self._start_ai_action(
            button=self.ai_run_button,
            busy_text="AI 自优化运行中...",
            idle_text="运行 AI 自优化",
            command=command,
            env_overrides=self._collect_ai_env_overrides(),
        )

    def list_ai_models(self):
        base_url = self.ai_base_url_entry.get().strip()
        if not base_url:
            messagebox.showwarning("参数无效", "请先填写 Base URL。")
            return

        command = [
            sys.executable,
            "-u",
            "ai_protocol_optimizer.py",
            "--scene",
            self.current_scene.get().strip() or "multi_mode",
            "--parent-a",
            self.ai_parent_a_entry.get().strip() or "g2_1",
            "--parent-b",
            self.ai_parent_b_entry.get().strip() or "g2_2",
            "--protocol-name",
            self.ai_protocol_name_entry.get().strip() or "ai_probe_model",
            "--api-key-env",
            self.ai_key_env_entry.get().strip() or "GEEK_API_KEY",
            "--base-url",
            base_url,
            "--list-models",
        ]
        if self.ai_model_entry.get().strip():
            command.extend(["--model", self.ai_model_entry.get().strip()])
        if self.ai_endpoint_entry.get().strip():
            command.extend(["--endpoint-path", self.ai_endpoint_entry.get().strip()])

        self._start_ai_action(
            button=self.ai_models_button,
            busy_text="模型列表获取中...",
            idle_text="列出可用模型",
            command=command,
            env_overrides=self._collect_ai_env_overrides(),
        )

    def refresh_baseline_list(self):
        self.baseline_listbox.delete(0, tk.END)
        base_dir = Path("base_protocol")
        if not base_dir.exists():
            return
        for file_path in sorted(base_dir.glob("*.py")):
            self.baseline_listbox.insert(tk.END, file_path.name)

    def on_baseline_select(self, event=None):
        selected_indices = self.baseline_listbox.curselection()
        if not selected_indices:
            return
        file_name = self.baseline_listbox.get(selected_indices[0])
        file_path = Path("base_protocol") / file_name
        self.selected_baseline_path = str(file_path)
        self.baseline_path_label.config(text=f"当前编辑: {file_path}")
        try:
            self.baseline_code_text.delete("1.0", tk.END)
            self.baseline_code_text.insert("1.0", file_path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取基线协议: {e}")

    def save_baseline_protocol(self):
        if not self.selected_baseline_path:
            messagebox.showwarning("操作无效", "请先选择一个基线协议。")
            return
        try:
            Path(self.selected_baseline_path).write_text(self.baseline_code_text.get("1.0", tk.END), encoding="utf-8")
            messagebox.showinfo("成功", f"已保存基线协议:\n{self.selected_baseline_path}")
            self.status_var.set(f"已保存基线协议: {self.selected_baseline_path}")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存基线协议失败: {e}")

    def copy_baseline_to_scene(self):
        scene_name = self.current_scene.get().strip()
        if not scene_name:
            messagebox.showwarning("操作无效", "请先在左侧选择一个场景。")
            return
        if not self.selected_baseline_path:
            messagebox.showwarning("操作无效", "请先选择一个基线协议。")
            return

        target_dir = Path(scene_name) / "generation_0"
        target_dir.mkdir(parents=True, exist_ok=True)
        src = Path(self.selected_baseline_path)
        dst = target_dir / src.name
        try:
            shutil.copyfile(src, dst)
            messagebox.showinfo("成功", f"已复制到:\n{dst}")
            self.populate_protocol_tree()
            self.status_var.set(f"已复制基线协议到 {scene_name}/generation_0")
        except Exception as e:
            messagebox.showerror("复制失败", f"复制失败: {e}")

    def test_baseline_in_scene(self):
        scene_name = self.current_scene.get().strip()
        if not scene_name:
            messagebox.showwarning("操作无效", "请先在左侧选择一个场景。")
            return
        if not self.selected_baseline_path:
            messagebox.showwarning("操作无效", "请先选择一个基线协议。")
            return
        if self.test_running:
            messagebox.showwarning("请稍候", "已有测试正在运行。")
            return

        self.test_running = True
        self.test_button.config(text="测试运行中...")
        self.status_var.set("正在测试基线协议，请查看协议工作区的测试输出。")
        self.output_console.config(state="normal")
        self.output_console.delete("1.0", tk.END)
        self.output_console.config(state="disabled")
        test_thread = threading.Thread(
            target=self._run_test_in_thread,
            args=(self.selected_baseline_path, scene_name, self.log_queue),
            daemon=True,
        )
        test_thread.start()
        self.after(100, self._process_log_queue)

    def refresh_artifact_list(self):
        self.artifact_listbox.delete(0, tk.END)
        project_root = Path(__file__).resolve().parent
        patterns = ["*.csv", "*.pdf", "*.png", "*.jpg", "*.jpeg", "*.txt", "*.json"]
        matched = []
        for pattern in patterns:
            matched.extend(project_root.glob(pattern))
            matched.extend((project_root / "Experiments").glob(pattern))

        unique_sorted = sorted({p.resolve() for p in matched if p.exists()}, key=lambda p: p.name.lower())
        for p in unique_sorted:
            rel = p.relative_to(project_root)
            self.artifact_listbox.insert(tk.END, str(rel))
        self.status_var.set(f"结果中心已刷新，共 {len(unique_sorted)} 个文件。")

    def open_selected_artifact(self, event=None):
        selected_indices = self.artifact_listbox.curselection()
        if not selected_indices:
            return
        rel_path = self.artifact_listbox.get(selected_indices[0])
        file_path = Path(__file__).resolve().parent / rel_path
        try:
            if os.name == "nt":
                os.startfile(str(file_path))
            else:
                messagebox.showinfo("提示", f"当前系统不支持 startfile，请手动打开:\n{file_path}")
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开文件:\n{file_path}\n\n{e}")

    def open_output_folder(self, rel_dir: str):
        folder = (Path(__file__).resolve().parent / rel_dir).resolve()
        try:
            if os.name == "nt":
                os.startfile(str(folder))
            else:
                messagebox.showinfo("提示", f"当前系统不支持 startfile，请手动打开:\n{folder}")
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开目录:\n{folder}\n\n{e}")

    # --- 事件处理和核心逻辑函数 (大部分不变) ---
    def on_scene_select(self, event=None):
        selected_indices = self.scene_listbox.curselection()
        if not selected_indices: return
        scene_name = self.scene_listbox.get(selected_indices[0])
        self.current_scene.set(scene_name)
        self.populate_protocol_tree()
        self.update_generation_selector()
        self.load_config_file()
        self.load_task_file()
        self.code_text.delete("1.0", tk.END)
        self.filename_entry.delete(0, tk.END)
        self.output_console.config(state="normal")
        self.output_console.delete("1.0", tk.END)
        self.output_console.config(state="disabled")
        self.selected_protocol_path = None
        self.batch_scene_selector.set(scene_name)
        self.status_var.set(f"当前场景: {scene_name}。可在协议工作区编辑/测试，或在批量测试与实验页签运行自动化流程。")

    def create_scene(self):
        scene_name = self.scene_name_entry.get().strip()
        if not scene_name or any(c in r'/\:*?"<>|' for c in scene_name):
            messagebox.showwarning("警告", "场景名称不能为空或包含非法字符。")
            return
        if os.path.exists(scene_name):
            messagebox.showerror("错误", f"目录 '{scene_name}' 已存在！")
            return
        try:
            gen_0_dir = os.path.join(scene_name, "generation_0")
            prompts_dir = os.path.join(scene_name, "prompts")
            os.makedirs(gen_0_dir); os.makedirs(prompts_dir)
            task_file_path = os.path.join(scene_name, "Task.py")
            with open(task_file_path, 'w', encoding='utf-8') as f: f.write(TASK_PY_TEMPLATE)
            config_file_path = os.path.join(scene_name, "config.py")
            with open(config_file_path, 'w', encoding='utf-8') as f: f.write(CONFIG_PY_TEMPLATE)
            base_dir = "base_protocol"
            shutil.copytree(base_dir, gen_0_dir, dirs_exist_ok=True)
            messagebox.showinfo("成功", f"场景 '{scene_name}' 创建成功，并已生成 config.py 和 Task.py。")
            self.scene_name_entry.delete(0, tk.END)
            self.refresh_scene_list()
            self.status_var.set(f"场景 '{scene_name}' 创建成功。下一步请在左侧列表选中该场景。")
        except Exception as e:
            messagebox.showerror("创建失败", f"创建场景时发生错误: {e}")

    def load_config_file(self):
        scene_name = self.current_scene.get()
        for entry in self.config_entries.values(): entry.delete(0, tk.END)
        if not scene_name:
            self.config_file_label.config(text="当前未选择场景")
            self.save_config_button.config(state="disabled")
            return
        config_path = Path(scene_name) / "config.py"
        self.config_file_label.config(text=f"正在编辑: {config_path}")
        if not config_path.exists():
             self.save_config_button.config(state="normal") # 允许创建
             return
        try:
            # 确保使用绝对路径加载，以防相对路径问题
            absolute_config_path = config_path.resolve()
            config_module = import_module_from_path("config", absolute_config_path)
            simulation_config = getattr(config_module, 'SIMULATION_CONFIG', {})
            for param, entry in self.config_entries.items():
                if param in simulation_config:
                    entry.insert(0, str(simulation_config[param]))
            self.save_config_button.config(state="normal")
            # 清理导入的模块，避免缓存问题
            if "config" in sys.modules: del sys.modules["config"]
        except Exception as e:
            messagebox.showerror("加载失败", f"加载 config.py 时出错: {e}")
            self.save_config_button.config(state="disabled")

    def save_config_file(self):
        scene_name = self.current_scene.get()
        if not scene_name: return
        config_path = Path(scene_name) / "config.py"
        try:
            content = "# --- *** 模拟参数配置 *** ---\n"
            content += "SIMULATION_CONFIG = {\n"
            for param, entry in self.config_entries.items():
                value = entry.get().strip()
                if not value.isdigit():
                    messagebox.showerror("输入无效", f"参数 '{param}' 的值必须是整数。")
                    return
                # 尝试获取描述，如果失败则忽略
                try:
                    description = entry.master.winfo_children()[0]['text'].split('(')[1][:-2]
                except IndexError:
                    description = "" # 发生错误时留空描述
                content += f"    '{param}': {int(value)},    # {description}\n"
            content += "}\n"
            with open(config_path, 'w', encoding='utf-8') as f: f.write(content)
            messagebox.showinfo("成功", f"配置已成功保存至\n{config_path}")
            self.status_var.set(f"已保存配置: {config_path}")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存 config.py 时出错: {e}")

    def load_task_file(self):
        scene_name = self.current_scene.get()
        self.task_code_text.delete("1.0", tk.END)
        if not scene_name:
            self.task_file_label.config(text="当前未选择场景")
            self.save_task_button.config(state="disabled")
            return
        task_path = os.path.join(scene_name, "Task.py")
        self.task_file_label.config(text=f"正在编辑: {task_path}")
        try:
            if os.path.exists(task_path):
                with open(task_path, 'r', encoding='utf-8') as f:
                    self.task_code_text.insert("1.0", f.read())
            else:
                self.task_code_text.insert("1.0", "# Task.py 不存在。")
            self.save_task_button.config(state="normal")
        except Exception as e:
            messagebox.showerror("加载失败", f"加载 Task.py 时出错: {e}")

    def save_task_file(self):
        scene_name = self.current_scene.get()
        if not scene_name: return
        task_path = os.path.join(scene_name, "Task.py")
        code = self.task_code_text.get("1.0", tk.END)
        try:
            with open(task_path, 'w', encoding='utf-8') as f: f.write(code)
            messagebox.showinfo("成功", f"Task.py 已成功保存至\n{task_path}")
            self.status_var.set(f"已保存任务脚本: {task_path}")
        except Exception as e:
            messagebox.showerror("保存失败", f"保存 Task.py 时出错: {e}")

    def start_test_protocol(self):
        if self.test_running: return
        if not self.selected_protocol_path:
            messagebox.showwarning("操作无效", "请先从树中选择一个要测试的协议。")
            return
        self.test_running = True
        self.test_button.config(text="测试运行中...")
        self.status_var.set("正在测试选中协议，请查看测试输出日志。")
        self.output_console.config(state="normal")
        self.output_console.delete("1.0", tk.END)
        self.output_console.config(state="disabled")
        test_thread = threading.Thread(
            target=self._run_test_in_thread,
            args=(self.selected_protocol_path, self.current_scene.get(), self.log_queue),
            daemon=True
        )
        test_thread.start()
        self.after(100, self._process_log_queue)

    def _process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message is None:
                    self.test_running = False
                    self.test_button.config(text="开始测试选中协议")
                    self.status_var.set("选中协议测试已完成。")
                    return
                self.output_console.config(state="normal")
                self.output_console.insert(tk.END, message + "\n")
                self.output_console.see(tk.END)
                self.output_console.config(state="disabled")
        except queue.Empty:
            self.after(100, self._process_log_queue)

    def _run_test_in_thread(self, selected_path, scene_name, log_queue):
        project_root = Path(__file__).resolve().parent
        scene_path = project_root / scene_name
        protocol_path = Path(selected_path)
        original_sys_path = list(sys.path)
        def log(message): log_queue.put(message)
        modules_loaded = {} # 跟踪动态加载的模块
        try:
            sys.path.insert(0, str(project_root))
            sys.path.insert(0, str(scene_path))
            log(f"--- 开始测试协议: {protocol_path.name} ---")
            log(f"--- 场景: {scene_name} ---")
            log(f"\n[1/6] 正在加载配置文件: {scene_path / 'config.py'}")
            config_module = import_module_from_path("config", scene_path / "config.py")
            modules_loaded["config"] = config_module # 跟踪
            simulation_config = getattr(config_module, 'SIMULATION_CONFIG', {})
            log("✅ 配置文件加载成功。")
            log(f"\n[2/6] 正在加载任务模块: {scene_path / 'Task.py'}")
            task_module = import_module_from_path("Task", scene_path / "Task.py")
            modules_loaded["Task"] = task_module # 跟踪
            generate_tags_func = getattr(task_module, 'generate_tags')
            log("✅ 任务模块加载成功。")
            config = {
                'num_tags': simulation_config.get('NUM_TAGS', 100000),
                'total_runs': simulation_config.get('TOTAL_RUNS', 100),
                'price_min': simulation_config.get('PRICE_MIN', 1),
                'price_max': simulation_config.get('PRICE_MAX', 1000),
            }
            log(f"  - 测试配置: {config['total_runs']}轮, {config['num_tags']}标签, 价格范围[{config['price_min']}-{config['price_max']}]")
            log(f"\n[3/6] 正在加载协议: {protocol_path.name}")
            proto_module_name = protocol_path.stem
            protocol_module = import_module_from_path(proto_module_name, protocol_path)
            modules_loaded[proto_module_name] = protocol_module # 跟踪
            protocol_class = getattr(protocol_module, proto_module_name)
            protocol_instance = protocol_class()
            log("✅ 协议加载成功。")
            log(f"\n[4/6] 正在执行约束验证...")
            validator = ProtocolValidator(protocol_name=protocol_path.name, protocol_instance=protocol_instance)
            validation_tags, _ = generate_tags_func(100, config['price_min'], config['price_max'])
            is_valid, report = validator.validate(validation_tags, config['price_min'], config['price_max'])
            log(report)
            if is_valid:
                log(f"\n[5/6] 协议通过验证，开始性能测试 (共{config['total_runs']}轮)...")
                passed_runs = 0; total_time = 0.0
                for i in range(config['total_runs']):
                    tag_set, true_info = generate_tags_func(config['num_tags'], config['price_min'], config['price_max'])
                    try:
                        modes, _, ex_time, _ = protocol_instance.execute(tag_set, config['price_min'], config['price_max'])
                        total_time += ex_time
                        is_correct = set(modes).issubset(set(true_info.get('modes', [])))
                        if is_correct: passed_runs += 1
                    except Exception as e:
                        log(f"  在第 {i+1} 轮测试中发生运行时错误: {e}"); break
                    if config['total_runs'] > 0 and (i + 1) % (config['total_runs'] // 10 or 1) == 0:
                        log(f"  ...进度: {i+1}/{config['total_runs']}")
                log(f"\n[6/6] 生成最终报告...")
                pass_rate = (passed_runs / config['total_runs']) * 100 if config['total_runs'] > 0 else 0
                avg_time = total_time / config['total_runs'] if config['total_runs'] > 0 else 0
                log("\n" + "="*20 + " 最终性能测试报告 " + "="*20)
                log(f"{'协议名称':<20} | {protocol_path.name}")
                log(f"{'测试通过率':<20} | {pass_rate:.2f}%")
                log(f"{'平均执行时间 (ms)':<20} | {avg_time:.2f}")
                log("="*62)
            else:
                log("\n[5/6] 协议未通过验证，性能测试已跳过。")
        except Exception as e:
            log(f"\n\nFATAL ERROR: 测试期间发生意外错误: {e}")
            log(f"TRACEBACK:\n{traceback.format_exc()}")
        finally:
            # 清理 sys.modules 中动态加载的模块
            for mod_name in modules_loaded:
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
            sys.path[:] = original_sys_path # 恢复原始路径
            log("\n--- 测试结束 ---")
            log_queue.put(None) # 发送结束信号

    def populate_protocol_tree(self, event=None):
        for i in self.protocol_tree.get_children(): self.protocol_tree.delete(i)
        scene_name = self.current_scene.get()
        if not scene_name or not os.path.isdir(scene_name): return
        gen_dirs = sorted([d for d in os.listdir(scene_name) if d.startswith("generation_")])
        for gen_dir in gen_dirs:
            gen_path = os.path.join(scene_name, gen_dir)
            if os.path.isdir(gen_path):
                gen_node = self.protocol_tree.insert("", "end", text=gen_dir, open=True)
                protocols = sorted([f for f in os.listdir(gen_path) if f.endswith(".py")])
                for protocol in protocols:
                    self.protocol_tree.insert(gen_node, "end", text=protocol, values=(os.path.join(gen_path, protocol),))

    def on_protocol_select(self, event=None):
        selected_id = self.protocol_tree.focus()
        if not selected_id: return
        item = self.protocol_tree.item(selected_id)
        if item['values']:
            self.selected_protocol_path = item['values'][0]
            protocol_name = os.path.basename(self.selected_protocol_path)
            self.filename_entry.delete(0, tk.END); self.filename_entry.insert(0, protocol_name)
            try:
                with open(self.selected_protocol_path, 'r', encoding='utf-8') as f:
                    self.code_text.delete("1.0", tk.END); self.code_text.insert("1.0", f.read())
            except Exception as e: messagebox.showerror("错误", f"无法读取文件: {e}")
        else: self.selected_protocol_path = None

    def delete_selected_protocol(self):
        if not self.selected_protocol_path:
             messagebox.showwarning("操作无效", "请先从树中选择一个协议文件。")
             return
        if messagebox.askyesno("确认删除", f"您确定要永久删除文件\n'{self.selected_protocol_path}' 吗？"):
            try:
                os.remove(self.selected_protocol_path)
                messagebox.showinfo("成功", "文件已删除。")
                self.populate_protocol_tree()
                self.code_text.delete("1.0", tk.END); self.filename_entry.delete(0, tk.END)
                self.selected_protocol_path = None
            except Exception as e: messagebox.showerror("删除失败", f"删除文件时出错: {e}")

    def update_generation_selector(self):
        scene_name = self.current_scene.get()
        self.generation_selector['values'] = []
        if not scene_name: return
        gen_dirs = sorted([d for d in os.listdir(scene_name) if d.startswith("generation_")])
        self.generation_selector['values'] = gen_dirs + ["[ 创建新世代 ]"]
        if gen_dirs: self.generation_selector.set(gen_dirs[-1])
        else: self.generation_selector.set("[ 创建新世代 ]")

    def save_protocol(self):
        scene_name = self.current_scene.get()
        filename = self.filename_entry.get().strip()
        code = self.code_text.get("1.0", tk.END).strip()
        target_gen = self.generation_selector.get()
        if not all([scene_name, filename, code, target_gen]):
            messagebox.showwarning("信息不全", "文件名、代码和目标世代均不能为空。"); return
        if not filename.endswith(".py"): filename += ".py"
        try:
            if target_gen == "[ 创建新世代 ]":
                gen_dirs = [d for d in os.listdir(scene_name) if d.startswith("generation_")]
                gen_indices = [int(d.split("_")[1]) for d in gen_dirs if d.split('_')[-1].isdigit()]
                next_index = max(gen_indices) + 1 if gen_indices else 0
                target_dir = os.path.join(scene_name, f"generation_{next_index}")
            else: target_dir = os.path.join(scene_name, target_gen)
            os.makedirs(target_dir, exist_ok=True)
            file_path = os.path.join(target_dir, filename)
            if os.path.exists(file_path) and file_path != self.selected_protocol_path:
                 if not messagebox.askyesno("确认覆盖", f"文件'{filename}'已在目标世代存在，是否覆盖？"): return
            with open(file_path, 'w', encoding='utf-8') as f: f.write(code)
            messagebox.showinfo("成功", f"协议已保存至\n{file_path}")
            self.populate_protocol_tree()
            self.update_generation_selector()
            self.status_var.set(f"协议已保存: {file_path}")
        except Exception as e: messagebox.showerror("保存失败", f"保存文件时出错: {e}")

    def generate_crossover_prompts(self):
        scene_name = self.current_scene.get()
        if not scene_name: messagebox.showwarning("操作无效", "请先在左侧选择一个场景。"); return
        parent_names = [entry.get().strip() for entry in self.parent_entries]
        parent_names = [name for name in parent_names if name]
        if len(parent_names) < 2: messagebox.showwarning("输入不足", "请输入至少2个父代协议的名称。"); return
        try:
            num_generated, errors = create_crossover_prompts(parent_names, scene_name)
            summary = f"操作完成！\n\n成功生成 {num_generated} 个提示词到\n'{scene_name}/prompts'。"
            if errors: summary += "\n\n发生错误:\n- " + "\n- ".join(errors)
            messagebox.showinfo("生成报告", summary)
            self.status_var.set(f"提示词生成完成: {num_generated} 个文件。")
        except Exception as e: messagebox.showerror("严重错误", f"生成提示词时发生意外错误: {e}")

    def refresh_scene_list(self):
        self.scene_listbox.delete(0, tk.END)
        dirs = [d for d in os.listdir(".") if os.path.isdir(d) and d not in ["base_protocol", "__pycache__"]]
        scene_names = []
        for d in dirs:
            if os.path.exists(os.path.join(d, "generation_0")):
                self.scene_listbox.insert(tk.END, d)
                scene_names.append(d)

        if hasattr(self, "batch_scene_selector"):
            self.batch_scene_selector['values'] = scene_names
            if scene_names and not self.batch_scene_selector.get():
                self.batch_scene_selector.set(scene_names[0])

if __name__ == "__main__":
    setup_base_environment()
    app = ProtocolEvoGUI()
    print("Starting mainloop...") # Debug print
    app.mainloop()
    print("Mainloop finished.") # Debug print

