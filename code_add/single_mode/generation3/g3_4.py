import math
from typing import List, Tuple, Dict
from base import Tag, IProtocol, generate_random_seed, Frame
# Make sure the hash function file is named "hash.py"
from hash import simple_hash

class g3_4(IProtocol):
    """
    Hybrid Adaptive Protocol for RFID Mode Query (子代协议：混合自适应众数查询)

    This protocol is a novel child protocol designed for efficient and accurate mode query in large-scale RFID systems.
    It strictly adheres to the information isolation principle, synthesizes the core design philosophies of its two parent protocols,
    and introduces an innovative optimization mechanism.
    """
    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_tag_bits_sent = 0

        # --- Protocol Hyperparameters ---
        PRECHECK_BINS = 8
        PRECHECK_FRAME_SIZE = 64
        ESTIMATION_BATCH_SIZE = 120
        ESTIMATION_FRAME_SIZE = 256
        VERIFICATION_FRAME_SCALE_FACTOR = 1.5 # Scale factor for adaptive frame size
        MIN_VERIFICATION_FRAME_SIZE = 64      # Minimum frame size for verification

        # =================================================================================
        # Stage 1: Coarse-Grained Pruning (粗粒度剪枝)
        # =================================================================================
        # --- Crossover Part: Fusing the idea of "staged pruning" from Parent A with the "parallel estimation" from Parent B ---
        # This initial pruning stage is a common, effective strategy found in both parents. It quickly eliminates price ranges
        # where no tags exist, significantly reducing the search space for subsequent, more resource-intensive stages.
        
        active_prices = set()
        price_range = price_max - price_min + 1
        bin_size = math.ceil(price_range / PRECHECK_BINS)
        price_bins = [
            list(range(price_min + i * bin_size, min(price_min + (i + 1) * bin_size, price_max + 1)))
            for i in range(PRECHECK_BINS)
        ]

        for p_bin in price_bins:
            if not p_bin: continue
            
            # --- Reader Behavior: Broadcast Command ---
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += (seed.bit_length() + PRECHECK_FRAME_SIZE.bit_length() + p_bin[0].bit_length() + p_bin[-1].bit_length())
            
            # --- [Physical Layer Simulation] ---
            slot_states = [0] * PRECHECK_FRAME_SIZE
            p_bin_set = set(p_bin)
            for t in tags:
                if t.price in p_bin_set:
                    slot_id = simple_hash(t.id, seed) % PRECHECK_FRAME_SIZE
                    slot_states[slot_id] += 1
            # --- [End of Physical Layer Simulation] ---
            
            total_tag_bits_sent += PRECHECK_FRAME_SIZE * 1 # 1-bit reply
            
            # --- Reader Behavior: Analyze Frame ---
            if any(count > 0 for count in slot_states):
                active_prices.update(p_bin)

        if not active_prices:
            # Calculate total time and return if no tags are found
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_tag_bits_sent * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, {"reason": "No active prices found"}

        # =================================================================================
        # Stage 2: Parallel Estimation (并行估算)
        # =================================================================================
        # This stage also represents the crossover, adopting the efficient parallel SEM (Slot-based Estimation Method)
        # from both parents to generate an initial count estimate for every potentially active price.
        
        estimated_counts = {}
        sorted_active_prices = sorted(list(active_prices))
        price_batches = [
            sorted_active_prices[i:i + ESTIMATION_BATCH_SIZE] 
            for i in range(0, len(sorted_active_prices), ESTIMATION_BATCH_SIZE)
        ]

        for batch in price_batches:
            if not batch: continue
            
            # --- Reader Behavior: Broadcast Command ---
            total_frames += 1
            num_prices_in_batch = len(batch)
            seed = generate_random_seed()
            price_to_bit_pos_map = {price: i for i, price in enumerate(batch)}
            total_bits_sent_by_reader += (seed.bit_length() + ESTIMATION_FRAME_SIZE.bit_length() + num_prices_in_batch.bit_length())

            # --- [Physical Layer Simulation] ---
            physical_frame = Frame(num_slots=ESTIMATION_FRAME_SIZE, bits_per_slot=num_prices_in_batch)
            batch_price_set = set(batch)
            for t in tags:
                if t.price in batch_price_set:
                    slot_id = simple_hash(t.id, seed) % ESTIMATION_FRAME_SIZE
                    reply_vector = 1 << price_to_bit_pos_map[t.price]
                    physical_frame.aggregate(slot_id, reply_vector)
            # --- [End of Physical Layer Simulation] ---
            
            total_tag_bits_sent += ESTIMATION_FRAME_SIZE * num_prices_in_batch

            # --- Reader Behavior: Analyze Frame ---
            for price, bit_pos in price_to_bit_pos_map.items():
                logical_bit_vector = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                num_zeros = ESTIMATION_FRAME_SIZE - sum(logical_bit_vector)
                
                if num_zeros == ESTIMATION_FRAME_SIZE:
                    estimated_counts[price] = 0
                elif num_zeros == 0:
                    # Assign a high value for full frames, indicating a large count
                    estimated_counts[price] = ESTIMATION_FRAME_SIZE * 2.5 
                else:
                    estimated_counts[price] = -ESTIMATION_FRAME_SIZE * math.log(num_zeros / ESTIMATION_FRAME_SIZE)
        
        # =================================================================================
        # Stage 3: Adaptive Verification with Early Exit (带提前退出的自适应验证)
        # =================================================================================
        # --- Mutation Part: Introducing the "Early Exit based on Verified Maximum" mechanism ---
        # This is a novel optimization not present in either parent protocol. Both parents verify a fixed number of top candidates.
        # This protocol introduces a dynamic termination condition. After verifying the top candidate, its verified count becomes a benchmark.
        # Before verifying the next candidate, the protocol checks if its *initial estimate* is already lower than the current best *verified count*.
        # If it is, there is a negligible chance it will become the new mode, so the entire verification stage terminates early, saving significant time.

        final_counts = {}
        # Filter and sort candidates by their initial estimate in descending order
        candidates = sorted([p for p, e in estimated_counts.items() if e > 0], key=estimated_counts.get, reverse=True)

        max_verified_count = -1.0
        modes = []
        verified_candidates_info = {}

        for price in candidates:
            # **Early Exit Check**
            if estimated_counts[price] < max_verified_count:
                break # Terminate verification early

            # --- Reader Behavior: Broadcast Command ---
            # Crossover Application: Frame size is now adaptive, based on the estimate from Stage 2.
            # This fuses Parent B's concept of adaptive resource allocation with Parent A's concept of a final showdown.
            frame_size = max(MIN_VERIFICATION_FRAME_SIZE, int(estimated_counts[price] * VERIFICATION_FRAME_SCALE_FACTOR))
            
            total_frames += 1
            seed = generate_random_seed()
            total_bits_sent_by_reader += price.bit_length() + frame_size.bit_length() + seed.bit_length()
            
            # --- [Physical Layer Simulation] ---
            slot_states = [0] * frame_size
            for t in tags:
                if t.price == price:
                    slot_id = simple_hash(t.id, seed) % frame_size
                    slot_states[slot_id] += 1
            # --- [End of Physical Layer Simulation] ---
            
            total_tag_bits_sent += frame_size * 1

            # --- Reader Behavior: Refined Estimation ---
            num_zeros = slot_states.count(0)
            verified_count = 0
            if num_zeros == frame_size:
                verified_count = 0
            elif num_zeros == 0:
                verified_count = frame_size * 2.5
            else:
                verified_count = -frame_size * math.log(num_zeros / frame_size)
            
            final_counts[price] = verified_count
            verified_candidates_info[price] = {"initial_estimate": estimated_counts[price], "verification_frame_size": frame_size, "verified_count": verified_count}

            # Update the current best mode(s)
            if verified_count > max_verified_count:
                max_verified_count = verified_count
                modes = [price]
            elif math.isclose(verified_count, max_verified_count):
                modes.append(price)

        # --- Final Processing ---
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_tag_bits_sent * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "active_price_count": len(active_prices),
            "initial_estimates": estimated_counts,
            "candidates_for_verification": [p for p in candidates],
            "verified_candidates_details": verified_candidates_info,
            "final_counts": final_counts,
        }
        
        if not modes:
             return [], 0, total_time_ms, verification_data
        
        # Sort modes for deterministic output
        modes.sort()
        
        return modes, math.ceil(max_verified_count), total_time_ms, verification_data
