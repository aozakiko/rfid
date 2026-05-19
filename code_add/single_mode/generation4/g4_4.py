
import math
import statistics
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
# The hash function is assumed to be in a file named "hash.py"
from hash import simple_hash

class g4_4(IProtocol):
    """
    自适应分层评分协议 (Adaptive Hierarchical Scoring Protocol)

    本协议是一个全新的子代协议，通过交叉融合父代协议的优点并引入创新突变，
    旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    """
    
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots_overall = 0
        bits_per_slot_map = {} # To track bits per slot for each frame

        # --- 协议超参数 ---
        PRECHECK_BINS = 16              # 预检分箱数 (Inspired by Parent B)
        PRECHECK_FRAME_SIZE = 64        # 预检帧长 (Inspired by Parent B)
        SHOWDOWN_FRAME_SIZE = 128       # 裁决阶段初始帧长 (Inspired by Parent A)
        REQUERY_FRAME_SIZE = 512        # 突变：重查询时使用的更大帧长 (Mutation)
        REQUERY_COLLISION_THRESHOLD = 0.9 # 突变：触发重查询的冲突率阈值 (Mutation)

        # --- 交叉部分：融合父代B的分层剪枝思想与父代A的精确评分思想 ---
        # 本协议采纳了父代B的“分层过滤”核心框架，先通过粗粒度的范围预检来快速
        # 排除大量无关价格（剪枝），从而显著缩小搜索空间。随后，在精确评分阶段，
        # 本协议抛弃了父代B复杂的数学估算模型，转而采用了父代A更直接、更鲁棒的
        # “非空时隙数”作为核心评分依据，确保了最终结果的准确性。

        # 阶段一：粗粒度范围剪枝 (Inherited from Parent B)
        # 快速扫描价格区间，确定哪些区域“可能”包含标签，生成活跃价格列表。
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            total_frames += 1
            seed = generate_random_seed()
            # Reader broadcasts Select(p_bin_range), frame_size, seed
            total_bits_sent_by_reader += (p_bin[0].bit_length() + p_bin[-1].bit_length() + 
                                          PRECHECK_FRAME_SIZE.bit_length() + seed.bit_length())

            # [物理层模拟]
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_states[simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE] += 1
            # [物理层模拟结束]
            
            total_slots_overall += PRECHECK_FRAME_SIZE
            bits_per_slot_map[total_frames] = 1 # 1 bit response for presence check

            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            total_tag_bits = sum(total_slots_overall * bits_per_slot_map.get(f, 1) for f in range(1, total_frames + 1))
            T_tag = total_slots_overall * 1 * 0.4 # Simplified for this stage
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found after pruning"}
        
        # 阶段二：精确评分与自适应重查询 (Inspired by Parent A + Mutation)
        # 对筛选出的活跃价格，逐一进行精确评分。
        final_scores = {}
        sorted_active_prices = sorted(list(active_prices))

        for price in sorted_active_prices:
            current_frame_size = SHOWDOWN_FRAME_SIZE
            
            # --- 初始查询 ---
            total_frames += 1
            seed = generate_random_seed()
            # Reader broadcasts Select(price), frame_size, seed
            total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed.bit_length()
            
            # [物理层模拟]
            slot_states = [0] * current_frame_size
            for t in tags:
                if t.price == price:
                    slot_states[simple_hash(t.id, seed) % current_frame_size] += 1
            # [物理层模拟结束]
            
            total_slots_overall += current_frame_size
            bits_per_slot_map[total_frames] = 1

            # Reader分析聚合信号
            num_success = sum(1 for count in slot_states if count == 1)
            num_collision = sum(1 for count in slot_states if count > 1)
            
            score = num_success + num_collision
            collision_ratio = num_collision / current_frame_size if current_frame_size > 0 else 0

            # --- 突变部分：引入“基于冲突率的自适应重查询”全新机制 ---
            # 父代A和B都使用固定的帧长进行查询或估算。当标签数量远超帧长时，
            # 父代A的“非空时隙数”评分会饱和（所有时隙都非空），导致无法区分
            # 同样造成饱和但数量差异巨大的候选者，从而影响准确率。
            # 本协议引入一个动态优化：如果首次查询的冲突率超过一个高阈值，
            # 协议会判定该评分不可靠，并立即使用一个更大的帧长对该价格发起一次
            # “重查询”，以获得一个更精确、分辨率更高的分数。
            # 这个机制使得协议能自动为“热门候选者”投入更多通信资源，确保了最高评测精度。
            if collision_ratio > REQUERY_COLLISION_THRESHOLD:
                # 帧长严重不足，分数可能饱和，启动重查询
                current_frame_size = REQUERY_FRAME_SIZE
                
                total_frames += 1
                seed = generate_random_seed()
                # Reader broadcasts Select(price), new_frame_size, seed
                total_bits_sent_by_reader += price.bit_length() + current_frame_size.bit_length() + seed.bit_length()
                
                # [物理层模拟]
                slot_states_requery = [0] * current_frame_size
                for t in tags:
                    if t.price == price:
                        slot_states_requery[simple_hash(t.id, seed) % current_frame_size] += 1
                # [物理层模拟结束]
                
                total_slots_overall += current_frame_size
                bits_per_slot_map[total_frames] = 1
                
                # 使用重查询的结果更新分数
                num_success_requery = sum(1 for count in slot_states_requery if count == 1)
                num_collision_requery = sum(1 for count in slot_states_requery if count > 1)
                score = num_success_requery + num_collision_requery
            
            if score > 0:
                final_scores[price] = score

        # --- 时间成本计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        # Tag response time depends on total slots and bits per slot for each frame
        total_tag_response_units = total_slots_overall # Since bits_per_slot is always 1
        T_tag = total_tag_response_units * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        if not final_scores:
            return [], 0, total_time_ms, {"reason": "Showdown phase yielded no scores", "active_prices_count": len(active_prices)}

        # --- 最终处理 (Post-processing) ---
        max_score = max(final_scores.values())
        modes = sorted([p for p, s in final_scores.items() if s == max_score])
        
        verification_data = {
            "active_prices_after_pruning": len(active_prices),
            "showdown_candidate_count": len(sorted_active_prices),
            "final_scores_top10": dict(sorted(final_scores.items(), key=lambda item: item[1], reverse=True)[:10]),
            "total_frames": total_frames
        }
        
        return modes, max_score, total_time_ms, verification_data