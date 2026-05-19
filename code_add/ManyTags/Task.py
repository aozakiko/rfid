#!/usr/bin/env python


import random
from collections import Counter
from typing import List, Tuple, Dict
from base import Tag


SIMULATION_CONFIG = {
'NUM_TAGS': 10000,    # 模拟的标签数量
'PRICE_MIN': 1,        # 价格的最小值
'PRICE_MAX': 1000,     # 价格的最大值
'TOTAL_RUNS': 100      # 每个协议的重复测试次数
}

def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, any]]:

    tags = []

    # 设计核心：生成多个势均力敌的众数
    num_modes = random.randint(3, 5)

    # 确保价格范围足够大以供抽样
    if price_max - price_min + 1 < num_modes:
        num_modes = price_max - price_min + 1
        
    modes = sorted(random.sample(range(price_min, price_max + 1), num_modes))

    # 设定40%的标签将被赋予这些众数价格，剩下60%是噪声
    mode_probability = 0.4

    for i in range(num_tags):
        if random.random() < mode_probability:
            # 从多个众数中随机选择一个价格
            price = random.choice(modes)
        else:
            # 随机选择一个非众数的价格
            price = random.randint(price_min, price_max)
            while price in modes:
                price = random.randint(price_min, price_max)
                
        tags.append(Tag(i, price))

    true_info = {"modes": modes}
    return tags, true_info
