import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g4_2(IProtocol):
    """
    进化融合协议 (Evolved Fusion Protocol for Mode Query)

    本协议是一个根据遗传算法思想设计的全新子代协议，旨在高效、准确地解决大规模RFID系统中的众数查询问题。
    它严格遵循信息隔离原则，通过“交叉”操作深度融合了两个父代协议(g2_5 和 g3_4)的核心设计哲学，
    并通过“突变”操作引入了一项全新的、基于置信度的提前终止机制，以进一步优化性能。
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- 协议超参数 ---
        FILTER_FRAME_SIZE = 1024          # 过滤阶段帧长 (源于父代A)
        ESTIMATION_BATCH_SIZE = 128       # 估算阶段批处理大小 (源于父代B)
        ESTIMATION_FRAME_SIZE = 256       # 估算阶段帧长 (源于父代B)
        VERIFICATION_FRAME_SCALE_FACTOR = 1.8 # 验证阶段帧长缩放因子 (源于父代B)
        MIN_VERIFICATION_FRAME_SIZE = 64  # 最小验证帧长 (源于父代B)
        CONFIDENCE_THRESHOLD = 0.85       # 置信度阈值，用于提前终止 (突变部分)

        # =================================================================================
        # 阶段一: 全局哈希过滤 (Global Hash Filtering)
        # =================================================================================
        # --- 交叉部分：融合父代A(g2_5)的“确定性槽位过滤”思想与父代B(g3_4)的“分阶段处理”思想 ---
        # 本协议的第一阶段直接采纳了父代A的核心机制：使用一个大的哈希帧对整个价格空间进行一次性过滤。
        # 标签根据自身价格(price)哈希到特定槽位。这相比父代B的区间划分(binning)方法，能更精确、
        # 更快地生成一个候选价格列表，为后续阶段提供了高质量的输入，体现了父代A的效率优势。
        
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + FILTER_FRAME_SIZE.bit_length())

        # --- [物理层模拟] ---
        filtering_frame = Frame(num_slots=FILTER_FRAME_SIZE, bits_per_slot=1)
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % FILTER_FRAME_SIZE
            filtering_frame.aggregate(slot_id, 1)
        # --- [物理层模拟结束] ---
        
        total_tag_bits_sent += FILTER_FRAME_SIZE * 1
        
        active_slot_indices = {i for i, data in enumerate(filtering_frame.slots) if data > 0}
        
        candidate_prices = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % FILTER_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidate prices found after filtering"}

        # =================================================================================
        # 阶段二: 并行基数估算 (Parallel Cardinality Estimation)
        # =================================================================================
        # --- 交叉部分：融合父代A的“候选集”思想与父代B的“并行估算(SEM)”思想 ---
        # 此阶段将第一阶段（源于父代A）产生的候选集，作为父代B核心机制“并行估算”的输入。
        # 通过采用父代B的SEM（Slot-based Estimation Method），我们可以在单次通信中同时估算
        # 一批候选价格的标签数量，极大地提升了处理多个候选者时的通信效率。
        
        estimated_counts = {}
        sorted_candidate_prices = sorted(candidate_prices)
        price_batches = [
            sorted_candidate_prices[i:i + ESTIMATION_BATCH_SIZE] 
            for i in range(0, len(sorted_candidate_prices), ESTIMATION_BATCH_SIZE)
        ]

        for batch in price_batches:
            if not batch: continue
            
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())

            # --- [物理层模拟] ---
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = ESTIMATION_FRAME_SIZE - sum(logical_bit_vector)
                
                if num_zeros == ESTIMATION_FRAME_SIZE:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = ESTIMATION_FRAME_SIZE * 2.5 # Handle saturation
                else:
                    estimated_counts[price] = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)

        # =================================================================================
        # 阶段三: 基于置信度的自适应验证 (Confidence-based Adaptive Verification)
        # =================================================================================
        # --- 突变部分：引入“基于置信度阈值的迭代剪枝”全新机制 ---
        # 两个父代协议在验证阶段要么探测固定数量的候选者，要么使用一个简单的“估算值<已验证最大值”来提前退出。
        # 本协议引入了一个更智能的、全新的剪枝逻辑。在验证完当前的最优候选者后，我们会计算出一个“置信下界”
        # (max_verified_count * CONFIDENCE_THRESHOLD)。对于后续的候选者，如果其估算值甚至都无法超过这个下界，
        # 协议会认定它几乎不可能成为新的众数，并直接终止整个验证阶段。这种基于“置信度”的剪枝策略，
        # 在保留鲁棒性的同时，比父代协议的机制能更早地裁剪掉不必要的工作，从而显著节约时间。
        
        final_counts = {}
        candidates = sorted([p for p, e in estimated_counts.items() if e > 0], key=estimated_counts.get, reverse=True)

        max_verified_count = -1.0
        modes = []
        verified_candidates_info = {}

        for price in candidates:
            # **全新的提前退出检查 (Mutation)**
            if estimated_counts.get(price, 0) < max_verified_count * CONFIDENCE_THRESHOLD:
                break # Terminate verification early

            # 继承自父代B的自适应帧长验证逻辑
            frame_size = max(MIN_VERIFICATION_FRAME_SIZE, int(estimated_counts.get(price, 0) * VERIFICATION_FRAME_SCALE_FACTOR))
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + frame_size.bit_length() + seed.bit_length()
            
            # --- [物理层模拟] ---
            slot_states = [0] * frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_states[slot_id] += 1
            # --- [物理层模拟结束] ---
            
            total_tag_bits_sent += frame_size * 1

            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros == frame_size:
                verified_count = 0
            elif num_zeros == 0:
                verified_count = frame_size * 2.5
            else:
                verified_count = -frame_size * math.log(num_zeros / frame_size)
            
            final_counts[price] = verified_count
            verified_candidates_info[price] = {"initial_estimate": estimated_counts.get(price, 0), "verification_frame_size": frame_size, "verified_count": verified_count}

            if verified_count > max_verified_count:
                max_verified_count = verified_count
                modes = [price]
            elif math.isclose(verified_count, max_verified_count):
                modes.append(price)

        # --- 最终处理与时间计算 ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "candidates_after_filtering": len(candidate_prices),
            "initial_estimates": estimated_counts,
            "candidates_for_verification_sorted": candidates,
            "verified_candidates_details": verified_candidates_info,
            "final_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data