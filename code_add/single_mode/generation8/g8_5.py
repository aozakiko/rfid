import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_5(IProtocol):
    """
    Heuristic Tournament Adaptive Protocol (HTAP)

    This child protocol is designed to efficiently find the mode of tag prices by fusing the strengths of its two parent protocols and introducing a novel optimization.
    It follows a multi-stage approach:
    1.  Dual-Hash Filtering: Inherited from Parent A, this stage uses two hash functions to create a highly accurate initial set of candidate prices, significantly reducing noise.
    2.  Reconnaissance & Smart Promotion: Merges Parent B's tournament-style "seeding" round with its smart promotion logic, advancing candidates based on both high scores and high collision rates.
    3.  Iterative Confirmation: Adopts Parent A's multi-round, confidence-based early termination framework, but enhances it with Parent B's per-candidate dynamic frame sizing for precision.
    4.  Heuristic-Guided Probing (Mutation): A novel mechanism that prioritizes probing candidates in the confirmation stage based on their "promise" (a combination of score and collision rate) from the reconnaissance stage, maximizing the chances of early termination.
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        """
        Executes the core logic of the HTAP protocol.
        """
        # --- Timing and Statistics Variables ---
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- Protocol Hyperparameters ---
        FILTER_FRAME_SIZE_1 = 1024
        FILTER_FRAME_SIZE_2 = 1024
        RECON_FRAME_SIZE = 64
        COLLISION_PROMOTION_THRESHOLD = 0.15 # From Parent B
        MIN_RECON_SCORE_FOR_PROMOTION = 5
        MAX_SCORING_ROUNDS = 3             # From Parent A
        CONFIDENCE_THRESHOLD = 3.5         # From Parent A
        MIN_CONFIRM_FRAME_SIZE = 128
        MAX_CONFIRM_FRAME_SIZE = 1024

        # --- 交叉部分：融合父代A的“双哈希函数协同过滤”思想 ---
        # 阶段一: 高纯度候选集过滤
        # This protocol adopts the powerful dual-hash filtering mechanism from Parent A (g7_5).
        # By requiring a price to map to a non-empty slot in two separate hash frames,
        # we generate a much cleaner and smaller initial candidate set than a single filter,
        # which is crucial for the efficiency of subsequent stages.

        # First hash filtering pass
        total_frames += 1
        filter_seed_1 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_1.bit_length() + FILTER_FRAME_SIZE_1.bit_length()
        
        # [Physical Layer Simulation]
        filtering_slots_1 = [0] * FILTER_FRAME_SIZE_1
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_1) % FILTER_FRAME_SIZE_1
            filtering_slots_1[slot_id] = 1
        # [End Simulation]
        
        total_tag_bits_sent += FILTER_FRAME_SIZE_1 * 1
        active_slot_indices_1 = {i for i, state in enumerate(filtering_slots_1) if state > 0}

        # Second hash filtering pass
        total_frames += 1
        filter_seed_2 = generate_random_seed()
        total_bits_sent_by_reader += filter_seed_2.bit_length() + FILTER_FRAME_SIZE_2.bit_length()
        
        # [Physical Layer Simulation]
        filtering_slots_2 = [0] * FILTER_FRAME_SIZE_2
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed_2) % FILTER_FRAME_SIZE_2
            filtering_slots_2[slot_id] = 1
        # [End Simulation]

        total_tag_bits_sent += FILTER_FRAME_SIZE_2 * 1
        active_slot_indices_2 = {i for i, state in enumerate(filtering_slots_2) if state > 0}

        # Reader locally generates the final candidate set
        candidate_prices = []
        if active_slot_indices_1 and active_slot_indices_2:
            for p in range(price_min, price_max + 1):
                hash1_in_set = (simple_hash(p, filter_seed_1) % FILTER_FRAME_SIZE_1) in active_slot_indices_1
                hash2_in_set = (simple_hash(p, filter_seed_2) % FILTER_FRAME_SIZE_2) in active_slot_indices_2
                if hash1_in_set and hash2_in_set:
                    candidate_prices.append(p)

        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No candidates found after dual-hash filtering"}

        # --- 交叉部分：融合父代B的“锦标赛预选赛与智能晋级”思想 ---
        # 阶段二: 侦察与启发式评估
        # This stage acts as the "seeding round" from Parent B's (g7_3) tournament structure.
        # We perform a low-cost probe on each candidate to gather preliminary data: score and collision rate.
        # This data will be used for both smart promotion and for the novel heuristic ranking.
        recon_results = {}
        for price in candidate_prices:
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + RECON_FRAME_SIZE.bit_length() + seed.bit_length()

            # [Physical Layer Simulation]
            slot_states = [0] * RECON_FRAME_SIZE
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % RECON_FRAME_SIZE
                    slot_states[slot_id] += 1
            # [End Simulation]
            
            total_tag_bits_sent += RECON_FRAME_SIZE * 1
            
            num_non_empty = sum(1 for c in slot_states if c > 0)
            num_collisions = sum(1 for c in slot_states if c > 1)
            collision_rate = num_collisions / num_non_empty if num_non_empty > 0 else 0
            recon_results[price] = {'score': num_non_empty, 'collision_rate': collision_rate}

        # --- 突变部分：引入“基于启发式评估的探测优先级排序”全新机制 ---
        # 协议的核心突变点。我们不按任意顺序探测决赛选手，而是先计算一个“启发值”，
        # 综合了他们在侦察阶段的得分和冲突率。得分高或冲突高的候选者被认为更有潜力，
        # 值得被优先探测。这增加了在评分早期就发现真实众数的概率，从而最大化了提前终止的可能性，
        # 这是两个父代协议都未曾采用的优化策略。
        
        # Calculate heuristic promise score for mutation
        if recon_results:
            max_recon_score = max((res['score'] for res in recon_results.values()), default=1)
            for price, results in recon_results.items():
                # Simple heuristic: normalized score + collision rate
                normalized_score = results['score'] / max_recon_score if max_recon_score > 0 else 0
                results['promise'] = normalized_score + results['collision_rate']
        
        # Smart promotion logic from Parent B to select finalists
        max_seeding_score = max((res['score'] for res in recon_results.values()), default=0)
        finalists_set = set()
        if max_seeding_score > 0:
            for price, result in recon_results.items():
                # Rule 1: Highest score advances (Parent B's tournament logic)
                if result['score'] == max_seeding_score:
                    finalists_set.add(price)
                # Rule 2: High collision rate advances (Parent B's key insight)
                if result['score'] > MIN_RECON_SCORE_FOR_PROMOTION and result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                    finalists_set.add(price)
        
        if not finalists_set:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No finalists promoted after reconnaissance"}

        # [Mutation in action] Sort finalists by promise to guide probing
        finalists = sorted(list(finalists_set), key=lambda p: recon_results[p].get('promise', 0), reverse=True)

        # --- 交叉部分：融合父代A的“多轮迭代与置信度早停”与父代B的“个性化动态帧”思想 ---
        # 阶段三: 启发式引导下的自适应迭代确认
        # This stage forms the core scoring loop. Its structure is based on Parent A's (g7_5) iterative refinement
        # with confidence-based early termination. However, the frame size for each probe is determined
        # by Parent B's (g7_3) more precise method: dynamically calculating it based on the estimated
        # tag count (recon score) for that specific candidate. This fusion creates a loop that is both
        # macro-efficient (early exit) and micro-efficient (optimal frame per probe).
        
        current_scores = {}
        final_round_num = 0

        for round_num in range(MAX_SCORING_ROUNDS):
            final_round_num = round_num + 1
            
            # [Mutation in action] Probe in the heuristically sorted order
            for price in finalists:
                # Per-candidate dynamic frame sizing from Parent B
                estimated_tags = recon_results[price]['score']
                # A simple function to determine frame size, can be more complex
                dynamic_frame_size = int(max(MIN_CONFIRM_FRAME_SIZE, min(estimated_tags * 2.0, MAX_CONFIRM_FRAME_SIZE)))

                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += price.bit_length() + dynamic_frame_size.bit_length() + seed.bit_length()

                # [Physical Layer Simulation]
                slot_states = [0] * dynamic_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % dynamic_frame_size
                        slot_states[slot_id] += 1
                # [End Simulation]
                
                total_tag_bits_sent += dynamic_frame_size * 1
                current_scores[price] = sum(1 for c in slot_states if c > 0)

            # Confidence-based early termination logic from Parent A
            if not current_scores or max(current_scores.values()) == 0:
                break
            
            sorted_scores = sorted(current_scores.values(), reverse=True)
            max_score = sorted_scores[0]
            
            if len(sorted_scores) < 2:
                break # Only one candidate left, can terminate

            second_max_score = sorted_scores[1]
            if second_max_score > 0 and (max_score / second_max_score) >= CONFIDENCE_THRESHOLD:
                break # High confidence, terminate early

            # Prune candidates for the next round, as in Parent A
            if round_num < MAX_SCORING_ROUNDS - 1:
                next_finalists = [p for p, s in current_scores.items() if s >= second_max_score]
                # Re-sort the remaining finalists by their original promise
                finalists = sorted(list(set(next_finalists)), key=lambda p: recon_results[p].get('promise', 0), reverse=True)
                if len(finalists) <= 1:
                    break

        # --- Final Processing and Time Calculation ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused A's dual-filter and iterative early-exit with B's tournament promotion and dynamic per-candidate frame sizing.",
            "mutation_info": "Introduced heuristic-guided probing order to maximize probability of early termination.",
            "initial_candidate_count": len(candidate_prices),
            "finalist_count": len(finalists_set),
            "final_round_num": final_round_num,
            "final_scores": current_scores
        }
        
        if not current_scores:
            return [], 0, total_time_ms, verification_data

        max_final_score = max(current_scores.values())
        if max_final_score == 0:
            return [], 0, total_time_ms, verification_data
            
        modes = sorted([
            price for price, score in current_scores.items() 
            if score == max_final_score
        ])
        
        return modes, max_final_score, total_time_ms, verification_data
