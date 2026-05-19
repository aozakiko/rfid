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


class test_g3_2(IProtocol):
    def _safe_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _next_power_of_two(self, x: int, minimum: int = 1, maximum: int = 32768) -> int:
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
            base = max(512, min(8192, candidate_count * 48))
        elif stage == "verify":
            base = max(1024, min(16384, candidate_count * 128))
        else:
            base = max(256, min(4096, candidate_count * 16))
        return self._next_power_of_two(base, minimum=64, maximum=32768)

    def _batch_values(self, values: List[int], batch_size: int) -> List[List[int]]:
        if batch_size <= 0:
            return []
        return [values[i:i + batch_size] for i in range(0, len(values), batch_size)]

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

    def _build_sog_marker(self, tag_id: Any, seed: int, sog_length: int) -> int:
        if sog_length <= 0:
            return 0
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

    def _estimate_count_from_sog_or(self, combined_signal: int, bits_per_slot: int, omega: float = 1.2897) -> float:
        if bits_per_slot <= 0:
            return 0.0
        lz = self._leading_zero_count(combined_signal, bits_per_slot)
        return omega * (2 ** lz)

    def _tag_response_sem_batch(
        self,
        tags: List[Tag],
        batch_values: List[int],
        seed: int,
        frame_size: int
    ) -> Frame:
        value_to_bit = {v: i for i, v in enumerate(batch_values)}
        target_set = set(batch_values)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=max(1, len(batch_values)))
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
            slot_seed = simple_hash(seed, value ^ 0x9E3779B1)
            slot_id = simple_hash(tag.id, slot_seed) % frame_size
            sog_seed = simple_hash(seed, simple_hash(value, 0xA5A5A5A5))
            sog = self._build_sog_marker(tag.id, sog_seed, bits_per_slot)
            physical_frame.aggregate(slot_id, sog)
        return physical_frame

    def _tag_response_single_value_bitmap(
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

    def _analyze_sem_batch(self, frame: Frame, batch_values: List[int], frame_size: int) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]]]:
        estimates: Dict[int, float] = {}
        diagnostics: Dict[int, Dict[str, float]] = {}
        for bit_pos, value in enumerate(batch_values):
            occupied = 0
            for slot_data in frame.slots:
                occupied += (slot_data >> bit_pos) & 1
            zeros = frame_size - occupied
            est = self._estimate_count_from_zero(zeros, frame_size)
            estimates[value] = est
            diagnostics[value] = {
                "occupied": float(occupied),
                "zeros": float(zeros),
                "occupancy_ratio": float(occupied / frame_size) if frame_size > 0 else 0.0,
                "correction": 1.0,
            }
        return estimates, diagnostics

    def _analyze_value_mapped_sog_batch(
        self,
        frame: Frame,
        batch_values: List[int],
        frame_size: int,
        bits_per_slot: int,
        seed: int
    ) -> Tuple[Dict[int, float], Dict[int, int]]:
        estimates: Dict[int, float] = {}
        unique_flags: Dict[int, int] = {}
        slot_to_values = defaultdict(list)

        for value in batch_values:
            slot_seed = simple_hash(seed, value ^ 0x9E3779B1)
            slot_id = simple_hash(value, slot_seed) % frame_size
            slot_to_values[slot_id].append(value)

        for slot_id, values in slot_to_values.items():
            if len(values) == 1:
                value = values[0]
                est = self._estimate_count_from_sog_or(frame.slots[slot_id], bits_per_slot)
                one_bits = bin(frame.slots[slot_id]).count("1") if frame.slots[slot_id] != 0 else 0
                density = one_bits / float(max(1, bits_per_slot))
                density_penalty = 1.0 - min(0.18, 0.35 * density)
                estimates[value] = max(0.0, est * density_penalty)
                unique_flags[value] = 1

        for value in batch_values:
            if value not in estimates:
                estimates[value] = 0.0
                unique_flags[value] = 0

        return estimates, unique_flags

    def _run_sem_stage(
        self,
        tags: List[Tag],
        values: List[int],
        batch_size: int,
        frame_size: int
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]], Dict[str, Any]]:
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []

        aggregated = {v: 0.0 for v in values}
        diagnostics: Dict[int, Dict[str, float]] = {}

        batches = self._batch_values(values, batch_size)
        for batch in batches:
            if not batch:
                continue
            seed = generate_random_seed()
            total_frames += 1
            total_slots += frame_size
            stage_frame_sizes.append(frame_size)
            stage_bits_per_slot.append(min(256, max(1, len(batch))))

            max_value_bits = 1
            for x in batch:
                if isinstance(x, int):
                    max_value_bits = max(max_value_bits, abs(int(x)).bit_length() + 1)
                else:
                    max_value_bits = max(max_value_bits, len(str(x)) * 8)

            total_reader_bits += (
                max(1, seed.bit_length()) +
                max(1, frame_size.bit_length()) +
                max(1, len(batch).bit_length()) +
                len(batch) * max_value_bits
            )

            frame = self._tag_response_sem_batch(tags, batch, seed, frame_size)
            batch_est, batch_diag = self._analyze_sem_batch(frame, batch, frame_size)
            for v in batch:
                aggregated[v] = batch_est.get(v, 0.0)
                diagnostics[v] = batch_diag.get(v, {})

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
        }
        return aggregated, diagnostics, metrics

    def _run_refine_stage(
        self,
        tags: List[Tag],
        candidates: List[int],
        frame_size: int,
        bits_per_slot: int,
        rounds: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], Dict[int, int], Dict[str, Any]]:
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

                total_reader_bits += (
                    max(1, seed.bit_length()) +
                    max(1, frame_size.bit_length()) +
                    max(1, bits_per_slot.bit_length()) +
                    max(1, len(batch).bit_length())
                )

                frame = self._tag_response_value_mapped_sog_batch(
                    tags=tags,
                    batch_values=batch,
                    seed=seed,
                    frame_size=frame_size,
                    bits_per_slot=bits_per_slot
                )
                batch_est, batch_unique = self._analyze_value_mapped_sog_batch(
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
                    unique_hits[v] += batch_unique.get(v, 0)

        final_estimates: Dict[int, float] = {}
        for v in candidates:
            if seen[v] > 0:
                base = aggregated[v] / seen[v]
                reliability = min(1.0, unique_hits[v] / float(max(1, rounds)))
                final_estimates[v] = base * (0.88 + 0.12 * reliability)
            else:
                final_estimates[v] = 0.0

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
        }
        return final_estimates, dict(unique_hits), metrics

    def _run_single_verify_stage(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        frame_size: int,
        rounds: int
    ) -> Tuple[Dict[int, float], Dict[int, int], Dict[str, Any]]:
        estimates: Dict[int, float] = {}
        occupied_map: Dict[int, int] = {}
        variability_map: Dict[int, float] = {}

        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []

        for value in candidate_values:
            est_rounds: List[float] = []
            occ_rounds: List[int] = []
            for _ in range(rounds):
                seed = generate_random_seed()
                total_frames += 1
                total_slots += frame_size
                stage_frame_sizes.append(frame_size)
                stage_bits_per_slot.append(1)

                value_bits = abs(int(value)).bit_length() + 1 if isinstance(value, int) else len(str(value)) * 8
                total_reader_bits += (
                    max(1, seed.bit_length()) +
                    max(1, frame_size.bit_length()) +
                    max(1, value_bits)
                )

                frame = self._tag_response_single_value_bitmap(tags, value, seed, frame_size)
                logical_bits = [1 if slot_data != 0 else 0 for slot_data in frame.slots]
                occ = sum(logical_bits)
                est = self._estimate_count_from_zero(frame_size - occ, frame_size)
                est_rounds.append(est)
                occ_rounds.append(occ)

            if est_rounds:
                mean_est = sum(est_rounds) / len(est_rounds)
                mean_occ = sum(occ_rounds) / len(occ_rounds)
                var = sum((x - mean_est) ** 2 for x in est_rounds) / max(1, len(est_rounds))
                estimates[value] = mean_est
                occupied_map[value] = int(round(mean_occ))
                variability_map[value] = var
            else:
                estimates[value] = 0.0
                occupied_map[value] = 0
                variability_map[value] = 0.0

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "variability_map": variability_map,
        }
        return estimates, occupied_map, metrics

    def _sort_desc(self, d: Dict[int, float]) -> List[Tuple[int, float]]:
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))

    def _select_top_with_ties(self, estimates: Dict[int, float], limit: int) -> List[int]:
        ordered = self._sort_desc(estimates)
        if not ordered or limit <= 0:
            return []
        if len(ordered) <= limit:
            return [v for v, _ in ordered]
        threshold = ordered[limit - 1][1]
        selected = [v for v, s in ordered if s >= threshold]
        return selected[:max(limit + 2, limit)]

    def _confidence_interval_radius(self, est: float, frame_size: int, rounds: int, variance_hint: float = 0.0) -> float:
        if est <= 0:
            return 0.5
        variance_proxy = max(est, variance_hint, 1.0)
        radius = 1.1 * math.sqrt(variance_proxy / max(1, rounds))
        density_penalty = 0.02 * (frame_size / max(64.0, frame_size))
        return radius + density_penalty

    def _select_conservative_modes(
        self,
        scores: Dict[int, float],
        radii: Dict[int, float],
        max_candidates: int = 3,
        relative_guard: float = 0.93
    ) -> List[int]:
        ordered = self._sort_desc(scores)
        if not ordered:
            return []
        best_v, best_s = ordered[0]
        selected = [best_v]
        best_r = radii.get(best_v, 0.5)

        for v, s in ordered[1:]:
            r = radii.get(v, 0.5)
            overlap = (best_s - best_r) <= (s + r)
            close_ratio = s >= best_s * relative_guard if best_s > 0 else False
            if overlap and close_ratio:
                selected.append(v)
            if len(selected) >= max_candidates:
                break
        return sorted(selected[:max_candidates])

    def _neighbor_expand(self, values: List[int], price_min: int, price_max: int, radius: int = 1) -> List[int]:
        expanded = set()
        for v in values:
            for d in range(-radius, radius + 1):
                nv = v + d
                if price_min <= nv <= price_max:
                    expanded.add(nv)
        return sorted(expanded)

    def _score_local_window(
        self,
        coarse_counts: Dict[int, float],
        centers: List[int],
        price_min: int,
        price_max: int,
        radius: int = 2
    ) -> Dict[int, float]:
        result: Dict[int, float] = {}
        for c in centers:
            acc = 0.0
            weight_sum = 0.0
            for d in range(-radius, radius + 1):
                v = c + d
                if price_min <= v <= price_max:
                    w = 1.0 / (1.0 + abs(d))
                    acc += coarse_counts.get(v, 0.0) * w
                    weight_sum += w
            result[c] = acc / weight_sum if weight_sum > 0 else coarse_counts.get(c, 0.0)
        return result

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "total_slots": 0,
            }
            return [], 0, 0.0, verification_data

        domain_size = len(all_values)
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0
        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []
        stage_names: List[str] = []

        # --- 交叉部分：融合父代 A 的“分批位图并行估计 + 自适应帧长 + 候选重检”思想与父代 B 的“逐级候选筛选 + SOG/单值复核”思想 ---
        coarse_batch_size = min(96, max(16, min(domain_size, 96)))
        coarse_frame_size = self._adaptive_frame_size(coarse_batch_size, "coarse")

        coarse_counts, coarse_diag, coarse_metrics = self._run_sem_stage(
            tags=tags,
            values=all_values,
            batch_size=coarse_batch_size,
            frame_size=coarse_frame_size
        )
        total_frames += coarse_metrics["frames_used"]
        total_bits_sent_by_reader += coarse_metrics["reader_bits"]
        total_slots += coarse_metrics["total_slots"]
        stage_frame_sizes.extend(coarse_metrics["stage_frame_sizes"])
        stage_bits_per_slot.extend(coarse_metrics["stage_bits_per_slot"])
        stage_names.extend(["coarse_sem"] * len(coarse_metrics["stage_frame_sizes"]))

        coarse_pool_size = min(10, max(4, int(math.sqrt(max(1, domain_size))) // 2 + 3))
        base_candidates = self._select_top_with_ties(coarse_counts, coarse_pool_size)
        local_scores = self._score_local_window(coarse_counts, base_candidates, price_min, price_max, radius=2)
        ranked_local = self._sort_desc(local_scores)
        anchor_candidates = [v for v, _ in ranked_local[:min(6, len(ranked_local))]]
        coarse_candidates = self._neighbor_expand(anchor_candidates, price_min, price_max, radius=1)

        if not coarse_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            avg_bits_per_slot = max(1, int(round(sum(stage_bits_per_slot) / len(stage_bits_per_slot)))) if stage_bits_per_slot else 1
            T_tag = total_slots * avg_bits_per_slot * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_batch_size,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "total_slots": total_slots,
                "coarse_top": self._sort_desc(coarse_counts)[:10],
                "early_stop": True,
            }
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        refine_batch_size = min(32, max(2, len(coarse_candidates)))
        refine_bits_per_slot = min(48, max(16, int(math.ceil(math.log2(max(4, len(tags) + 1)))) + 6))
        refine_frame_size = self._adaptive_frame_size(len(coarse_candidates), "refine")
        refine_rounds = 3 if len(coarse_candidates) <= 12 else 2

        refined_counts, unique_hits, refine_metrics = self._run_refine_stage(
            tags=tags,
            candidates=coarse_candidates,
            frame_size=refine_frame_size,
            bits_per_slot=refine_bits_per_slot,
            rounds=refine_rounds,
            batch_size=refine_batch_size
        )
        total_frames += refine_metrics["frames_used"]
        total_bits_sent_by_reader += refine_metrics["reader_bits"]
        total_slots += refine_metrics["total_slots"]
        stage_frame_sizes.extend(refine_metrics["stage_frame_sizes"])
        stage_bits_per_slot.extend(refine_metrics["stage_bits_per_slot"])
        stage_names.extend(["refine_sog"] * len(refine_metrics["stage_frame_sizes"]))

        fused_scores: Dict[int, float] = {}
        for v in coarse_candidates:
            c = coarse_counts.get(v, 0.0)
            r = refined_counts.get(v, 0.0)
            if r > 0:
                fused_scores[v] = 0.68 * c + 0.32 * r
            else:
                fused_scores[v] = c

        sorted_fused = self._sort_desc(fused_scores)
        preliminary_verify = [v for v, _ in sorted_fused[:min(6, len(sorted_fused))]]
        preliminary_verify = self._neighbor_expand(preliminary_verify, price_min, price_max, radius=1)
        preliminary_verify = preliminary_verify[:min(10, len(preliminary_verify))]

        # --- 突变部分：引入“邻域感知候选扩张 + 双阶段单值强验证 + 峰值并列保护”全新机制 ---
        early_stop = False
        need_verify = True

        if len(sorted_fused) == 1:
            early_stop = True
            need_verify = False
        elif len(sorted_fused) >= 2:
            top_v, top_s = sorted_fused[0]
            sec_v, sec_s = sorted_fused[1]
            margin_ratio = (top_s - sec_s) / max(top_s, 1e-9) if top_s > 0 else 0.0
            if margin_ratio >= 0.28 and unique_hits.get(top_v, 0) >= unique_hits.get(sec_v, 0) + 2:
                early_stop = True
                need_verify = False

        verify_counts: Dict[int, float] = {}
        occupied_map: Dict[int, int] = {}
        verify_variability_map: Dict[int, float] = {}
        verify_frame_size = self._adaptive_frame_size(max(1, len(preliminary_verify)), "verify")
        verify_rounds = 3
        extra_recheck_used = False
        recheck_counts: Dict[int, float] = {}
        recheck_frame_size = 0
        final_scores: Dict[int, float] = dict(fused_scores)

        if need_verify and preliminary_verify:
            verify_counts, occupied_map, verify_metrics = self._run_single_verify_stage(
                tags=tags,
                candidate_values=preliminary_verify,
                frame_size=verify_frame_size,
                rounds=verify_rounds
            )
            total_frames += verify_metrics["frames_used"]
            total_bits_sent_by_reader += verify_metrics["reader_bits"]
            total_slots += verify_metrics["total_slots"]
            stage_frame_sizes.extend(verify_metrics["stage_frame_sizes"])
            stage_bits_per_slot.extend(verify_metrics["stage_bits_per_slot"])
            stage_names.extend(["verify_single"] * len(verify_metrics["stage_frame_sizes"]))
            verify_variability_map = dict(verify_metrics.get("variability_map", {}))

            verify_radii: Dict[int, float] = {}
            for v in preliminary_verify:
                c = coarse_counts.get(v, 0.0)
                r = refined_counts.get(v, 0.0)
                vv = verify_counts.get(v, 0.0)
                final_scores[v] = 0.10 * c + 0.15 * r + 0.75 * vv
                verify_radii[v] = self._confidence_interval_radius(
                    vv,
                    verify_frame_size,
                    verify_rounds,
                    variance_hint=verify_variability_map.get(v, 0.0)
                )

            ordered_verify = self._sort_desc(verify_counts)
            estimated_values = self._select_conservative_modes(
                final_scores,
                verify_radii,
                max_candidates=3,
                relative_guard=0.965
            )

            abnormal_gap_trigger = False
            if len(ordered_verify) >= 2 and ordered_verify[0][1] > 0:
                top_gap = (ordered_verify[0][1] - ordered_verify[1][1]) / ordered_verify[0][1]
                if top_gap < 0.12:
                    abnormal_gap_trigger = True

            if abnormal_gap_trigger:
                extra_recheck_used = True
                re_candidates = [v for v, _ in ordered_verify[:min(4, len(ordered_verify))]]
                recheck_frame_size = self._next_power_of_two(
                    max(verify_frame_size, self._adaptive_frame_size(len(re_candidates) * 2, "verify")),
                    minimum=64,
                    maximum=32768
                )
                recheck_counts, recheck_occ_map, re_metrics = self._run_single_verify_stage(
                    tags=tags,
                    candidate_values=re_candidates,
                    frame_size=recheck_frame_size,
                    rounds=2
                )
                total_frames += re_metrics["frames_used"]
                total_bits_sent_by_reader += re_metrics["reader_bits"]
                total_slots += re_metrics["total_slots"]
                stage_frame_sizes.extend(re_metrics["stage_frame_sizes"])
                stage_bits_per_slot.extend(re_metrics["stage_bits_per_slot"])
                stage_names.extend(["recheck_single"] * len(re_metrics["stage_frame_sizes"]))
                occupied_map.update(recheck_occ_map)

                merged_verify: Dict[int, float] = {}
                for v in re_candidates:
                    base = verify_counts.get(v, 0.0)
                    extra = recheck_counts.get(v, 0.0)
                    merged_verify[v] = (base * verify_rounds + extra * 2) / (verify_rounds + 2)
                verify_counts.update(merged_verify)

                verify_radii = {}
                for v in preliminary_verify:
                    c = coarse_counts.get(v, 0.0)
                    r = refined_counts.get(v, 0.0)
                    vv = verify_counts.get(v, 0.0)
                    final_scores[v] = 0.08 * c + 0.12 * r + 0.80 * vv
                    verify_radii[v] = self._confidence_interval_radius(
                        vv,
                        recheck_frame_size if recheck_frame_size > 0 else verify_frame_size,
                        verify_rounds + 2,
                        variance_hint=verify_variability_map.get(v, 0.0)
                    )

                estimated_values = self._select_conservative_modes(
                    final_scores,
                    verify_radii,
                    max_candidates=3,
                    relative_guard=0.97
                )
        else:
            verify_radii = {}
            for v in preliminary_verify:
                verify_radii[v] = self._confidence_interval_radius(
                    final_scores.get(v, 0.0),
                    refine_frame_size,
                    max(1, refine_rounds),
                    variance_hint=max(1.0, refined_counts.get(v, 0.0))
                )
            estimated_values = self._select_conservative_modes(
                final_scores,
                verify_radii,
                max_candidates=2,
                relative_guard=0.95
            )

        if not estimated_values:
            verify_sorted = self._sort_desc(verify_counts) if verify_counts else []
            if verify_sorted:
                top_score = verify_sorted[0][1]
                estimated_values = [v for v, s in verify_sorted if s >= top_score * 0.97][:2]
            elif sorted_fused:
                estimated_values = [sorted_fused[0][0]]

        estimated_values = sorted(list(dict.fromkeys(estimated_values)))[:3]

        metric_score = 0.0
        if estimated_values:
            score_source = verify_counts if verify_counts else final_scores
            metric_score = max(score_source.get(v, final_scores.get(v, 0.0)) for v in estimated_values)

        avg_bits_per_slot = max(1, int(round(sum(stage_bits_per_slot) / len(stage_bits_per_slot)))) if stage_bits_per_slot else 1
        T_reader = total_bits_sent_by_reader * 0.025
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
            "total_slots": total_slots,
            "coarse_frame_size": coarse_frame_size,
            "refine_frame_size": refine_frame_size,
            "verify_frame_size": verify_frame_size,
            "coarse_bits_per_slot": coarse_batch_size,
            "refine_bits_per_slot": refine_bits_per_slot,
            "verify_bits_per_slot": 1 if need_verify else 0,
            "coarse_candidate_pool_size": coarse_pool_size,
            "base_candidates": base_candidates,
            "anchor_candidates": anchor_candidates,
            "coarse_candidates": coarse_candidates,
            "coarse_top": self._sort_desc(coarse_counts)[:10],
            "refined_top": self._sort_desc(refined_counts)[:10],
            "preliminary_verify": preliminary_verify,
            "verify_counts": dict(self._sort_desc(verify_counts)) if verify_counts else {},
            "occupied_slots_final": occupied_map,
            "coarse_diagnostics": {k: coarse_diag[k] for k in base_candidates[:min(6, len(base_candidates))] if k in coarse_diag},
            "refine_unique_hits": unique_hits,
            "verify_variability_map": verify_variability_map,
            "recheck_counts": recheck_counts,
            "extra_recheck_used": extra_recheck_used,
            "recheck_frame_size": recheck_frame_size,
            "early_stop": early_stop,
            "need_verify": need_verify,
            "final_scores": final_scores,
            "protocol_genes": {
                "batched_bitmap_parallelism": True,
                "collision_sog_sketch": True,
                "adaptive_frame_size": True,
                "multi_stage_filtering": True,
                "single_value_verification": True,
                "confidence_interval_correction": True,
                "stability_driven_early_stop": True,
                "candidate_recheck": extra_recheck_used,
                "neighbor_aware_candidate_expansion": True,
                "peak_tie_protection": True,
            }
        }

        metric = int(math.ceil(metric_score)) if metric_score > 0 else 0
        return list(estimated_values), metric, total_time_ms, verification_data
