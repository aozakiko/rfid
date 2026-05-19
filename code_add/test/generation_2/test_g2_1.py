import math
from typing import List, Tuple, Dict, Any
from collections import defaultdict

from base import Tag, Frame, generate_random_seed, IProtocol
try:
    from hash import simple_hash
except Exception:
    try:
        from base import simple_hash
    except Exception:
        def simple_hash(value, seed):
            return hash((value, seed)) & 0xFFFFFFFF


class test_g2_1(IProtocol):
    """
    面向离散属性众数/多众数查询的混合式 RFID 协议。
    设计目标：
    1) 通过批量位图并行筛选候选；
    2) 通过 SOG/碰撞草图进行多轮精估；
    3) 通过候选重检与置信边界进行保守输出；
    4) 保持协议骨架可扩展到其他统计任务。
    """

    def _safe_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _estimate_count_from_zero(self, zero_count: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        if zero_count >= frame_size:
            return 0.0
        if zero_count <= 0:
            return float(frame_size) * 3.0
        ratio = zero_count / float(frame_size)
        return -frame_size * math.log(ratio)

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

    def _adaptive_frame_size(self, candidate_count: int, scale: int, minimum: int, maximum: int) -> int:
        base = max(minimum, candidate_count * scale)
        power = 1
        while power < base and power < maximum:
            power <<= 1
        return min(maximum, max(minimum, power))

    def _batch_values(self, values: List[int], batch_size: int) -> List[List[int]]:
        if batch_size <= 0:
            return []
        return [values[i:i + batch_size] for i in range(0, len(values), batch_size)]

    def _select_top_with_ties(self, scores: Dict[int, float], limit: int) -> List[int]:
        if not scores or limit <= 0:
            return []
        ordered = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        if limit >= len(ordered):
            return [k for k, _ in ordered]
        threshold = ordered[limit - 1][1]
        selected = [k for k, v in ordered if v >= threshold]
        return selected[:max(limit + 2, limit)]

    def _select_conservative_candidates(
        self,
        scores: Dict[int, float],
        max_candidates: int = 3,
        rel_threshold: float = 0.93,
        abs_gap_ratio: float = 0.08
    ) -> List[int]:
        positive_items = [(k, v) for k, v in scores.items() if v is not None and v > 0]
        if not positive_items:
            return []
        positive_items.sort(key=lambda x: (-x[1], x[0]))
        top_score = positive_items[0][1]
        if top_score <= 0:
            return []

        selected = []
        abs_gap = max(1.0, top_score * abs_gap_ratio)
        for val, score in positive_items:
            if score >= top_score * rel_threshold and (top_score - score) <= abs_gap:
                selected.append(val)
            if len(selected) >= max_candidates:
                break

        if not selected:
            selected = [positive_items[0][0]]
        return sorted(selected)

    def _tag_response_sem_batch(
        self,
        tags: List[Tag],
        batch_values: List[int],
        seed: int,
        frame_size: int
    ) -> Frame:
        value_to_bit = {v: i for i, v in enumerate(batch_values)}
        target_set = set(batch_values)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=len(batch_values))
        for tag in tags:
            value = self._safe_attr_value(tag)
            if value not in target_set:
                continue
            slot_id = simple_hash(tag.id, seed) % frame_size
            bit_pos = value_to_bit[value]
            physical_frame.aggregate(slot_id, 1 << bit_pos)
        return physical_frame

    def _tag_response_value_mapped_sog_batch(
        self,
        tags: List[Tag],
        batch_values: List[int],
        seed: int,
        frame_size: int,
        bits_per_slot: int
    ) -> Frame:
        target_set = set(batch_values)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        for tag in tags:
            value = self._safe_attr_value(tag)
            if value not in target_set:
                continue
            slot_seed = simple_hash(seed, value)
            slot_id = simple_hash(tag.id, slot_seed) % frame_size
            sog_seed = simple_hash(seed, simple_hash(value, 0x9E3779B1))
            sog = self._build_sog_string(tag.id, sog_seed, bits_per_slot)
            physical_frame.aggregate(slot_id, sog)
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

    def _analyze_sem_batch(self, frame: Frame, batch_values: List[int], frame_size: int) -> Dict[int, float]:
        estimates = {}
        for bit_pos, value in enumerate(batch_values):
            ones = 0
            for slot_data in frame.slots:
                ones += (slot_data >> bit_pos) & 1
            zeros = frame_size - ones
            estimates[value] = self._estimate_count_from_zero(zeros, frame_size)
        return estimates

    def _analyze_value_mapped_sog_batch(
        self,
        frame: Frame,
        batch_values: List[int],
        frame_size: int,
        bits_per_slot: int,
        seed: int
    ) -> Dict[int, float]:
        estimates = {}
        slot_to_values = defaultdict(list)
        for value in batch_values:
            slot_seed = simple_hash(seed, value)
            slot_id = simple_hash(value, slot_seed) % frame_size
            slot_to_values[slot_id].append(value)

        for slot_id, values in slot_to_values.items():
            if len(values) == 1:
                value = values[0]
                est = self._estimate_count_from_sog_or(frame.slots[slot_id], bits_per_slot)
                estimates[value] = est

        for value in batch_values:
            if value not in estimates:
                estimates[value] = 0.0
        return estimates

    def _run_sem_stage(
        self,
        tags: List[Tag],
        values: List[int],
        batch_size: int,
        frame_size: int
    ) -> Tuple[Dict[int, float], int, int, int, List[int], List[int]]:
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes = []
        stage_bits_per_slot = []
        estimates = {v: 0.0 for v in values}

        batches = self._batch_values(values, batch_size)
        for batch in batches:
            if not batch:
                continue
            seed = generate_random_seed()
            total_frames += 1
            total_slots += frame_size
            stage_frame_sizes.append(frame_size)
            stage_bits_per_slot.append(len(batch))

            total_reader_bits += (
                seed.bit_length() +
                frame_size.bit_length() +
                len(batch).bit_length() +
                len(batch) * max(1, max(abs(int(x)) for x in batch).bit_length() if batch else 1)
            )

            frame = self._tag_response_sem_batch(tags, batch, seed, frame_size)
            batch_est = self._analyze_sem_batch(frame, batch, frame_size)
            for v, est in batch_est.items():
                estimates[v] = est

        return estimates, total_frames, total_reader_bits, total_slots, stage_frame_sizes, stage_bits_per_slot

    def _run_refine_stage(
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
        stage_frame_sizes = []
        stage_bits_per_slot = []

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

                total_reader_bits += (
                    seed.bit_length() +
                    frame_size.bit_length() +
                    bits_per_slot.bit_length() +
                    len(batch).bit_length()
                )

                frame = self._tag_response_value_mapped_sog_batch(
                    tags=tags,
                    batch_values=batch,
                    seed=seed,
                    frame_size=frame_size,
                    bits_per_slot=bits_per_slot
                )
                batch_est = self._analyze_value_mapped_sog_batch(
                    frame=frame,
                    batch_values=batch,
                    frame_size=frame_size,
                    bits_per_slot=bits_per_slot,
                    seed=seed
                )

                for v in batch:
                    est = batch_est.get(v, 0.0)
                    if est > 0:
                        aggregated[v] += est
                        seen[v] += 1
                        unique_hits[v] += 1

        final_estimates = {}
        for v in candidates:
            if seen[v] > 0:
                base_est = aggregated[v] / seen[v]
                confidence_bonus = 1.0 + min(0.18, 0.03 * unique_hits[v])
                final_estimates[v] = base_est * confidence_bonus
            else:
                final_estimates[v] = 0.0

        return final_estimates, unique_hits, total_frames, total_reader_bits, total_slots, stage_frame_sizes, stage_bits_per_slot

    def _run_verify_stage(
        self,
        tags: List[Tag],
        candidates: List[int],
        frame_size: int,
        bits_per_slot: int,
        rounds: int
    ) -> Tuple[Dict[int, float], List[int], int, int, int, List[int], List[int]]:
        sog_stats = {v: {"sum_est": 0.0, "rounds": 0, "unique_hits": 0} for v in candidates}
        round_unique_counts = []

        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes = []
        stage_bits_per_slot = []

        for _ in range(rounds):
            seed = generate_random_seed()
            total_frames += 1
            total_slots += frame_size
            stage_frame_sizes.append(frame_size)
            stage_bits_per_slot.append(bits_per_slot)

            total_reader_bits += (
                seed.bit_length() +
                frame_size.bit_length() +
                bits_per_slot.bit_length()
            )

            frame = self._tag_response_global_sog(tags, seed, frame_size, bits_per_slot)

            slot_to_candidates = defaultdict(list)
            for v in candidates:
                slot_to_candidates[simple_hash(v, seed) % frame_size].append(v)

            unique_cnt = 0
            for slot_idx, vals in slot_to_candidates.items():
                if len(vals) == 1:
                    unique_cnt += 1
                    v = vals[0]
                    est = self._estimate_count_from_sog_or(frame.slots[slot_idx], bits_per_slot)
                    sog_stats[v]["sum_est"] += est
                    sog_stats[v]["rounds"] += 1
                    sog_stats[v]["unique_hits"] += 1
            round_unique_counts.append(unique_cnt)

        verify_estimates = {}
        for v in candidates:
            if sog_stats[v]["rounds"] > 0:
                base = sog_stats[v]["sum_est"] / sog_stats[v]["rounds"]
                verify_estimates[v] = base * (1.0 + min(0.15, 0.025 * sog_stats[v]["unique_hits"]))
            else:
                verify_estimates[v] = 0.0

        return verify_estimates, round_unique_counts, total_frames, total_reader_bits, total_slots, stage_frame_sizes, stage_bits_per_slot

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        all_values = list(range(price_min, price_max + 1))
        domain_size = len(all_values)

        if domain_size <= 0:
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

        stage_frame_sizes = []
        stage_bits_per_slot = []
        stage_names = []

        # --- 交叉部分：融合父代 A 的“分批位图并行估计 + 自适应帧长 + 候选重检”思想与父代 B 的“SOG/碰撞草图多轮精估 + 唯一映射时隙解析”思想 ---
        stage1_batch_size = min(128, max(8, domain_size if domain_size < 128 else 128))
        stage1_frame_size = self._adaptive_frame_size(stage1_batch_size, 8, 256, 32768)

        sem_estimates, frames, reader_bits, slots, sfs, sbps = self._run_sem_stage(
            tags=tags,
            values=all_values,
            batch_size=stage1_batch_size,
            frame_size=stage1_frame_size
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_slots += slots
        stage_frame_sizes.extend(sfs)
        stage_bits_per_slot.extend(sbps)
        stage_names.extend(["sem_batch"] * len(sfs))

        pre_candidate_limit = min(10, max(3, int(math.sqrt(max(1, domain_size))) + 1))
        pre_candidates = self._select_top_with_ties(sem_estimates, pre_candidate_limit)
        pre_candidates = self._select_conservative_candidates(
            {v: sem_estimates.get(v, 0.0) for v in pre_candidates},
            max_candidates=min(8, pre_candidate_limit),
            rel_threshold=0.87,
            abs_gap_ratio=0.18
        ) if pre_candidates else []

        if not pre_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            avg_bits_per_slot = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
            T_tag = total_slots * avg_bits_per_slot * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
            verification_data = {
                "frame_size": stage1_frame_size,
                "bits_per_slot": stage1_batch_size,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "stage1_sem_estimates": sem_estimates,
                "early_stop": True
            }
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        refine_batch_size = min(32, max(2, len(pre_candidates)))
        refine_bits_per_slot = min(64, max(16, int(math.ceil(math.log2(max(4, len(tags) + 1)))) + 8))
        refine_frame_size = self._adaptive_frame_size(len(pre_candidates), 32, 512, 32768)
        refine_rounds = 2 if len(pre_candidates) <= 4 else 3

        refined_estimates, unique_hits, frames, reader_bits, slots, sfs, sbps = self._run_refine_stage(
            tags=tags,
            candidates=pre_candidates,
            frame_size=refine_frame_size,
            bits_per_slot=refine_bits_per_slot,
            rounds=refine_rounds,
            batch_size=refine_batch_size
        )
        total_frames += frames
        total_bits_sent_by_reader += reader_bits
        total_slots += slots
        stage_frame_sizes.extend(sfs)
        stage_bits_per_slot.extend(sbps)
        stage_names.extend(["sog_refine_batch"] * len(sfs))

        fused_scores = {}
        for v in pre_candidates:
            sem_score = sem_estimates.get(v, 0.0)
            refine_score = refined_estimates.get(v, 0.0)
            if refine_score > 0:
                fused_scores[v] = 0.42 * sem_score + 0.58 * refine_score
            else:
                fused_scores[v] = 0.82 * sem_score

        ranked_fused = sorted(fused_scores.items(), key=lambda x: (-x[1], x[0]))
        top_verify_candidates = [v for v, _ in ranked_fused[:min(5, len(ranked_fused))]]

        # --- 突变部分：引入“动态置信边界早停 + 不确定候选自适应重检”全新机制 ---
        # 若前两名差距足够大则提前终止；否则仅对少量不确定候选做第三阶段重检。
        need_verify = True
        early_stop = False
        if len(ranked_fused) >= 2 and ranked_fused[0][1] > 0:
            top_score = ranked_fused[0][1]
            second_score = ranked_fused[1][1]
            margin_ratio = (top_score - second_score) / max(top_score, 1e-9)
            top_unique = unique_hits.get(ranked_fused[0][0], 0)
            second_unique = unique_hits.get(ranked_fused[1][0], 0)
            if margin_ratio >= 0.16 and top_unique >= max(1, second_unique):
                need_verify = False
                early_stop = True
        elif len(ranked_fused) == 1:
            need_verify = False
            early_stop = True

        verify_estimates = {}
        round_unique_counts = []
        final_scores = dict(fused_scores)

        if need_verify and top_verify_candidates:
            verify_bits_per_slot = min(64, max(16, refine_bits_per_slot))
            verify_frame_size = self._adaptive_frame_size(len(top_verify_candidates), 64, 512, 32768)
            verify_rounds = 2 if len(top_verify_candidates) <= 3 else 3

            verify_estimates, round_unique_counts, frames, reader_bits, slots, sfs, sbps = self._run_verify_stage(
                tags=tags,
                candidates=top_verify_candidates,
                frame_size=verify_frame_size,
                bits_per_slot=verify_bits_per_slot,
                rounds=verify_rounds
            )
            total_frames += frames
            total_bits_sent_by_reader += reader_bits
            total_slots += slots
            stage_frame_sizes.extend(sfs)
            stage_bits_per_slot.extend(sbps)
            stage_names.extend(["sog_verify_global"] * len(sfs))

            for v in top_verify_candidates:
                sem_score = sem_estimates.get(v, 0.0)
                refine_score = refined_estimates.get(v, 0.0)
                verify_score = verify_estimates.get(v, 0.0)
                if verify_score > 0 and refine_score > 0:
                    final_scores[v] = 0.22 * sem_score + 0.33 * refine_score + 0.45 * verify_score
                elif verify_score > 0:
                    final_scores[v] = 0.38 * sem_score + 0.62 * verify_score
                else:
                    final_scores[v] = 0.46 * sem_score + 0.54 * refine_score

        estimated_values = self._select_conservative_candidates(
            final_scores,
            max_candidates=2 if early_stop else 3,
            rel_threshold=0.94 if need_verify else 0.91,
            abs_gap_ratio=0.10 if need_verify else 0.14
        )

        if not estimated_values and ranked_fused:
            estimated_values = [ranked_fused[0][0]]

        metric = 0
        if estimated_values:
            metric = int(math.ceil(max(final_scores.get(v, 0.0) for v in estimated_values)))

        T_reader = total_bits_sent_by_reader * 0.025
        avg_bits_per_slot = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
        T_tag = total_slots * avg_bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": stage_frame_sizes[0] if stage_frame_sizes else stage1_frame_size,
            "bits_per_slot": stage_bits_per_slot[0] if stage_bits_per_slot else stage1_batch_size,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "stage1_sem_estimates": sem_estimates,
            "refined_estimates": refined_estimates,
            "verify_estimates": verify_estimates,
            "final_scores": final_scores,
            "pre_candidates": pre_candidates,
            "top_verify_candidates": top_verify_candidates,
            "refine_unique_hits": dict(unique_hits),
            "verify_round_unique_counts": round_unique_counts,
            "early_stop": early_stop,
            "protocol_genes": {
                "batched_bitmap_parallelism": True,
                "collision_sog_sketch": True,
                "adaptive_frame_size": True,
                "multi_stage_filtering": True,
                "candidate_recheck": need_verify,
                "confidence_early_stop": True
            }
        }

        return list(estimated_values), metric, total_time_ms, verification_data
