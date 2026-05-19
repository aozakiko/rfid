import math
from typing import List, Tuple, Dict, Any
from collections import defaultdict

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


class test_g3_1(IProtocol):
    def _safe_attr_value(self, tag: Tag) -> Any:
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

    def _estimate_count_from_zero(self, zero_count: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        if zero_count >= frame_size:
            return 0.0
        if zero_count <= 0:
            return float(frame_size) * 3.0
        ratio = zero_count / float(frame_size)
        return -frame_size * math.log(ratio)

    def _estimate_count_from_sog_or(self, combined_signal: int, bits_per_slot: int, omega: float = 1.2897) -> float:
        if bits_per_slot <= 0:
            return 0.0
        lz = self._leading_zero_count(combined_signal, bits_per_slot)
        return omega * (2 ** lz)

    def _build_sog_string(self, tag_id: Any, seed: int, sog_length: int) -> int:
        rnd = simple_hash(tag_id, seed)
        mu = 0
        found_one = False
        for i in range(sog_length - 1, -1, -1):
            if (rnd >> i) & 1:
                found_one = True
                break
            mu += 1
        if not found_one:
            mu = sog_length
        if mu >= sog_length:
            return 0
        return 1 << (sog_length - 1 - mu)

    def _next_power_two_capped(self, x: int, minimum: int, maximum: int) -> int:
        x = max(minimum, x)
        p = 1
        while p < x and p < maximum:
            p <<= 1
        return min(maximum, max(minimum, p))

    def _adaptive_frame_size(self, candidate_count: int, stage: str) -> int:
        candidate_count = max(1, candidate_count)
        if stage == "coarse":
            base = max(256, min(4096, candidate_count * 8))
        elif stage == "refine":
            base = max(512, min(8192, candidate_count * 24))
        elif stage == "verify":
            base = max(512, min(16384, candidate_count * 64))
        elif stage == "single":
            base = max(1024, min(16384, candidate_count * 128))
        else:
            base = max(256, min(32768, candidate_count * 8))
        return self._next_power_two_capped(base, 256 if stage == "coarse" else 512, 32768)

    def _batch_values(self, values: List[int], batch_size: int) -> List[List[int]]:
        if batch_size <= 0:
            return []
        return [values[i:i + batch_size] for i in range(0, len(values), batch_size)]

    def _sort_scores_desc(self, scores: Dict[int, float]) -> List[Tuple[int, float]]:
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))

    def _select_top_with_ties(self, scores: Dict[int, float], limit: int) -> List[int]:
        if not scores or limit <= 0:
            return []
        ordered = self._sort_scores_desc(scores)
        if limit >= len(ordered):
            return [k for k, _ in ordered]
        threshold = ordered[limit - 1][1]
        selected = [k for k, v in ordered if v >= threshold]
        return selected[:max(limit + 2, limit)]

    def _select_conservative_candidates(
        self,
        scores: Dict[int, float],
        max_candidates: int = 3,
        rel_threshold: float = 0.94,
        abs_gap_ratio: float = 0.08
    ) -> List[int]:
        positive = [(k, v) for k, v in scores.items() if v is not None and v > 0]
        if not positive:
            return []
        positive.sort(key=lambda x: (-x[1], x[0]))
        top_score = positive[0][1]
        if top_score <= 0:
            return []
        selected = []
        abs_gap = max(1.0, top_score * abs_gap_ratio)
        for value, score in positive:
            if score >= top_score * rel_threshold and (top_score - score) <= abs_gap:
                selected.append(value)
            if len(selected) >= max_candidates:
                break
        if not selected:
            selected = [positive[0][0]]
        return sorted(selected)

    def _tag_response_sem_batch(
        self,
        tags: List[Tag],
        batch_values: List[int],
        seed: int,
        frame_size: int
    ) -> Frame:
        value_to_bit = {v: i for i, v in enumerate(batch_values)}
        batch_set = set(batch_values)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=len(batch_values))
        for tag in tags:
            value = self._safe_attr_value(tag)
            if value not in batch_set:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            physical_frame.aggregate(slot_id, 1 << value_to_bit[value])
        return physical_frame

    def _tag_response_global_sog(
        self,
        tags: List[Tag],
        seed: int,
        frame_size: int,
        bits_per_slot: int
    ) -> Frame:
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        for tag in tags:
            value = self._safe_attr_value(tag)
            if value is None:
                continue
            sog = self._build_sog_string(tag.id, seed, bits_per_slot)
            slot_id = simple_hash(value, seed) % frame_size
            physical_frame.aggregate(slot_id, sog)
        return physical_frame

    def _tag_response_value_mapped_sog_batch(
        self,
        tags: List[Tag],
        batch_values: List[int],
        seed: int,
        frame_size: int,
        bits_per_slot: int
    ) -> Frame:
        batch_set = set(batch_values)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        for tag in tags:
            value = self._safe_attr_value(tag)
            if value not in batch_set:
                continue
            slot_seed = simple_hash(seed, value)
            slot_id = simple_hash(tag.id, slot_seed) % frame_size
            sog_seed = simple_hash(seed, simple_hash(value, 0x9E3779B1))
            sog = self._build_sog_string(tag.id, sog_seed, bits_per_slot)
            physical_frame.aggregate(slot_id, sog)
        return physical_frame

    def _tag_response_single_value(
        self,
        tags: List[Tag],
        target_value: int,
        seed: int,
        frame_size: int
    ) -> Frame:
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=1)
        for tag in tags:
            value = self._safe_attr_value(tag)
            if value != target_value:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            physical_frame.aggregate(slot_id, 1)
        return physical_frame

    def _analyze_sem_batch(self, frame: Frame, batch_values: List[int], frame_size: int) -> Dict[int, float]:
        estimates = {}
        for bit_pos, value in enumerate(batch_values):
            occupied = 0
            for slot_data in frame.slots:
                occupied += (slot_data >> bit_pos) & 1
            zeros = frame_size - occupied
            estimates[value] = self._estimate_count_from_zero(zeros, frame_size)
        return estimates

    def _analyze_global_sog(
        self,
        frame: Frame,
        candidates: List[int],
        frame_size: int,
        bits_per_slot: int,
        seed: int
    ) -> Tuple[Dict[int, float], Dict[int, int]]:
        estimates: Dict[int, float] = {v: 0.0 for v in candidates}
        unique_map: Dict[int, int] = {v: 0 for v in candidates}
        slot_to_values = defaultdict(list)
        for value in candidates:
            slot_id = simple_hash(value, seed) % frame_size
            slot_to_values[slot_id].append(value)
        for slot_id, values in slot_to_values.items():
            if len(values) == 1:
                value = values[0]
                estimates[value] = self._estimate_count_from_sog_or(frame.slots[slot_id], bits_per_slot)
                unique_map[value] = 1
        return estimates, unique_map

    def _analyze_value_mapped_sog_batch(
        self,
        frame: Frame,
        batch_values: List[int],
        frame_size: int,
        bits_per_slot: int,
        seed: int
    ) -> Tuple[Dict[int, float], Dict[int, int]]:
        estimates: Dict[int, float] = {v: 0.0 for v in batch_values}
        unique_map: Dict[int, int] = {v: 0 for v in batch_values}
        slot_to_values = defaultdict(list)
        for value in batch_values:
            slot_seed = simple_hash(seed, value)
            slot_id = simple_hash(value, slot_seed) % frame_size
            slot_to_values[slot_id].append(value)
        for slot_id, values in slot_to_values.items():
            if len(values) == 1:
                value = values[0]
                estimates[value] = self._estimate_count_from_sog_or(frame.slots[slot_id], bits_per_slot)
                unique_map[value] = 1
        return estimates, unique_map

    def _run_sem_stage(
        self,
        tags: List[Tag],
        values: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], Dict[int, int], int, int, int, List[int], List[int]]:
        estimates: Dict[int, float] = {v: 0.0 for v in values}
        saturation_flags: Dict[int, int] = {v: 0 for v in values}
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []

        value_bit_len = max(1, max(abs(int(values[0])) if values else 1, abs(int(values[-1])) if values else 1, 1).bit_length() + 1)
        batches = self._batch_values(values, batch_size)
        for batch in batches:
            if not batch:
                continue
            seed = generate_random_seed()
            total_frames += 1
            total_slots += frame_size
            stage_frame_sizes.append(frame_size)
            stage_bits_per_slot.append(len(batch))
            total_reader_bits += seed.bit_length() + frame_size.bit_length() + len(batch).bit_length() + len(batch) * value_bit_len
            frame = self._tag_response_sem_batch(tags, batch, seed, frame_size)
            batch_est = self._analyze_sem_batch(frame, batch, frame_size)
            for bit_pos, value in enumerate(batch):
                occupied = 0
                for slot_data in frame.slots:
                    occupied += (slot_data >> bit_pos) & 1
                zeros = frame_size - occupied
                estimates[value] = batch_est.get(value, 0.0)
                saturation_flags[value] = 1 if zeros == 0 else 0

        return estimates, saturation_flags, total_frames, total_reader_bits, total_slots, stage_frame_sizes, stage_bits_per_slot

    def _run_global_sog_stage(
        self,
        tags: List[Tag],
        candidates: List[int],
        frame_size: int,
        bits_per_slot: int,
        rounds: int
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]], int, int, int, List[int], List[int], List[int]]:
        stats = {v: {"sum_est": 0.0, "rounds": 0, "unique_hits": 0} for v in candidates}
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []
        unique_counts_per_round: List[int] = []

        for _ in range(rounds):
            seed = generate_random_seed()
            total_frames += 1
            total_slots += frame_size
            stage_frame_sizes.append(frame_size)
            stage_bits_per_slot.append(bits_per_slot)
            total_reader_bits += seed.bit_length() + frame_size.bit_length() + bits_per_slot.bit_length()

            frame = self._tag_response_global_sog(tags, seed, frame_size, bits_per_slot)
            estimates, unique_map = self._analyze_global_sog(frame, candidates, frame_size, bits_per_slot, seed)

            unique_cnt = 0
            for v in candidates:
                if unique_map.get(v, 0) == 1:
                    unique_cnt += 1
                    stats[v]["sum_est"] += estimates.get(v, 0.0)
                    stats[v]["rounds"] += 1
                    stats[v]["unique_hits"] += 1
            unique_counts_per_round.append(unique_cnt)

        final_estimates = {}
        for v in candidates:
            if stats[v]["rounds"] > 0:
                base = stats[v]["sum_est"] / stats[v]["rounds"]
                bonus = 1.0 + min(0.12, 0.02 * stats[v]["unique_hits"])
                final_estimates[v] = base * bonus
            else:
                final_estimates[v] = 0.0

        return final_estimates, stats, total_frames, total_reader_bits, total_slots, stage_frame_sizes, stage_bits_per_slot, unique_counts_per_round

    def _run_value_mapped_sog_verify(
        self,
        tags: List[Tag],
        candidates: List[int],
        frame_size: int,
        bits_per_slot: int,
        rounds: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], Dict[int, int], int, int, int, List[int], List[int]]:
        aggregated = defaultdict(float)
        seen = defaultdict(int)
        unique_hits = defaultdict(int)

        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []

        batches = self._batch_values(candidates, batch_size)
        for _ in range(rounds):
            for batch in batches:
                if not batch:
                    continue
                seed = generate_random_seed()
                total_frames += 1
                total_slots += frame_size
                stage_frame_sizes.append(frame_size)
                stage_bits_per_slot.append(bits_per_slot)
                total_reader_bits += seed.bit_length() + frame_size.bit_length() + bits_per_slot.bit_length() + len(batch).bit_length()

                frame = self._tag_response_value_mapped_sog_batch(tags, batch, seed, frame_size, bits_per_slot)
                est_map, uniq_map = self._analyze_value_mapped_sog_batch(frame, batch, frame_size, bits_per_slot, seed)

                for v in batch:
                    if uniq_map.get(v, 0) == 1 and est_map.get(v, 0.0) > 0:
                        aggregated[v] += est_map[v]
                        seen[v] += 1
                        unique_hits[v] += 1

        verify_estimates = {}
        for v in candidates:
            if seen[v] > 0:
                verify_estimates[v] = (aggregated[v] / seen[v]) * (1.0 + min(0.15, 0.025 * unique_hits[v]))
            else:
                verify_estimates[v] = 0.0

        return verify_estimates, dict(unique_hits), total_frames, total_reader_bits, total_slots, stage_frame_sizes, stage_bits_per_slot

    def _run_single_value_recheck(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int
    ) -> Tuple[float, int, int, int, int]:
        seed = generate_random_seed()
        reader_bits = seed.bit_length() + frame_size.bit_length() + max(1, abs(int(target_value)).bit_length() + 1)
        frame = self._tag_response_single_value(tags, target_value, seed, frame_size)
        occupied = frame_size - frame.slots.count(0)
        zeros = frame_size - occupied
        est = self._estimate_count_from_zero(zeros, frame_size)
        return est, 1, reader_bits, frame_size, occupied

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0
            }
            return [], 0, 0.0, verification_data

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []
        stage_names: List[str] = []

        domain_size = len(all_values)
        coarse_batch_size = min(128, max(16, domain_size if domain_size < 128 else 128))
        coarse_frame_size = self._adaptive_frame_size(domain_size, "coarse")

        # --- 交叉部分：融合父代 A 的“分批位图并行估计 + 候选重检 + 候选保守输出”思想与父代 B 的“逐级候选筛选 + 全局 SOG 碰撞草图精估 + 自适应帧长”思想 ---
        coarse_counts, coarse_saturation, frames, reader_bits, slots, sfs, sbps = self._run_sem_stage(
            tags=tags,
            values=all_values,
            frame_size=coarse_frame_size,
            batch_size=coarse_batch_size
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_slots += slots
        stage_frame_sizes.extend(sfs)
        stage_bits_per_slot.extend(sbps)
        stage_names.extend(["coarse_sem"] * len(sfs))

        coarse_limit = min(12, max(4, int(math.sqrt(max(1, domain_size))) + 1))
        coarse_top = self._select_top_with_ties(coarse_counts, coarse_limit)
        coarse_candidates = self._select_conservative_candidates(
            {v: coarse_counts.get(v, 0.0) for v in coarse_top},
            max_candidates=min(8, coarse_limit),
            rel_threshold=0.86,
            abs_gap_ratio=0.20
        ) if coarse_top else []

        if not coarse_candidates and coarse_top:
            coarse_candidates = [coarse_top[0]]

        if not coarse_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            avg_bits_per_slot = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
            T_tag = total_slots * avg_bits_per_slot * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_batch_size,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "coarse_counts": coarse_counts,
                "coarse_saturation_flags": coarse_saturation,
                "early_stop": True
            }
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        refine_frame_size = self._adaptive_frame_size(len(coarse_candidates), "refine")
        refine_bits_per_slot = min(64, max(16, int(math.ceil(math.log2(max(4, len(tags) + 1)))) + 8))
        refine_rounds = 3 if len(coarse_candidates) <= 4 else 4

        sog_estimates, sog_stats, frames, reader_bits, slots, sfs, sbps, unique_counts_per_round = self._run_global_sog_stage(
            tags=tags,
            candidates=coarse_candidates,
            frame_size=refine_frame_size,
            bits_per_slot=refine_bits_per_slot,
            rounds=refine_rounds
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_slots += slots
        stage_frame_sizes.extend(sfs)
        stage_bits_per_slot.extend(sbps)
        stage_names.extend(["refine_global_sog"] * len(sfs))

        fusion_scores: Dict[int, float] = {}
        for v in coarse_candidates:
            coarse_score = coarse_counts.get(v, 0.0)
            sog_score = sog_estimates.get(v, 0.0)
            unique_hits = int(sog_stats.get(v, {}).get("unique_hits", 0))
            reliability = min(1.0, unique_hits / max(1.0, refine_rounds))
            saturation_penalty = 0.88 if coarse_saturation.get(v, 0) == 1 else 1.0
            if sog_score > 0:
                fusion_scores[v] = saturation_penalty * ((0.52 + 0.12 * reliability) * coarse_score + (0.48 - 0.12 * reliability) * sog_score)
            else:
                fusion_scores[v] = saturation_penalty * (0.84 * coarse_score)

        ranked_fusion = self._sort_scores_desc(fusion_scores)
        prelim_candidates = [v for v, _ in ranked_fusion[:min(5, len(ranked_fusion))]]

        # --- 突变部分：引入“二次不确定性分流验证”全新机制 ---
        # 先依据融合分数与唯一命中可靠度判断是否早停；若不确定，则只对少量可疑候选做值映射 SOG 重检；
        # 若仍近似双峰，再进行单值位图终检，以兼顾双峰保守输出与通信开销控制。
        early_stop = False
        need_batch_verify = False
        need_single_recheck = False
        confidence_gap = 1.0

        if len(ranked_fusion) >= 2 and ranked_fusion[0][1] > 0:
            best = ranked_fusion[0][1]
            second = ranked_fusion[1][1]
            confidence_gap = (best - second) / max(best, 1e-9)
            best_u = int(sog_stats.get(ranked_fusion[0][0], {}).get("unique_hits", 0))
            second_u = int(sog_stats.get(ranked_fusion[1][0], {}).get("unique_hits", 0))
            if confidence_gap >= 0.18 and best_u >= second_u:
                early_stop = True
            else:
                need_batch_verify = True
        elif len(ranked_fusion) == 1:
            early_stop = True
        else:
            need_batch_verify = False
            early_stop = True

        verify_estimates: Dict[int, float] = {}
        verify_unique_hits: Dict[int, int] = {}
        final_scores: Dict[int, float] = dict(fusion_scores)
        batch_verify_candidates = prelim_candidates[:]

        if need_batch_verify and batch_verify_candidates:
            verify_frame_size = self._adaptive_frame_size(len(batch_verify_candidates), "verify")
            verify_bits_per_slot = min(64, max(16, refine_bits_per_slot))
            verify_rounds = 2 if len(batch_verify_candidates) <= 3 else 3
            verify_batch_size = min(32, max(2, len(batch_verify_candidates)))

            verify_estimates, verify_unique_hits, frames, reader_bits, slots, sfs, sbps = self._run_value_mapped_sog_verify(
                tags=tags,
                candidates=batch_verify_candidates,
                frame_size=verify_frame_size,
                bits_per_slot=verify_bits_per_slot,
                rounds=verify_rounds,
                batch_size=verify_batch_size
            )
            total_frames += frames
            total_bits_sent_by_reader += reader_bits
            total_slots += slots
            stage_frame_sizes.extend(sfs)
            stage_bits_per_slot.extend(sbps)
            stage_names.extend(["verify_value_mapped_sog"] * len(sfs))

            for v in batch_verify_candidates:
                coarse_score = coarse_counts.get(v, 0.0)
                refine_score = sog_estimates.get(v, 0.0)
                verify_score = verify_estimates.get(v, 0.0)
                if verify_score > 0 and refine_score > 0:
                    final_scores[v] = 0.25 * coarse_score + 0.30 * refine_score + 0.45 * verify_score
                elif verify_score > 0:
                    final_scores[v] = 0.35 * coarse_score + 0.65 * verify_score
                else:
                    final_scores[v] = 0.50 * coarse_score + 0.50 * max(refine_score, 0.0)

            ordered_after_verify = self._sort_scores_desc(final_scores)
            if len(ordered_after_verify) >= 2 and ordered_after_verify[0][1] > 0:
                best2 = ordered_after_verify[0][1]
                second2 = ordered_after_verify[1][1]
                gap2 = (best2 - second2) / max(best2, 1e-9)
                if gap2 < 0.09:
                    need_single_recheck = True
            elif len(ordered_after_verify) == 1:
                need_single_recheck = False

        single_recheck_estimates: Dict[int, float] = {}
        occupied_slots_final: Dict[int, int] = {}
        single_recheck_targets: List[int] = []

        if need_single_recheck:
            ordered_after_verify = self._sort_scores_desc(final_scores)
            top_score = ordered_after_verify[0][1] if ordered_after_verify else 0.0
            for v, s in ordered_after_verify[:min(3, len(ordered_after_verify))]:
                if top_score > 0 and s >= top_score * 0.90:
                    single_recheck_targets.append(v)
            if not single_recheck_targets and ordered_after_verify:
                single_recheck_targets = [ordered_after_verify[0][0]]

            single_frame_size = self._adaptive_frame_size(len(single_recheck_targets), "single")
            for v in single_recheck_targets:
                est, frames, reader_bits, slots, occupied = self._run_single_value_recheck(tags, v, single_frame_size)
                total_frames += frames
                total_bits_sent_by_reader += reader_bits
                total_slots += slots
                stage_frame_sizes.append(single_frame_size)
                stage_bits_per_slot.append(1)
                stage_names.append("single_value_recheck")
                single_recheck_estimates[v] = est
                occupied_slots_final[v] = occupied

            for v in single_recheck_targets:
                prior = final_scores.get(v, 0.0)
                single_est = single_recheck_estimates.get(v, 0.0)
                final_scores[v] = 0.40 * prior + 0.60 * single_est

        ordered_final = self._sort_scores_desc(final_scores)
        if ordered_final:
            best = ordered_final[0][1]
        else:
            best = 0.0

        if len(ordered_final) >= 2 and best > 0:
            second = ordered_final[1][1]
            final_gap = (best - second) / max(best, 1e-9)
            if final_gap < 0.05:
                rel_threshold = 0.93
                abs_gap_ratio = 0.12
                max_candidates = 3
            elif final_gap < 0.14:
                rel_threshold = 0.955
                abs_gap_ratio = 0.09
                max_candidates = 2
            else:
                rel_threshold = 0.985
                abs_gap_ratio = 0.04
                max_candidates = 2
        else:
            rel_threshold = 0.985
            abs_gap_ratio = 0.04
            max_candidates = 1

        estimated_values = self._select_conservative_candidates(
            final_scores,
            max_candidates=max_candidates,
            rel_threshold=rel_threshold,
            abs_gap_ratio=abs_gap_ratio
        )

        if not estimated_values and ordered_final:
            estimated_values = [ordered_final[0][0]]

        metric = int(math.ceil(best)) if best > 0 else 0

        T_reader = total_bits_sent_by_reader * 0.025
        avg_bits_per_slot = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
        T_tag = total_slots * avg_bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": stage_frame_sizes[0] if stage_frame_sizes else coarse_frame_size,
            "bits_per_slot": stage_bits_per_slot[0] if stage_bits_per_slot else coarse_batch_size,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "coarse_frame_size": coarse_frame_size,
            "coarse_bits_per_slot": coarse_batch_size,
            "refine_frame_size": refine_frame_size,
            "refine_bits_per_slot": refine_bits_per_slot,
            "coarse_counts": coarse_counts,
            "coarse_saturation_flags": coarse_saturation,
            "coarse_candidates": coarse_candidates,
            "sog_estimates": sog_estimates,
            "sog_stats": sog_stats,
            "fusion_scores": fusion_scores,
            "verify_estimates": verify_estimates,
            "verify_unique_hits": verify_unique_hits,
            "single_recheck_estimates": single_recheck_estimates,
            "occupied_slots_final": occupied_slots_final,
            "final_scores": final_scores,
            "unique_counts_per_round": unique_counts_per_round,
            "confidence_gap": confidence_gap,
            "need_batch_verify": need_batch_verify,
            "need_single_recheck": need_single_recheck,
            "early_stop": early_stop,
            "protocol_genes": {
                "batched_bitmap_parallelism": True,
                "collision_sog_sketch": True,
                "adaptive_frame_size": True,
                "multi_stage_filtering": True,
                "candidate_recheck": True,
                "value_mapped_verification": True,
                "uncertainty_routed_verification": True
            }
        }

        return list(estimated_values), metric, total_time_ms, verification_data
