import math
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g3_3(IProtocol):
    """
    子代协议: 自适应并行验证协议 (Adaptive Parallel Verification Protocol)

    本协议设计用于高效、准确地解决在海量标签中寻找多个频率相近众数（热门价格）的挑战性问题。
    它通过一个多阶段的精炼过程，深度融合了父代协议的核心思想并引入了全新的自适应机制。

    - 阶段一 (全局估算): 协议首先对整个价格范围进行一次快速的全景扫描，得到所有价格
      数量的粗略估计。
    - 阶段二 (自适应分组): 协议会分析估算结果，自动识别出由最可能的众数候选者组成的
      “竞争者小组”，而无需预设固定的候选者数量。
    - 阶段三 (并行验证): 随后，协议将所有通信资源聚焦于这个“竞争者小组”，通过一次
      高精度的并行通信，同时对小组内所有候选者的数量进行精确验证。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        # 阶段一 (估算)
        estimation_batch_size = 128
        estimation_frame_size = 512

        # 阶段二 (自适应分组)
        candidate_group_dropoff_ratio = 0.75 # 候选组数量下降阈值
        max_contender_group_size = 32      # 竞争者小组的最大规模

        # =================================================================================
        # --- 交叉部分：融合父代A的并行SEM估算思想与父代B的两阶段（估算-验证）框架 ---
        # 本协议采纳了父代B的“先粗略估算，后精确验证”的宏观流程。
        # 在此基础上，将父代A的核心机制——即通过单帧并行处理多个目标（价格）的SEM
        # （Slot-Encoded Multiplexing）思想——不仅用于第一阶段的全局估算，还创新性地
        # 应用于第三阶段，对筛选出的核心候选者进行并行化的精确验证，而非父代B的串行验证。
        # =================================================================================

        # --- 阶段一: 全局估算 (Global Estimation Scan) ---
        all_prices = list(range(price_min, price_max + 1))
        price_batches = [all_prices[i:i + estimation_batch_size] for i in range(0, len(all_prices), estimation_batch_size)]
        
        estimated_counts = {}

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            
            total_bits_sent_by_reader += (seed.bit_length() + estimation_frame_size.bit_length() + num_prices_in_batch.bit_length())

            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=estimation_frame_size, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % estimation_frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---

            total_tag_bits_sent += estimation_frame_size * num_prices_in_batch
            
            # --- 读写器分析聚合的Frame ---
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = estimation_frame_size - sum(logical_bit_vector)
                
                if num_zeros == 0:
                    estimated_counts[price] = float('inf')
                elif num_zeros == estimation_frame_size:
                    estimated_counts[price] = 0
                else:
                    estimated_counts[price] = -estimation_frame_size * math.log(num_zeros / estimation_frame_size)

        # =================================================================================
        # --- 突变部分：引入“自适应竞争者分组”全新机制 ---
        # 与父代协议采用固定数量候选者或逐一验证的策略不同，本协议引入了一个全新的
        # 启发式分组机制。在全局估算后，读写器会动态分析估算值的分布，自动寻找第一个
        # 明显的“数量断层”，并将此断层之前所有估算值相近的“头部”价格识别为一个
        # “竞争者小组”。这种方法使得协议能够根据实际数据分布，智能地将验证资源集中在
        # 最有希望成为众数的、统计意义上无法区分的一组价格上，避免了对无望候选者的
        # 资源浪费，也保证了对所有潜在众数的一视同仁。
        # =================================================================================

        # --- 阶段二: 自适应竞争者分组 (Adaptive Contender Grouping) ---
        sorted_candidates = sorted(
            estimated_counts.items(),
            key=lambda item: (item[1] is float('inf'), item[1]),
            reverse=True
        )

        contender_group = []
        if sorted_candidates:
            # 至少包含估算值最高的候选者
            contender_group.append(sorted_candidates[0])
            last_count = sorted_candidates[0][1]
            if last_count == float('inf'): # 如果最高估算值为inf，则所有inf的都加入
                 for price, count in sorted_candidates[1:]:
                     if count == float('inf'):
                         contender_group.append((price, count))
                     else:
                         break # inf块结束
            else: # 最高估算值为有限值
                for price, count in sorted_candidates[1:]:
                    # 如果当前计数值与上一个相差不大，则加入竞争组
                    if count >= last_count * candidate_group_dropoff_ratio:
                        contender_group.append((price, count))
                        # last_count = count # 可选：是否更新比较基准
                    else:
                        break # 发现第一个显著的“断层”
            
            # 限制竞争者小组的最大规模
            contender_group = contender_group[:max_contender_group_size]

        if not contender_group or contender_group[0][1] == 0:
             # 如果没有候选者或最高估算为0，则提前结束
             return [], 0, 0.0, {"status": "No potential modes found in estimation phase."}

        # --- 阶段三: 并行验证 (Parallel Contender Verification) ---
        final_counts = {}
        
        # 准备并行验证
        contender_prices = [p for p, c in contender_group]
        num_contenders = len(contender_prices)
        
        # 根据估算值动态设定一个更精确的帧长
        sum_of_estimates = sum(c for p, c in contender_group if c != float('inf'))
        # 如果有inf，需要一个较大的基准值
        if float('inf') in (c for p, c in contender_group):
            sum_of_estimates += 256 * len([c for p,c in contender_group if c==float('inf')])

        verification_frame_size = max(256, int(round(sum_of_estimates * 2.0)))
        
        contender_price_to_bit_pos = {price: i for i, price in enumerate(contender_prices)}

        total_frames += 1
        seed_v = generate_random_seed()
        total_bits_sent_by_reader += (seed_v.bit_length() + verification_frame_size.bit_length() + num_contenders.bit_length())

        # --- [物理层模拟] ---
        verify_physical_frame = Frame(num_slots=verification_frame_size, bits_per_slot=num_contenders)
        contender_price_set = set(contender_prices)
        for t in tags:
            if t.price in contender_price_set:
                slot_id = simple_hash(t.id, seed_v) % verification_frame_size
                bit_pos = contender_price_to_bit_pos[t.price]
                reply_vector = 1 << bit_pos
                verify_physical_frame.aggregate(slot_id, reply_vector)
        # --- [物理层模拟结束] ---

        total_tag_bits_sent += verification_frame_size * num_contenders
        
        # --- 读写器分析高精度验证帧 ---
        for price, bit_pos in contender_price_to_bit_pos.items():
            logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in verify_physical_frame.slots]
            num_zeros = verification_frame_size - sum(logical_bit_vector)
            
            if num_zeros == 0:
                final_counts[price] = float('inf')
            elif num_zeros == verification_frame_size:
                final_counts[price] = 0
            else:
                final_counts[price] = -verification_frame_size * math.log(num_zeros / verification_frame_size)


        # --- 阶段四: 结果汇总与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead
        
        verification_data = {
            "protocol_name": "AdaptiveParallelVerificationProtocol",
            "phase1_top5_estimates": {p: (round(c, 2) if c != float('inf') else 'inf') for p, c in sorted_candidates[:5]},
            "phase2_contender_group_size": len(contender_group),
            "phase3_final_counts": {p: (round(c, 2) if c != float('inf') else 'inf') for p, c in final_counts.items()}
        }
        
        if not final_counts:
            return [], 0, total_time_ms, verification_data

        max_count = 0
        if float('inf') in final_counts.values():
            max_count = float('inf')
        else:
            valid_counts = [c for c in final_counts.values()]
            if valid_counts:
                max_count = max(valid_counts)
        
        modes = []
        if max_count > 0:
            if max_count == float('inf'):
                modes = sorted([price for price, count in final_counts.items() if count == float('inf')])
            else:
                modes = sorted([price for price, count in final_counts.items() if math.isclose(count, max_count)])
        
        final_max_count = math.ceil(max_count) if max_count != float('inf') else float('inf')
        
        return modes, final_max_count, total_time_ms, verification_data
