import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed
from hash import simple_hash

class g8_2(IProtocol):
    """
    Adaptive Multi-stage Contender Refinement Protocol (AMCR)

    This protocol is a new generation solution designed to efficiently and accurately solve the mode query problem
    in large-scale RFID systems. It strictly adheres to the principle of information isolation, deeply integrates
    the core ideas of its two parent protocols, and introduces a novel "per-contender adaptive probing"
    mechanism to achieve highly dynamic and efficient resource allocation.
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- Protocol Hyperparameters ---
        # Phase 1: Global Filtering
        GLOBAL_FILTER_FRAME_SIZE = 1024

        # Phase 2: Iterative Refinement
        MAX_REFINEMENT_ROUNDS = 4
        INITIAL_PROBE_FRAME_SIZE = 32
        
        # Crossover Parameters
        COLLISION_PROMOTION_THRESHOLD = 0.20 # Collision rate threshold for promoting a contender
        MIN_SCORE_FOR_COLLISION_PROMOTION = 5 # Minimum score to be considered for collision-based promotion

        # Mutation Parameters
        MIN_ADAPTIVE_FRAME_SIZE = 64
        MAX_ADAPTIVE_FRAME_SIZE = 2048
        CONFIDENCE_THRESHOLD_FOR_EARLY_EXIT = 3.5 # (Top Score / Second Top Score)

        # --- Phase 1: Global Candidate Filtering ---
        total_frames += 1
        filter_seed = generate_random_seed()
        total_bits_sent_by_reader += (filter_seed.bit_length() + GLOBAL_FILTER_FRAME_SIZE.bit_length())

        # --- [Physical Layer Simulation] ---
        filtering_slots = [0] * GLOBAL_FILTER_FRAME_SIZE
        for t in tags:
            slot_id = simple_hash(t.price, filter_seed) % GLOBAL_FILTER_FRAME_SIZE
            filtering_slots[slot_id] = 1 # We only care if the slot is non-empty
        # --- [End of Physical Layer Simulation] ---
        
        total_tag_bits_sent += GLOBAL_FILTER_FRAME_SIZE * 1

        active_slot_indices = {i for i, state in enumerate(filtering_slots) if state > 0}
        
        candidate_prices = []
        if active_slot_indices:
            for p in range(price_min, price_max + 1):
                if simple_hash(p, filter_seed) % GLOBAL_FILTER_FRAME_SIZE in active_slot_indices:
                    candidate_prices.append(p)
        
        if not candidate_prices:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            return [], 0, T_reader + T_tag, {"reason": "No candidates found after global filtering"}

        # --- Phase 2: Iterative, Adaptive Refinement ---
        contenders = candidate_prices
        round_results = {}
        protocol_log = []

        for round_num in range(MAX_REFINEMENT_ROUNDS):
            current_round_results = {}
            if not contenders:
                protocol_log.append(f"Round {round_num+1}: No contenders left, stopping.")
                break
            
            protocol_log.append(f"Round {round_num+1}: Probing {len(contenders)} contenders.")

            for price in contenders:
                # --- Mutation Part: Introducing the "Per-Contender Adaptive Probing" novel mechanism ---
                # Neither parent protocol adjusts the frame size for each individual contender within a single evaluation round.
                # Parent A (g7_3) uses a dynamic frame size only in the final championship round.
                # Parent B (g6_1) uses a globally increasing frame size for all contenders between rounds.
                # This protocol introduces a novel mechanism: for each round, we calculate a tailored frame size
                # for each contender based on its performance (score and collision rate) in the previous round.
                # This allows the protocol to allocate resources more intelligently, using larger frames for contenders
                # that appear to be numerous (high score or high collision) and smaller frames for less likely candidates.
                
                probe_frame_size = INITIAL_PROBE_FRAME_SIZE
                if round_num > 0 and price in round_results:
                    prev_result = round_results[price]
                    prev_score = prev_result['score']
                    prev_frame_size = prev_result['frame_size']
                    
                    # Estimate tag count using the Eom-Lee estimator for non-empty slots
                    if prev_frame_size > prev_score > 0:
                        estimated_tags = prev_frame_size * math.log(prev_frame_size / (prev_frame_size - prev_score))
                        
                        # Apply a multiplier for high collision rates, as the estimate is likely low
                        if prev_result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                            estimated_tags *= 2.0
                        
                        # Calculate optimal frame size (theoretically N) with a safety margin
                        optimal_size = int(estimated_tags * 1.8)
                        probe_frame_size = max(MIN_ADAPTIVE_FRAME_SIZE, min(optimal_size, MAX_ADAPTIVE_FRAME_SIZE))
                    elif prev_score > 0:
                        # If frame is saturated, aggressively increase the size
                        probe_frame_size = prev_frame_size * 4

                total_frames += 1
                seed = generate_random_seed()
                total_bits_sent_by_reader += (price.bit_length() + probe_frame_size.bit_length() + seed.bit_length())

                # --- [Physical Layer Simulation] ---
                slot_states = [0] * probe_frame_size
                for t in tags:
                    if t.price == price:
                        slot_id = simple_hash(t.id, seed) % probe_frame_size
                        slot_states[slot_id] += 1
                # --- [End of Physical Layer Simulation] ---
                
                total_tag_bits_sent += probe_frame_size * 1
                
                num_non_empty = sum(1 for count in slot_states if count > 0)
                num_collisions = sum(1 for count in slot_states if count > 1)
                collision_rate = num_collisions / probe_frame_size if probe_frame_size > 0 else 0

                current_round_results[price] = {
                    'score': num_non_empty, 
                    'collision_rate': collision_rate, 
                    'frame_size': probe_frame_size
                }
            
            round_results = current_round_results
            
            # --- Crossover Part: Fusing Parent A's evaluation logic with Parent B's iterative framework ---
            # This section merges the core philosophies of both parents.
            # From Parent B (g6_1), we inherit the iterative refinement structure and the concept of
            # early termination based on a confidence score.
            # From Parent A (g7_3), we inherit the more sophisticated contender evaluation logic, which considers
            # not only the highest score but also promotes candidates with a high collision rate,
            # identifying them as potential "underestimated" winners.

            if not round_results or max((res['score'] for res in round_results.values()), default=0) == 0:
                break 

            sorted_scores = sorted(list(set(res['score'] for res in round_results.values())), reverse=True)
            max_score = sorted_scores[0]
            
            # Check for early termination (Parent B's idea)
            if len(sorted_scores) > 1:
                second_max_score = sorted_scores[1]
                if second_max_score > 0 and (max_score / second_max_score) >= CONFIDENCE_THRESHOLD_FOR_EARLY_EXIT:
                    protocol_log.append(f"Round {round_num+1}: Confidence threshold met. Terminating early.")
                    break
            elif len(sorted_scores) == 1 and max_score > 0: # Only one score level left
                 protocol_log.append(f"Round {round_num+1}: Only one score level remains. Terminating early.")
                 break

            # If not terminating, select contenders for the next round (Parent A's idea)
            if round_num < MAX_REFINEMENT_ROUNDS - 1:
                next_contenders = set()
                for price, result in round_results.items():
                    # Rule 1: Highest scorers always advance
                    if result['score'] == max_score:
                        next_contenders.add(price)
                    # Rule 2: High-collision contenders also advance
                    if result['score'] >= MIN_SCORE_FOR_COLLISION_PROMOTION and result['collision_rate'] > COLLISION_PROMOTION_THRESHOLD:
                        next_contenders.add(price)
                contenders = sorted(list(next_contenders))

        # --- Phase 3: Final Decision and Time Calculation ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "crossover_info": "Fused Parent B's iterative refinement with Parent A's dual-criteria (score + collision) promotion logic.",
            "mutation_info": "Introduced per-contender, per-round adaptive frame sizing based on previous round's performance.",
            "initial_candidate_count": len(candidate_prices),
            "final_round_num": round_num + 1,
            "final_results": round_results,
            "log": protocol_log
        }

        if not round_results:
            return [], 0, total_time_ms, verification_data

        final_max_score = max((res['score'] for res in round_results.values()), default=0)
        if final_max_score == 0:
            return [], 0, total_time_ms, verification_data

        modes = sorted([p for p, res in round_results.items() if res['score'] == final_max_score])
        
        return modes, final_max_score, total_time_ms, verification_data