# base.py

import hashlib
import random

from typing import List, Tuple, Dict

class Tag:
    """
    定义一个RFID标签对象。

    Attributes:
        id (int): 标签的唯一标识符。
        price (int): 标签携带的价格属性值。
    """
    def __init__(self, tag_id: int, price: int):
        self.id = tag_id
        self.price = price

    def __repr__(self) -> str:
        return f"Tag(ID={self.id}, Price={self.price})"

class Frame:
    """
    定义一个通信帧对象。
    在本次模拟中，一个帧本质上是一个时隙列表，每个时隙存储一个整数，
    该整数的二进制形式代表了所有在该时隙回复的标签价格向量的按位或结果。
    """
    def __init__(self, num_slots: int, bits_per_slot: int):
        self.num_slots = num_slots
        self.bits_per_slot = bits_per_slot
        # 初始化所有时隙的数据为0
        self.slots = [0] * num_slots

    def aggregate(self, slot_index: int, data: int):
        """
        将一个标签的回复数据聚合到指定的时隙中。

        Args:
            slot_index (int): 目标时隙的索引。
            data (int): 标签回复的数据（价格向量）。
        """
        if 0 <= slot_index < self.num_slots:
            self.slots[slot_index] |= data

    def __repr__(self) -> str:
        return f"Frame(num_slots={self.num_slots})"

def generate_random_seed(min_val: int = 1, max_val: int = 100000, rng=None) -> int:
    """生成一个指定范围内的随机整数作为种子。"""
    source = rng if rng is not None else random
    return source.randint(min_val, max_val)

def simple_hash(val1, val2: int) -> int:
    """
    一个简单的哈希函数，用于模拟CPDL中的 H(a, b)。
    它将两个输入转换为字符串拼接后，计算其哈希值。

    Returns:
        int: 一个非负整数哈希值。
    """
    payload = f"{val1}\x1f{val2}".encode("utf-8", errors="replace")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


class IProtocol:
    """
    定义所有协议都必须遵循的通用接口。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        # *** 修改点：增加了一个返回类型 Dict，用于存放验证所需的数据 ***
        """
        执行协议的核心逻辑。

        Returns:
            Tuple containing:
            - List[int]: 估算出的众数价格列表。
            - int: 估算出的最高频次/指标。
            - float: 协议执行的总时间（毫秒）。
            - Dict: 用于验证的参数字典, e.g., {"frame_size": 512, "bits_per_slot": 1000}
        """
        raise NotImplementedError("此方法必须在子类中实现")



