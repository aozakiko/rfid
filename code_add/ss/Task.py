#!/usr/bin/env python
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

# --- *** 模拟参数配置 *** ---
SIMULATION_CONFIG = {
    'NUM_TAGS': 100000,    # 模拟的标签数量
    'PRICE_MIN': 1,        # 价格的最小值
    'PRICE_MAX': 1000,     # 价格的最大值
    'TOTAL_RUNS': 100      # 每个协议的重复测试次数
}

# --- *** 标签生成逻辑 (多峰均势场景) *** ---
def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, any]]:
    """生成一个具有多个频率相近的众数的标签列表。"""
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
