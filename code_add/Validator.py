# Validator.py

from typing import Tuple, Dict, List
from base import IProtocol, Tag
from backend_tools.protocol_metrics import normalize_protocol_metrics

class ProtocolValidator:
    """
    一个用于验证RFID协议是否遵循特定约束的工具。
    此版本只包含自动化可验证的约束。
    """
    def __init__(self, protocol_name: str, protocol_instance: IProtocol):
        self.protocol_name = protocol_name
        self.protocol = protocol_instance
        # --- 修改点 1: 移除了约束3和4的定义 ---
        self.constraints = {
            1: {"description": "时隙宽度限制 (<= 256 bits)", "status": "未检查", "is_ok": False},
            2: {"description": "帧长限制 (<= 32768 slots)", "status": "未检查", "is_ok": False},
        }

    def validate(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[bool, str]:
        """
        执行协议并验证所有约束。
        
        Returns:
            Tuple[bool, str]: (验证是否通过, 详细的报告字符串)
        """
        
        self.validation_notes = []

        try:
            # 我们只需要第四个返回参数
            *_, verification_data = self.protocol.execute(tags, price_min, price_max)
            verification_data = normalize_protocol_metrics(verification_data)
            self.validation_notes = verification_data.get("validation_notes", [])
        except Exception as e:
            report = f"协议 {self.protocol_name} 执行出错: {e}"
            return False, report

        # 自动化验证
        # 约束1: 时隙宽度
        bits_per_slot = verification_data.get("bits_per_slot", 0)
        if bits_per_slot <= 0:
            self.constraints[1]["status"] = "❌ 失败 (缺少有效 bits_per_slot 指标)"
            self.constraints[1]["is_ok"] = False
        elif bits_per_slot <= 256:
            self.constraints[1]["status"] = f"✅ 通过 ({bits_per_slot} bits)"
            self.constraints[1]["is_ok"] = True
        else:
            self.constraints[1]["status"] = f"❌ 失败 ({bits_per_slot} bits > 256)"
            self.constraints[1]["is_ok"] = False

        # 约束2: 帧长
        frame_size = verification_data.get("frame_size", 0)
        if frame_size <= 0:
            self.constraints[2]["status"] = "❌ 失败 (缺少有效 frame_size 指标)"
            self.constraints[2]["is_ok"] = False
        elif frame_size <= 2**15:
            self.constraints[2]["status"] = f"✅ 通过 ({frame_size} slots)"
            self.constraints[2]["is_ok"] = True
        else:
            self.constraints[2]["status"] = f"❌ 失败 ({frame_size} slots > 32768)"
            self.constraints[2]["is_ok"] = False

        # 生成报告
        report = self._generate_report()
        
        # 返回最终结果
        all_passed = all(c["is_ok"] for c in self.constraints.values())
        return all_passed, report

    def _generate_report(self) -> str:
        """生成最终的验证报告字符串。"""
        report_lines = []
        report_lines.append("="*20 + f" 验证报告: {self.protocol_name} " + "="*20)
        for num, check in self.constraints.items():
            report_lines.append(f"约束 {num}: {check['description']:<35} | 状态: {check['status']}")
        if self.validation_notes:
            report_lines.append("补充说明:")
            for note in self.validation_notes:
                report_lines.append(f"- {note}")
        report_lines.append("="*64)
        # --- 修改点 2: 移除了手动审查指南的打印 ---
        return "\n".join(report_lines)