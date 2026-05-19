import math
from typing import List, Tuple, Dict, Set
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g9_1(IProtocol):
    """
    子代协议: 自适应确认协议 (Adaptive Confirmation Protocol)

    本协议为解决多众数查询问题而设计，其核心是通过深度融合父代协议的优势（交叉）并引入全新的优化机制（突变）来提升效率和准确性。
    协议严格遵循信息隔离原则，在执行期间，读写器无法访问任何标签的私有属性。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_response_bits = 0

        # --- 协议超参数 ---
        # 阶段一: 过滤相关
        initial_filter_frame_size = 256
        num_hash_functions = 2
        max_filter_rounds = 4
        candidate_stop_threshold = 48
        # 父代A的自适应帧长调整参数
        high_density_threshold = 0.75
        low_density_threshold = 0.25
        min_frame_size = 64
        max_frame_size = 1024

        # 阶段二: 验证相关 (源自父代A)
        verification_batch_size = 32

        # --- 突变部分引入的超参数 ---
        confirmation_threshold = 4  # 当映射到单例时隙的候选价格数量小于等于此值时，触发即时确认

        # ==================================================================
        #  阶段一: 自适应过滤 & 单例即时确认
        # --- 交叉部分：融合父代A的“自适应帧长调整”思想与父代B(和A共有)的“布隆过滤”核心机制 ---
        # ==================================================================
        candidate_prices: Set[int] = set(range(price_min, price_max + 1))
        # `provisional_scores` 用于存储在突变阶段被提前确认的价格及其频次
        provisional_scores: Dict[int, int] = {}
        
        current_filter_frame_size = initial_filter_frame_size
        
        for round_num in range(max_filter_rounds):
            if len(candidate_prices) <= candidate_stop_threshold:
                break

            total_frames += num_hash_functions
            filter_seeds = [generate_random_seed() for _ in range(num_hash_functions)]
            # 读写器广播帧长和哈希种子
            total_bits_sent_by_reader += num_hash_functions * (current_filter_frame_size.bit_length() + 32)

            # --- [物理层模拟] ---
            # 读写器准备接收帧，每个slot记录响应次数 (0: 空, 1: 单例, >1: 碰撞)
            filter_frames_states = [[0] * current_filter_frame_size for _ in range(num_hash_functions)]
            for t in tags:
                if t.price in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(t.price, filter_seeds[i]) % current_filter_frame_size
                        filter_frames_states[i][slot_id] += 1
            # 在过滤阶段，标签只回复1比特表示存在性
            total_tag_response_bits += current_filter_frame_size * num_hash_functions * 1
            # --- [物理层模拟结束] ---

            # --- 读写器分析聚合信号 ---
            # 1. 识别单例时隙以备突变阶段使用
            singleton_slots_info: List[Tuple[int, int]] = [] # (hash_func_index, slot_id)
            for i in range(num_hash_functions):
                for j in range(current_filter_frame_size):
                    if filter_frames_states[i][j] == 1:
                        singleton_slots_info.append((i, j))

            # 2. 布隆过滤：移除映射到空时隙的候选者
            prices_to_remove = set()
            for p in candidate_prices:
                for i in range(num_hash_functions):
                    slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                    if filter_frames_states[i][slot_id] == 0:
                        prices_to_remove.add(p)
                        break
            candidate_prices -= prices_to_remove
            
            # --- 突变部分：引入“单例时隙即时确认”(Singleton Slot Instant Confirmation)机制 ---
            # 对于每个单例时隙，如果映射到它的候选价格数量很少，就立即发起一轮小范围查询来确定那个唯一标签的价格
            confirmed_prices_in_round = set()
            if singleton_slots_info:
                # 构建从时隙到可能价格的反向映射
                slot_to_prices_map: Dict[Tuple[int, int], List[int]] = {info: [] for info in singleton_slots_info}
                for p in candidate_prices:
                    for i in range(num_hash_functions):
                        slot_id = simple_hash(p, filter_seeds[i]) % current_filter_frame_size
                        if (i, slot_id) in slot_to_prices_map:
                            slot_to_prices_map[(i, slot_id)].append(p)

                for slot_info, mapped_prices in slot_to_prices_map.items():
                    # 只有当映射到此时隙的候选者足够少时才值得确认
                    if 0 < len(mapped_prices) <= confirmation_threshold:
                        total_frames += 1
                        confirm_frame_size = len(mapped_prices)
                        price_to_pos_map = {price: i for i, price in enumerate(mapped_prices)}

                        # 读写器广播此次确认查询的参数
                        total_bits_sent_by_reader += confirm_frame_size.bit_length() + sum(p.bit_length() for p in mapped_prices)
                        
                        # --- [物理层模拟 - 确认阶段] ---
                        # 理论上只有一个标签会响应
                        confirm_frame = [0] * confirm_frame_size
                        hash_idx, original_slot_id = slot_info
                        original_seed = filter_seeds[hash_idx]
                        
                        for t in tags:
                            if t.price in price_to_pos_map:
                                # 确保是之前响应到那个单例时隙的标签
                                if simple_hash(t.price, original_seed) % current_filter_frame_size == original_slot_id:
                                    pos = price_to_pos_map[t.price]
                                    confirm_frame[pos] = 1 # 响应
                        total_tag_response_bits += confirm_frame_size * 1
                        # --- [物理层模拟结束] ---
                        
                        # 读写器分析确认结果
                        for price, pos in price_to_pos_map.items():
                            if confirm_frame[pos] == 1:
                                provisional_scores[price] = provisional_scores.get(price, 0) + 1
                                confirmed_prices_in_round.add(price)
                                break # 找到响应者后即可退出
            
            # 从主候选集中移除已被确认的价格，避免在后续过滤轮次中产生干扰
            candidate_prices -= confirmed_prices_in_round

            # 3. (源自父代A) 根据帧密度自适应调整下一轮的帧长
            total_slots_in_round = num_hash_functions * current_filter_frame_size
            total_non_empty_slots = sum(sum(1 for count in frame if count > 0) for frame in filter_frames_states)
            density = total_non_empty_slots / total_slots_in_round if total_slots_in_round > 0 else 0

            if density > high_density_threshold:
                current_filter_frame_size = min(max_frame_size, current_filter_frame_size * 2)
            elif density < low_density_threshold:
                current_filter_frame_size = max(min_frame_size, current_filter_frame_size // 2)

        # ==================================================================
        #  阶段二: 分批并行验证
        # --- 交叉部分：验证的核心逻辑继承自父代A的“分批处理”与并行位图思想 ---
        # ==================================================================
        final_scores = provisional_scores.copy() # 将突变阶段的成果作为初始分数
        
        if candidate_prices:
            candidate_list = sorted(list(candidate_prices))
            num_batches = math.ceil(len(candidate_list) / verification_batch_size)

            for i in range(num_batches):
                batch_candidates = candidate_list[i * verification_batch_size : (i + 1) * verification_batch_size]
                if not batch_candidates: continue
                
                total_frames += 1
                price_to_bit_pos_map = {price: j for j, price in enumerate(batch_candidates)}
                bits_per_slot = len(batch_candidates)
                verification_frame_size = 128 # 可根据批次大小调整，此处为简化设为定值

                seed_verify = generate_random_seed()
                # 读写器广播验证参数
                total_bits_sent_by_reader += (verification_frame_size.bit_length() +
                                            seed_verify.bit_length() +
                                            sum(p.bit_length() for p in batch_candidates))

                # --- [物理层模拟] ---
                verification_frame_slots = [0] * verification_frame_size
                for t in tags:
                    if t.price in price_to_bit_pos_map:
                        slot_id = simple_hash(t.id, seed_verify) % verification_frame_size
                        bit_pos = price_to_bit_pos_map[t.price]
                        verification_frame_slots[slot_id] |= (1 << bit_pos)
                
                total_tag_response_bits += verification_frame_size * bits_per_slot
                # --- [物理层模拟结束] ---
                
                # 读写器分析聚合信号，计算当前批次中每个价格的得分
                for price, bit_pos in price_to_bit_pos_map.items():
                    score = sum(((slot_data >> bit_pos) & 1) for slot_data in verification_frame_slots)
                    if score > 0:
                        final_scores[price] = final_scores.get(price, 0) + score

        # ==================================================================
        #  最终处理与时间计算
        # ==================================================================
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_response_bits * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 0 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        debug_data = {
            "num_candidates_after_filter": len(candidate_prices) + len(provisional_scores),
            "provisional_scores": provisional_scores,
            "final_scores": final_scores
        }

        if not final_scores:
            return [], 0, total_time_ms, debug_data

        max_score = 0
        for score in final_scores.values():
            if score > max_score:
                max_score = score
        
        modes = sorted([p for p, s in final_scores.items() if s == max_score])

        return modes, max_score, total_time_ms, debug_data
