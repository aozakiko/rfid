import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g2_3(IProtocol):
    """
    子代协议：多哈希布隆过滤与精确验证协议 (HBFV)

    本协议通过交叉融合父代协议的核心思想，并引入全新的突变机制，
    旨在高效且准确地解决具有挑战性的多众数查询问题。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1  # 协议所有阶段的时隙回复均为1bit

        # --- 协议超参数 ---
        # 阶段一：过滤帧的帧长
        filter_frame_size = 512
        # 阶段一：过滤所使用的哈希函数数量（突变机制的核心）
        num_hash_functions = 2
        # 阶段二：验证帧的帧长
        verification_frame_size = 64

        # ==================================================================
        #  阶段一: 基于多哈希的布隆过滤 - 快速筛除无关价格
        # ==================================================================

        # --- 交叉部分：融合父代A的两阶段架构思想与父代B的价格映射探测思想 ---
        # 本协议采纳了父代A的“先过滤，后验证”的两阶段高效架构。第一阶段的设计灵感
        # 源自父代B将价格属性映射到时隙的思想，但通过哈希进行了泛化，以快速构建出
        # 整个价格空间的“活动热力图”，避免了对每个价格进行暴力探测。

        # --- 突变部分：引入基于多哈希函数（类布隆过滤器）的候选价格筛选机制 ---
        # 与父代A仅使用单个哈希函数进行过滤不同，本协议引入了全新的多哈希过滤机制。
        # 读写器使用多个独立的哈希函数对价格空间进行探测。一个价格只有在所有哈希
        # 函数对应的时隙中都显示为“非空”，才会被认定为候选者。这种类布隆过滤器的
        # 设计，能够极大地降低“哈希碰撞”导致的伪候选者数量，从而显著减少第二阶段
        # 需要进行的昂贵的验证通信次数，提升了整体效率。

        # --- 读写器行为: 广播多哈希过滤指令 ---
        total_frames += 1 # 概念上是一次通信，广播所有过滤参数
        filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
        # 广播帧长和多个种子
        bits_sent = filter_frame_size.bit_length()
        for seed in filter_seeds:
            bits_sent += seed.bit_length()
        total_bits_sent_by_reader += bits_sent

        # --- [物理层模拟] ---
        # 标签根据自身价格和收到的多个种子，在概念上的多个帧中进行响应
        # 读写器最终接收到 num_hash_functions 个独立的聚合信号（帧）
        filter_frames_states = [[0] * filter_frame_size for _ in range(num_hash_functions)]
        for t in tags:
            for i in range(num_hash_functions):
                slot_id = simple_hash(t.price, filter_seeds[i]) % filter_frame_size
                filter_frames_states[i][slot_id] += 1
        total_slots += filter_frame_size * num_hash_functions
        # --- [物理层模拟结束] ---

        # ==================================================================
        #  阶段二: 细粒度验证 - 对候选价格进行精确评分
        # ==================================================================

        candidate_scores = {}

        # 读写器迭代所有可能的价格，通过布隆过滤器检查其是否为候选者
        for p in range(price_min, price_max + 1):
            is_candidate = True
            # --- 突变部分：应用多哈希过滤逻辑 ---
            # 读写器在本地计算价格p在所有哈希函数下的映射位置
            # 并检查所有这些位置在第一阶段是否都非空
            for i in range(num_hash_functions):
                slot_p = simple_hash(p, filter_seeds[i]) % filter_frame_size
                if filter_frames_states[i][slot_p] == 0:
                    is_candidate = False
                    break # 只要有一个哈希未命中，就不是候选者

            if is_candidate:
                # --- 交叉部分：融合父代A的精确验证思想与父代B的独立探测模式 ---
                # 对于通过了过滤阶段的少数候选价格，本协议借鉴父代A的做法，为每个
                # 候选者p启动一次独立的、类似父代B的帧通信过程，以精确评估其数量。
                # 这种方法确保了最终结果的准确性，同时由于候选者数量被大幅削减，
                # 效率远高于纯粹的父代B。

                # --- 读写器行为: 广播对候选价格 p 的探测指令 ---
                total_frames += 1
                seed_verify = generate_random_seed()
                total_bits_sent_by_reader += p.bit_length() + verification_frame_size.bit_length() + seed_verify.bit_length()

                # --- [物理层模拟] ---
                verification_slot_states = [0] * verification_frame_size
                for t in tags:
                    if t.price == p:
                        slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                        verification_slot_states[slot_id] += 1
                total_slots += verification_frame_size
                # --- [物理层模拟结束] ---

                # --- 读写器分析聚合信号 ---
                # 使用父代协议中已证明鲁棒的“非空时隙数”作为评分标准
                num_non_empty = sum(1 for count in verification_slot_states if count > 0)
                if num_non_empty > 0:
                    candidate_scores[p] = num_non_empty

        # ==================================================================
        #  最终处理 (Post-processing)
        # ==================================================================

        # --- 时间成本计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "filter_frame_size": filter_frame_size,
            "num_hash_functions": num_hash_functions,
            "verification_frame_size": verification_frame_size,
            "num_candidates": len(candidate_scores),
            "final_scores": candidate_scores
        }

        if not candidate_scores:
            return [], 0, total_time_ms, verification_data

        # --- 找出得分最高的众数 ---
        max_score = max(candidate_scores.values())

        # 返回所有得分最高的众数，并确保结果排序
        modes = sorted([
            price for price, score in candidate_scores.items()
            if score == max_score
        ])

        return modes, max_score, total_time_ms, verification_data
