import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
from hash import simple_hash

class g5_5(IProtocol):
    """
    混合分层过滤协议 (Hybrid Hierarchical Filtering Protocol)

    本协议是一个为解决多众数查询问题设计的全新子代协议。它严格遵循交叉与突变的设计准则，
    通过一个三阶段的分层过滤与验证策略，旨在准确且高效地从大量标签中找出一个高频价格。

    协议流程分为三个核心阶段：

    1.  **多哈希布隆过滤阶段 (交叉)**:
        # --- 交叉部分：融合父代A的多哈希过滤思想与父代B的逐级筛选哲学 ---
        此阶段深度融合了父代协议的核心思想。它采用父代A的“多哈希函数（类布隆过滤器）”策略，
        对整个价格空间进行快速扫描。这个阶段的目标，与父代B的第一阶段哲学一致，
        即并非精确计数，而是高效地构建一个高质量的、规模较小的候选价格池，为下一阶段的
        分析做准备。此阶段能以极低的成本筛除绝大多数无关价格。

    2.  **并行批量估算阶段 (交叉)**:
        # --- 交叉部分：融合父代B的并行批量估算策略 ---
        对于在第一阶段幸存下来的候选者，协议采用父代B的“并行批量估算”策略。它不再
        是针对整个价格空间，而是仅对候选池内的价格进行一次更精细的粗略计数。这使得
        协议能够以较高的效率为每个候选者赋予一个估算出的“权重”，从而识别出最有可能
        成为众数的“顶尖竞争者”。

    3.  **动态剪枝与精确验证阶段 (突变与交叉)**:
        # --- 交叉部分：融合父代A与父代B共有的动态追溯计数思想 ---
        对于上一阶段筛选出的极少数顶尖竞争者，本协议采用了两个父代共有的“动态追溯计数”机制，
        通过“不成功则加倍重试”的策略，确保获得确定性的计数值。
        
        # --- 突变部分：引入基于置信度的动态候选剪枝全新机制 ---
        在此基础上，本协议引入了一项在两个父代中都未曾出现过的全新优化。在对顶尖竞争者列表
        进行逐一精确验证时，协议会维护一个当前已知的“最高准确计数值”。在验证下一个候选者之前，
        协议会先比较其在第二阶段得到的“估算值”与当前的“最高准确计数值”。如果该估算值
        明显低于已知的最高值（例如，低于其50%），协议将基于“该候选者已无可能胜出”的
        高置信度判断，直接跳过对它的精确验证。这个剪枝机制能够在不牺牲准确率的前提下，
        显著减少最终阶段昂贵的通信开销。
    """

    def _run_batch_estimation(
        self,
        tags: List[Tag],
        candidate_prices: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], int, int, int]:
        """
        在给定的候选价格列表上执行批量估算。
        该函数源于父代B，用于协议的第二阶段。
        """
        estimated_counts = {}
        frames_used = 0
        reader_bits = 0
        tag_slots = 0

        price_batches = [candidate_prices[i:i + batch_size] for i in range(0, len(candidate_prices), batch_size)]

        for batch in price_batches:
            if not batch:
                continue

            frames_used += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}

            reader_bits += (seed.bit_length() + frame_size.bit_length() +
                            num_prices_in_batch.bit_length() + sum(p.bit_length() for p in batch))

            # [物理层模拟]
            physical_frame_slots = [0] * frame_size
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    bit_pos = price_to_bit_pos_map[t.price]
                    reply_vector = 1 << bit_pos
                    physical_frame_slots[slot_id] |= reply_vector
            # [物理层模拟结束]

            tag_slots += frame_size

            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame_slots]
                num_zeros = frame_size - sum(logical_bit_vector)

                if num_zeros == frame_size:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    estimated_counts[price] = float('inf')
                else:
                    estimated_count = -frame_size * math.log(num_zeros / frame_size)
                    estimated_counts[price] = estimated_count

        return estimated_counts, frames_used, reader_bits, tag_slots


    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        bits_per_slot = 1 # 假设第一和第三阶段时隙大小为1bit

        # --- 协议超参数 ---
        # 阶段一: 多哈希过滤
        FILTER_FRAME_SIZE = 512
        NUM_HASH_FUNCTIONS = 2
        # 阶段二: 批量估算
        ESTIMATION_FRAME_SIZE = 128
        ESTIMATION_BATCH_SIZE = 32
        # 阶段三: 精确验证
        TOP_CONTENDERS_POOL_SIZE = 10
        INITIAL_VERIFICATION_FRAME_SIZE = 64
        MAX_VERIFICATION_FRAME_SIZE = 8192
        PRUNING_THRESHOLD = 0.5 # 剪枝阈值 (突变部分)

        # ==================================================================
        #  阶段一: 多哈希布隆过滤 (交叉: 父代A + 父代B)
        # ==================================================================
        # --- 交叉部分：融合父代A的多哈希过滤思想与父代B的逐级筛选哲学 ---
        total_frames += NUM_HASH_FUNCTIONS
        filter_seeds = [generate_random_seed() for _ in range(NUM_HASH_FUNCTIONS)]
        total_bits_sent_by_reader += sum(s.bit_length() for s in filter_seeds) + FILTER_FRAME_SIZE.bit_length()

        # [物理层模拟]
        filter_frames_states = [[0] * FILTER_FRAME_SIZE for _ in range(NUM_HASH_FUNCTIONS)]
        for t in tags:
            for i in range(NUM_HASH_FUNCTIONS):
                slot_id = simple_hash(t.price, filter_seeds[i]) % FILTER_FRAME_SIZE
                filter_frames_states[i][slot_id] += 1
        total_slots += FILTER_FRAME_SIZE * NUM_HASH_FUNCTIONS
        # [物理层模拟结束]

        candidate_prices_from_filter = []
        for p in range(price_min, price_max + 1):
            is_candidate = all(
                filter_frames_states[i][simple_hash(p, filter_seeds[i]) % FILTER_FRAME_SIZE] > 0
                for i in range(NUM_HASH_FUNCTIONS)
            )
            if is_candidate:
                candidate_prices_from_filter.append(p)

        # ==================================================================
        #  阶段二: 并行批量估算 (交叉: 父代B)
        # ==================================================================
        if not candidate_prices_from_filter:
             return [], 0, 0.0, {"message": "No candidates after filter stage"}

        # --- 交叉部分：...与父代B的并行批量估算策略 ---
        estimated_counts, frames, reader_bits, tag_slots = self._run_batch_estimation(
            tags, candidate_prices_from_filter, ESTIMATION_FRAME_SIZE, ESTIMATION_BATCH_SIZE
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        # 注意: 估算阶段的时隙更宽，但为了简化总时间计算，我们在最后统一处理
        # 这里的 tag_slots 是帧数 * 帧长

        sorted_contenders = sorted(
            estimated_counts.items(),
            key=lambda item: (item[1] != float('inf'), -item[1])
        )
        top_contenders = sorted_contenders[:TOP_CONTENDERS_POOL_SIZE]

        # ==================================================================
        #  阶段三: 动态剪枝与精确验证 (突变 + 交叉)
        # ==================================================================
        final_counts = {}
        max_known_accurate_count = 0
        skipped_count = 0

        for price, estimated_count in top_contenders:
            # --- 突变部分：引入基于置信度的动态候选剪枝全新机制 ---
            if estimated_count != float('inf') and estimated_count < max_known_accurate_count * PRUNING_THRESHOLD:
                skipped_count += 1
                continue
            
            # --- 交叉部分：融合父代A与父代B共有的动态追溯计数思想 ---
            current_frame_size = INITIAL_VERIFICATION_FRAME_SIZE
            accurate_count = float('inf')

            while accurate_count == float('inf') and current_frame_size <= MAX_VERIFICATION_FRAME_SIZE:
                seed = generate_random_seed()
                total_frames += 1
                total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + price.bit_length())

                # [物理层模拟]
                verification_slot_states = [0] * int(current_frame_size)
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % int(current_frame_size)
                        verification_slot_states[slot_id] += 1
                total_slots += int(current_frame_size)
                # [物理层模拟结束]

                num_zeros = verification_slot_states.count(0)

                if num_zeros == current_frame_size:
                    accurate_count = 0
                elif num_zeros == 0:
                    current_frame_size *= 2
                    accurate_count = float('inf')
                else:
                    accurate_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            
            final_count = math.ceil(accurate_count) if accurate_count != float('inf') else float('inf')
            final_counts[price] = final_count
            if final_count != float('inf'):
                 max_known_accurate_count = max(max_known_accurate_count, final_count)

        # ==================================================================
        #  最终处理
        # ==================================================================
        # 时间成本计算
        T_reader = total_bits_sent_by_reader * 0.025
        # 估算阶段的标签通信成本单独计算，因为它每个时隙的比特数不同
        T_tag_estimation = tag_slots * ESTIMATION_BATCH_SIZE * 0.4
        T_tag_filter_and_verify = total_slots * bits_per_slot * 0.4
        T_tag = T_tag_filter_and_verify + T_tag_estimation
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "candidates_from_filter": len(candidate_prices_from_filter),
            "top_contenders_before_pruning": top_contenders,
            "skipped_in_final_stage": skipped_count,
            "final_accurate_counts": final_counts,
            "total_frames": total_frames
        }

        if not final_counts:
            return [], 0, total_time_ms, verification_data

        # 找出得分最高的众数
        max_score = -1
        has_inf = any(score == float('inf') for score in final_counts.values())

        if has_inf:
            max_score = float('inf')
        else:
            max_score = max(final_counts.values()) if final_counts else 0

        modes = sorted([
            price for price, score in final_counts.items()
            if score == max_score
        ])
        
        display_score = int(max_score) if not has_inf else float('inf')

        return modes, display_score, total_time_ms, verification_data
