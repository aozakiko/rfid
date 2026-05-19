import math
from typing import List, Tuple, Dict, Any

try:
    from base import Tag, IProtocol, generate_random_seed, Frame
except Exception:
    class Tag:
        def __init__(self, id=None, **kwargs):
            self.id = id
            self.__dict__.update(kwargs)

    class IProtocol:
        pass

    def generate_random_seed():
        import random
        return random.randint(1, 2**31 - 1)

    class Frame:
        def __init__(self, num_slots: int, bits_per_slot: int):
            self.num_slots = num_slots
            self.bits_per_slot = bits_per_slot
            self.slots = [0 for _ in range(num_slots)]

        def aggregate(self, slot_id: int, reply_vector: int):
            self.slots[slot_id] |= reply_vector

try:
    from hash import simple_hash
except Exception:
    try:
        from base import simple_hash
    except Exception:
        def simple_hash(value, seed):
            return hash((value, seed)) & 0x7FFFFFFF


class test_g2_3(IProtocol):
    def _get_tag_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _leading_zero_count(self, value: int, bit_len: int) -> int:
        if bit_len <= 0:
            return 0
        count = 0
        for i in range(bit_len - 1, -1, -1):
            if ((value >> i) & 1) == 0:
                count += 1
            else:
                break
        return count

    def _estimate_count_from_zero_bitmap(self, zero_count: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        if zero_count >= frame_size:
            return 0.0
        if zero_count <= 0:
            return float(frame_size) * 3.0
        ratio = zero_count / float(frame_size)
        return -frame_size * math.log(ratio)

    def _estimate_count_from_slot_bits(self, slot_bits: List[int], frame_size: int) -> float:
        occupied = sum(slot_bits)
        zeros = frame_size - occupied
        return self._estimate_count_from_zero_bitmap(zeros, frame_size)

    def _estimate_count_from_sog_or(self, combined_signal: int, bits_per_slot: int, omega: float = 1.2897) -> float:
        if bits_per_slot <= 0:
            return 0.0
        lz = self._leading_zero_count(combined_signal, bits_per_slot)
        return omega * (2 ** lz)

    def _adaptive_frame_size(self, candidate_count: int, stage: str) -> int:
        candidate_count = max(1, candidate_count)
        if stage == "coarse":
            return min(32768, max(256, min(4096, candidate_count * 8)))
        if stage == "refine":
            return min(32768, max(512, min(8192, candidate_count * 24)))
        if stage == "accurate":
            return min(32768, max(1024, min(16384, candidate_count * 128)))
        return min(32768, max(256, candidate_count * 8))

    def _tag_response_sem_batch(self, tag: Tag, batch_price_set: set, price_to_bit_pos: Dict[int, int], seed: int, frame_size: int):
        v = self._get_tag_attr_value(tag)
        if v in batch_price_set:
            slot_id = simple_hash(tag.id, seed) % frame_size
            bit_pos = price_to_bit_pos[v]
            return slot_id, (1 << bit_pos)
        return None

    def _tag_response_sog(self, tag: Tag, seed: int, frame_size: int, bits_per_slot: int):
        v = self._get_tag_attr_value(tag)
        if v is None:
            return None
        rnd = simple_hash(tag.id, seed)
        mu = 0
        found_one = False
        for i in range(bits_per_slot - 1, -1, -1):
            if (rnd >> i) & 1:
                found_one = True
                break
            mu += 1
        if not found_one:
            mu = bits_per_slot
        max_mask_bit = bits_per_slot - 1
        sog = 0 if mu >= bits_per_slot else (1 << (max_mask_bit - mu))
        slot_id = simple_hash(v, seed) % frame_size
        return slot_id, sog

    def _tag_response_single(self, tag: Tag, target_value: int, seed: int, frame_size: int):
        v = self._get_tag_attr_value(tag)
        if v == target_value:
            slot_id = simple_hash(tag.id, seed) % frame_size
            return slot_id, 1
        return None

    def _simulate_sem_batch_frame(self, tags: List[Tag], batch: List[int], frame_size: int, seed: int):
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=len(batch))
        price_to_bit_pos = {p: i for i, p in enumerate(batch)}
        batch_set = set(batch)
        for tag in tags:
            resp = self._tag_response_sem_batch(tag, batch_set, price_to_bit_pos, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame, price_to_bit_pos

    def _simulate_sog_frame(self, tags: List[Tag], seed: int, frame_size: int, bits_per_slot: int):
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        for tag in tags:
            resp = self._tag_response_sog(tag, seed, frame_size, bits_per_slot)
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame

    def _simulate_single_value_frame(self, tags: List[Tag], target_value: int, frame_size: int, seed: int):
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=1)
        for tag in tags:
            resp = self._tag_response_single(tag, target_value, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame

    def _run_parallel_sem_phase(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        batch_size: int,
        value_bit_len: int
    ) -> Tuple[Dict[int, float], Dict[int, int], Dict[str, int]]:
        estimated_counts: Dict[int, float] = {}
        saturation_flags: Dict[int, int] = {}
        frames_used = 0
        reader_bits = 0
        total_slots = 0

        batches = [target_values[i:i + batch_size] for i in range(0, len(target_values), batch_size)]
        for batch in batches:
            if not batch:
                continue
            seed = generate_random_seed()
            frames_used += 1
            reader_bits += seed.bit_length() + frame_size.bit_length() + len(batch).bit_length() + len(batch) * max(1, value_bit_len)
            total_slots += frame_size

            frame, price_to_bit_pos = self._simulate_sem_batch_frame(tags, batch, frame_size, seed)
            for value, bit_pos in price_to_bit_pos.items():
                logical_bits = [((slot_data >> bit_pos) & 1) for slot_data in frame.slots]
                zeros = frame_size - sum(logical_bits)
                saturation_flags[value] = 1 if zeros == 0 else 0
                estimated_counts[value] = self._estimate_count_from_slot_bits(logical_bits, frame_size)

        metrics = {
            "frames_used": frames_used,
            "reader_bits": reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": min(256, max(1, batch_size)),
        }
        return estimated_counts, saturation_flags, metrics

    def _run_sog_refine_phase(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        frame_size: int,
        bits_per_slot: int,
        rounds: int
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, int]], Dict[str, int], List[int]]:
        stats = {v: {"sum_est": 0.0, "rounds": 0, "unique_hits": 0} for v in candidate_values}
        frames_used = 0
        reader_bits = 0
        total_slots = 0
        unique_counts_per_round: List[int] = []

        for _ in range(rounds):
            seed = generate_random_seed()
            frames_used += 1
            reader_bits += seed.bit_length() + frame_size.bit_length() + bits_per_slot.bit_length()
            total_slots += frame_size

            frame = self._simulate_sog_frame(tags, seed, frame_size, bits_per_slot)

            slot_to_candidates: Dict[int, List[int]] = {}
            for v in candidate_values:
                slot_id = simple_hash(v, seed) % frame_size
                slot_to_candidates.setdefault(slot_id, []).append(v)

            unique_hits_round = 0
            for slot_id, values in slot_to_candidates.items():
                if len(values) == 1:
                    unique_hits_round += 1
                    v = values[0]
                    est = self._estimate_count_from_sog_or(frame.slots[slot_id], bits_per_slot)
                    stats[v]["sum_est"] += est
                    stats[v]["rounds"] += 1
                    stats[v]["unique_hits"] += 1
            unique_counts_per_round.append(unique_hits_round)

        estimates: Dict[int, float] = {}
        for v in candidate_values:
            if stats[v]["rounds"] > 0:
                estimates[v] = stats[v]["sum_est"] / stats[v]["rounds"]
            else:
                estimates[v] = 0.0

        metrics = {
            "frames_used": frames_used,
            "reader_bits": reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": bits_per_slot,
        }
        return estimates, stats, metrics, unique_counts_per_round

    def _run_single_value_recheck(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int
    ) -> Tuple[float, int, Dict[str, int]]:
        seed = generate_random_seed()
        reader_bits = seed.bit_length() + frame_size.bit_length() + max(1, abs(int(target_value))).bit_length()
        frame = self._simulate_single_value_frame(tags, target_value, frame_size, seed)
        occupied = frame_size - frame.slots.count(0)
        zeros = frame_size - occupied
        est = self._estimate_count_from_zero_bitmap(zeros, frame_size)
        metrics = {
            "frames_used": 1,
            "reader_bits": reader_bits,
            "total_slots": frame_size,
            "bits_per_slot": 1,
        }
        return est, occupied, metrics

    def _sort_scores_desc(self, d: Dict[int, float]):
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))

    def _select_conservative_modes(
        self,
        scores: Dict[int, float],
        max_candidates: int = 3,
        rel_threshold: float = 0.94
    ) -> List[int]:
        positive = [(k, v) for k, v in scores.items() if v is not None and v > 0]
        if not positive:
            return []
        positive.sort(key=lambda x: (-x[1], x[0]))
        top_score = positive[0][1]
        if top_score <= 0:
            return []
        selected = []
        for value, score in positive:
            if score >= top_score * rel_threshold:
                selected.append(value)
            if len(selected) >= max_candidates:
                break
        if not selected:
            selected = [positive[0][0]]
        return sorted(selected)

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
            }
            return [], 0, 0.0, verification_data

        value_bit_len = max(1, max(abs(price_min), abs(price_max), 1).bit_length() + 1)

        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []
        stage_names: List[str] = []

        coarse_batch_size = min(128, max(16, len(all_values)))
        coarse_bits_per_slot = min(256, coarse_batch_size)
        coarse_frame_size = self._adaptive_frame_size(len(all_values), "coarse")

        # --- 交叉部分：融合父代 A 的分批位图并行估计与父代 B 的逐级候选筛选/自适应帧长思想 ---
        coarse_counts, coarse_saturation, coarse_metrics = self._run_parallel_sem_phase(
            tags=tags,
            target_values=all_values,
            frame_size=coarse_frame_size,
            batch_size=coarse_bits_per_slot,
            value_bit_len=value_bit_len
        )
        total_frames += coarse_metrics["frames_used"]
        total_bits_sent_by_reader += coarse_metrics["reader_bits"]
        total_slots += coarse_metrics["total_slots"]
        for _ in range(coarse_metrics["frames_used"]):
            stage_frame_sizes.append(coarse_frame_size)
            stage_bits_per_slot.append(coarse_bits_per_slot)
            stage_names.append("coarse_sem")

        sorted_coarse = self._sort_scores_desc(coarse_counts)
        candidate_pool_size = min(10, max(4, int(math.sqrt(max(1, len(all_values))))))
        coarse_candidates = [v for v, c in sorted_coarse[:candidate_pool_size] if c > 0]
        if not coarse_candidates and sorted_coarse:
            coarse_candidates = [sorted_coarse[0][0]]

        if not coarse_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            bits_for_time = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
            T_tag = total_slots * bits_for_time * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            total_time_ms = T_reader + T_tag + T_overhead
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "coarse_counts": dict(sorted_coarse[:candidate_pool_size]),
                "coarse_saturation_flags": coarse_saturation,
                "early_stop": True,
            }
            return [], 0, total_time_ms, verification_data

        refine_frame_size = self._adaptive_frame_size(len(coarse_candidates), "refine")
        refine_bits_per_slot = 32
        refine_rounds = 4 if len(coarse_candidates) <= 8 else 5

        sog_estimates, sog_stats, sog_metrics, unique_counts_per_round = self._run_sog_refine_phase(
            tags=tags,
            candidate_values=coarse_candidates,
            frame_size=refine_frame_size,
            bits_per_slot=refine_bits_per_slot,
            rounds=refine_rounds
        )
        total_frames += sog_metrics["frames_used"]
        total_bits_sent_by_reader += sog_metrics["reader_bits"]
        total_slots += sog_metrics["total_slots"]
        for _ in range(sog_metrics["frames_used"]):
            stage_frame_sizes.append(refine_frame_size)
            stage_bits_per_slot.append(refine_bits_per_slot)
            stage_names.append("refine_sog")

        fusion_scores: Dict[int, float] = {}
        for v in coarse_candidates:
            coarse_score = coarse_counts.get(v, 0.0)
            sog_score = sog_estimates.get(v, 0.0)
            unique_hits = sog_stats.get(v, {}).get("unique_hits", 0)
            reliability = min(1.0, unique_hits / max(1.0, refine_rounds * 0.7))
            if sog_score > 0:
                fusion_scores[v] = (0.55 + 0.15 * reliability) * coarse_score + (0.45 - 0.15 * reliability) * sog_score
            else:
                fusion_scores[v] = 0.85 * coarse_score

        ranked_fusion = self._sort_scores_desc(fusion_scores)
        preliminary_final = [v for v, _ in ranked_fusion[:min(4, len(ranked_fusion))]]

        # --- 突变部分：引入“置信差驱动的候选重检 + 动态早停 + 多阶段校验”全新机制 ---
        need_recheck = False
        early_stop = False
        confidence_gap = 1.0
        if len(ranked_fusion) >= 2 and ranked_fusion[0][1] > 0:
            best = ranked_fusion[0][1]
            second = ranked_fusion[1][1]
            confidence_gap = (best - second) / max(best, 1e-9)
            if confidence_gap < 0.12:
                need_recheck = True
        else:
            early_stop = True

        final_counts: Dict[int, float] = {}
        occupied_map: Dict[int, int] = {}
        recheck_targets = preliminary_final[:]

        if need_recheck and recheck_targets:
            accurate_frame_size = self._adaptive_frame_size(len(recheck_targets), "accurate")
            for v in recheck_targets:
                est, occupied, metrics = self._run_single_value_recheck(tags, v, accurate_frame_size)
                total_frames += metrics["frames_used"]
                total_bits_sent_by_reader += metrics["reader_bits"]
                total_slots += metrics["total_slots"]
                stage_frame_sizes.append(accurate_frame_size)
                stage_bits_per_slot.append(1)
                stage_names.append("accurate_single")
                final_counts[v] = est
                occupied_map[v] = occupied

            for v in recheck_targets:
                fusion = fusion_scores.get(v, 0.0)
                recheck = final_counts.get(v, 0.0)
                final_counts[v] = 0.35 * fusion + 0.65 * recheck
        else:
            early_stop = True
            final_counts = dict(fusion_scores)
            accurate_frame_size = refine_frame_size

        estimated_values: List[int] = []
        metric = 0
        if final_counts:
            ordered_final = self._sort_scores_desc(final_counts)
            if len(ordered_final) >= 2 and ordered_final[0][1] > 0:
                best = ordered_final[0][1]
                second = ordered_final[1][1]
                final_gap = (best - second) / max(best, 1e-9)
                if final_gap < 0.06:
                    threshold = best * 0.94
                elif final_gap < 0.15:
                    threshold = best * 0.965
                else:
                    threshold = best * 0.992
            else:
                best = ordered_final[0][1]
                threshold = best * 0.992

            estimated_values = self._select_conservative_modes(
                final_counts,
                max_candidates=3,
                rel_threshold=min(0.995, max(0.92, threshold / max(best, 1e-9))) if best > 0 else 0.95
            )
            if not estimated_values and ordered_final:
                estimated_values = [ordered_final[0][0]]
            metric = int(math.ceil(best)) if best > 0 else 0

        T_reader = total_bits_sent_by_reader * 0.025
        bits_for_time = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
        T_tag = total_slots * bits_for_time * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": stage_frame_sizes[0] if stage_frame_sizes else 0,
            "bits_per_slot": stage_bits_per_slot[0] if stage_bits_per_slot else 0,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "coarse_frame_size": coarse_frame_size,
            "coarse_bits_per_slot": coarse_bits_per_slot,
            "refine_frame_size": refine_frame_size,
            "refine_bits_per_slot": refine_bits_per_slot,
            "accurate_frame_size": accurate_frame_size,
            "coarse_counts": dict(sorted_coarse[:candidate_pool_size]),
            "coarse_candidates": coarse_candidates,
            "coarse_saturation_flags": coarse_saturation,
            "sog_estimates": sog_estimates,
            "sog_stats": sog_stats,
            "fusion_scores": fusion_scores,
            "final_counts": final_counts,
            "occupied_slots_final": occupied_map,
            "unique_counts_per_round": unique_counts_per_round,
            "need_recheck": need_recheck,
            "early_stop": early_stop,
            "confidence_gap": confidence_gap,
        }

        return estimated_values, metric, total_time_ms, verification_data
