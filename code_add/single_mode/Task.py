# Task.py

import random
from collections import Counter
from typing import List, Tuple, Dict
from base import Tag

def generate_tags(num_tags: int, price_min: int, price_max: int) -> Tuple[List[Tag], Dict[str, any]]:
    """
    生成一个标签列表用于模拟，并确保有且只有一个众数。
    
    Args:
        num_tags (int): 要生成的标签总数。
        price_min (int): 价格下限。
        price_max (int): 价格上限。
        
    Returns:
        Tuple containing:
        - List[Tag]: 生成的标签对象列表。
        - Dict: 包含真实众数信息的字典 {"mode": mode_price}。
    """
    tags = []
    
    # --- *** 修改点 1: 固定只生成一个众数 *** ---
    # 随机设定一个“热门”价格作为唯一的众数
    single_mode = random.randint(price_min, price_max)
    
    print(f"场景设置: 本次模拟的唯一预设众数为 {single_mode}")

    for i in range(num_tags):
        # 60%的概率选择这个唯一的热门价格，40%的概率选择一个不同的随机价格
        if random.random() < 0.4:
            price = single_mode
        else:
            # 确保随机价格不与众数价格冲突，以强化众数的唯一性
            price = random.randint(price_min, price_max)
            while price == single_mode:
                price = random.randint(price_min, price_max)
                
        tags.append(Tag(i, price))
        
    # --- *** 修改点 2: 简化返回的真实信息 *** ---
    # 现在我们只关心众数的值，不再需要返回数量或处理多个众数的情况。
    true_info = {"mode": single_mode}

    return tags, true_info