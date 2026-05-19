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


class test_g3_3(IProtocol):
    def _get_tag_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _pow2_ceiling(self, x: int) -> int:
        x = max(1, int(x))
        p = 1
        while p < x and p < 32768:
            p <<= 1
        return min(32768, max(1, p))

    def _adaptive_frame_size(self, candidate_count: int, stage: str) -> int:
        candidate_count = max(1, candidate_count)
        if stage == "coarse":
            base = max(128, min(4096, candidate_count * 4))
        elif stage == "refine":
            base = max(256, min(8192, candidate_count * 24))
        elif stage == "verify":
            base = max(512, min(16384, candidate_count * 80))
        else:
            base = max(128, min(4096, candidate_count * 8))
        return self._pow2_ceiling(base)

    def _estimate_from_zero_bitmap(self, zeros: int, frame_size: int) -> float:
        if frame_size <= 0:
            return 0.0
        if zeros >= frame_size:
            return 0.0
        if zeros <= 0:
            return float(frame_size) * 2.5
        ratio = zeros / float(frame_size)
        return -frame_size * math.log(max(ratio, 1e-12))

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

    def _estimate_from_sog_or(self, combined_signal: int, bits_per_slot: int) -> float:
        if bits_per_slot <= 0:
            return 0.0
        lz = self._leading_zero_count(combined_signal, bits_per_slot)
        omega = 1.2897
        return omega * (2 ** min(bits_per_slot, lz))

    def _estimate_with_density_correction(
        self,
        logical_bits: List[int],
        frame_size: int,
        use_sog: bool = False
    ) -> Tuple[float, Dict[str, float]]:
        occupied = sum(logical_bits)
        zeros = frame_size - occupied
        raw = self._estimate_from_zero_bitmap(zeros, frame_size)
        occupancy_ratio = occupied / float(frame_size) if frame_size > 0 else 0.0
        correction = 1.0
        if use_sog:
            correction += 0.10 * occupancy_ratio
        elif occupancy_ratio > 0.72:
            correction += 0.04 * (occupancy_ratio - 0.72) / 0.28
        return raw * correction, {
            "occupied": float(occupied),
            "zeros": float(zeros),
            "occupancy_ratio": float(occupancy_ratio),
            "correction": float(correction),
        }

    def _tag_response_bitmap(
        self,
        tag: Tag,
        batch_set: set,
        value_to_bit: Dict[int, int],
        seed: int,
        frame_size: int
    ):
        v = self._get_tag_attr_value(tag)
        if v not in batch_set:
            return None
        slot_id = simple_hash(tag.id, seed) % frame_size
        return slot_id, (1 << value_to_bit[v])

    def _tag_response_value_sog(
        self,
        tag: Tag,
        target_set: set,
        seed: int,
        frame_size: int,
        bits_per_slot: int
    ):
        v = self._get_tag_attr_value(tag)
        if v not in target_set:
            return None
        rnd = simple_hash(tag.id, simple_hash(seed, v ^ 0x9E3779B1))
        mu = 0
        found_one = False
        for i in range(bits_per_slot - 1, -1, -1):
            if (rnd >> i) & 1:
                found_one = True
                break
            mu += 1
        if not found_one or mu >= bits_per_slot:
            sog = 0
        else:
            sog = 1 << (bits_per_slot - 1 - mu)
        slot_id = simple_hash(v, seed) % frame_size
        return slot_id, sog

    def _tag_response_single_value(
        self,
        tag: Tag,
        target_value: int,
        seed: int,
        frame_size: int
    ):
        v = self._get_tag_attr_value(tag)
        if v != target_value:
            return None
        slot_id = simple_hash(tag.id, seed) % frame_size
        return slot_id, 1

    def _simulate_bitmap_frame(
        self,
        tags: List[Tag],
        batch: List[int],
        frame_size: int,
        seed: int
    ) -> Tuple[Frame, Dict[int, int]]:
        bits_per_slot = max(1, len(batch))
        frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        value_to_bit = {v: i for i, v in enumerate(batch)}
        batch_set = set(batch)
        for tag in tags:
            resp = self._tag_response_bitmap(tag, batch_set, value_to_bit, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                frame.aggregate(slot_id, reply_vector)
        return frame, value_to_bit

    def _simulate_sog_frame(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        frame_size: int,
        seed: int,
        bits_per_slot: int
    ) -> Frame:
        frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        target_set = set(candidate_values)
        for tag in tags:
            resp = self._tag_response_value_sog(tag, target_set, seed, frame_size, bits_per_slot)
            if resp is not None:
                slot_id, reply_vector = resp
                frame.aggregate(slot_id, reply_vector)
        return frame

    def _simulate_single_verify_frame(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int,
        seed: int
    ) -> Frame:
        frame = Frame(num_slots=frame_size, bits_per_slot=1)
        for tag in tags:
            resp = self._tag_response_single_value(tag, target_value, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                frame.aggregate(slot_id, reply_vector)
        return frame

    def _chunk_values(self, values: List[int], batch_size: int) -> List[List[int]]:
        return [values[i:i + batch_size] for i in range(0, len(values), batch_size)]

    def _run_coarse_bitmap_stage(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        batch_size: int
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]], Dict[str, int]]:
        estimates: Dict[int, float] = {}
        diagnostics: Dict[int, Dict[str, float]] = {}
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0

        batches = self._chunk_values(target_values, batch_size)
        for batch in batches:
            if not batch:
                continue
            seed = generate_random_seed()
            total_frames += 1
            total_reader_bits += seed.bit_length() + frame_size.bit_length() + len(batch).bit_length()
            total_slots += frame_size

            frame, value_to_bit = self._simulate_bitmap_frame(tags, batch, frame_size, seed)
            for value, bit_pos in value_to_bit.items():
                logical_bits = [((slot_data >> bit_pos) & 1) for slot_data in frame.slots]
                est, info = self._estimate_with_density_correction(logical_bits, frame_size, use_sog=False)
                estimates[value] = est
                diagnostics[value] = info

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": min(256, max(1, batch_size)),
        }
        return estimates, diagnostics, metrics

    def _run_refine_sog_stage(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        frame_size: int,
        bits_per_slot: int,
        rounds: int
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]], Dict[str, int], List[int]]:
        stats = {
            v: {
                "sum_est": 0.0,
                "rounds": 0,
                "unique_hits": 0,
                "slot_collisions": 0.0,
            } for v in candidate_values
        }
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        unique_counts_per_round: List[int] = []

        for _ in range(rounds):
            seed = generate_random_seed()
            total_frames += 1
            total_reader_bits += seed.bit_length() + frame_size.bit_length() + bits_per_slot.bit_length()
            total_slots += frame_size

            frame = self._simulate_sog_frame(tags, candidate_values, frame_size, seed, bits_per_slot)

            slot_to_values: Dict[int, List[int]] = {}
            for v in candidate_values:
                slot_id = simple_hash(v, seed) % frame_size
                slot_to_values.setdefault(slot_id, []).append(v)

            unique_count = 0
            for slot_id, values in slot_to_values.items():
                if len(values) == 1:
                    unique_count += 1
                    v = values[0]
                    est = self._estimate_from_sog_or(frame.slots[slot_id], bits_per_slot)
                    stats[v]["sum_est"] += est
                    stats[v]["rounds"] += 1
                    stats[v]["unique_hits"] += 1
                else:
                    for v in values:
                        stats[v]["slot_collisions"] += 1.0
            unique_counts_per_round.append(unique_count)

        estimates: Dict[int, float] = {}
        diagnostics: Dict[int, Dict[str, float]] = {}
        for v in candidate_values:
            used = stats[v]["rounds"]
            if used > 0:
                est = stats[v]["sum_est"] / used
            else:
                est = 0.0
            collision_ratio = stats[v]["slot_collisions"] / float(max(1, rounds))
            reliability = stats[v]["unique_hits"] / float(max(1, rounds))
            estimates[v] = est
            diagnostics[v] = {
                "rounds": float(used),
                "unique_hits": float(stats[v]["unique_hits"]),
                "collision_ratio": float(collision_ratio),
                "reliability": float(reliability),
            }

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": bits_per_slot,
        }
        return estimates, diagnostics, metrics, unique_counts_per_round

    def _run_single_verify_stage(
        self,
        tags: List[Tag],
        candidate_values: List[int],
        frame_size: int,
        rounds: int
    ) -> Tuple[Dict[int, float], Dict[int, int], Dict[str, int]]:
        estimates: Dict[int, float] = {}
        occupied_map: Dict[int, int] = {}
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0

        for value in candidate_values:
            est_sum = 0.0
            occ_sum = 0
            used = 0
            for _ in range(rounds):
                seed = generate_random_seed()
                total_frames += 1
                total_reader_bits += seed.bit_length() + frame_size.bit_length() + max(1, abs(int(value)).bit_length() + 1)
                total_slots += frame_size
                frame = self._simulate_single_verify_frame(tags, value, frame_size, seed)
                logical_bits = [1 if slot_data != 0 else 0 for slot_data in frame.slots]
                occupied = sum(logical_bits)
                zeros = frame_size - occupied
                est = self._estimate_from_zero_bitmap(zeros, frame_size)
                est_sum += est
                occ_sum += occupied
                used += 1
            estimates[value] = est_sum / max(1, used)
            occupied_map[value] = int(round(occ_sum / max(1, used)))

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": 1,
        }
        return estimates, occupied_map, metrics

    def _sort_desc(self, d: Dict[int, float]) -> List[Tuple[int, float]]:
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))

    def _confidence_radius(self, est: float, frame_size: int, rounds: int, reliability: float = 1.0) -> float:
        if est <= 0:
            base = 0.75
        else:
            base = 1.15 * math.sqrt(max(1.0, est) / max(1, rounds))
        density_term = 0.015 * max(1.0, math.log2(max(2, frame_size)))
        rel_penalty = 0.8 * max(0.0, 1.0 - reliability)
        return base + density_term + rel_penalty

    def _fuse_scores(
        self,
        coarse_counts: Dict[int, float],
        refine_counts: Dict[int, float],
        refine_diag: Dict[int, Dict[str, float]]
    ) -> Dict[int, float]:
        fused: Dict[int, float] = {}
        for v in coarse_counts.keys():
            c = coarse_counts.get(v, 0.0)
            r = refine_counts.get(v, 0.0)
            reliability = refine_diag.get(v, {}).get("reliability", 0.0)
            if r > 0:
                w_ref = min(0.55, 0.25 + 0.35 * reliability)
                w_coarse = 1.0 - w_ref
                fused[v] = w_coarse * c + w_ref * r
            else:
                fused[v] = 0.88 * c
        return fused

    def _decide_candidate_pool(
        self,
        fused_scores: Dict[int, float],
        max_keep: int
    ) -> List[int]:
        ordered = self._sort_desc(fused_scores)
        if not ordered:
            return []
        top_score = ordered[0][1]
        if top_score <= 0:
            return [ordered[0][0]]
        selected = []
        for value, score in ordered:
            if score >= top_score * 0.84:
                selected.append(value)
            if len(selected) >= max_keep:
                break
        if not selected:
            selected = [ordered[0][0]]
        return sorted(selected)

    def _conservative_multimode_decision(
        self,
        verify_counts: Dict[int, float],
        verify_frame_size: int,
        verify_rounds: int,
        refine_diag: Dict[int, Dict[str, float]]
    ) -> Tuple[List[int], float, Dict[str, Any]]:
        ordered = self._sort_desc(verify_counts)
        if not ordered:
            return [], 0.0, {"decision": "empty"}

        best_value, best_score = ordered[0]
        best_rel = refine_diag.get(best_value, {}).get("reliability", 1.0)
        best_radius = self._confidence_radius(best_score, verify_frame_size, verify_rounds, best_rel)
        selected = [best_value]
        close_competitors = 0

        for value, score in ordered[1:]:
            rel = refine_diag.get(value, {}).get("reliability", 1.0)
            radius = self._confidence_radius(score, verify_frame_size, verify_rounds, rel)
            overlap = (best_score - best_radius) <= (score + radius)
            gap_ratio = (best_score - score) / max(best_score, 1e-9) if best_score > 0 else 1.0
            if overlap and gap_ratio <= 0.12:
                selected.append(value)
                close_competitors += 1
            else:
                break

        selected = sorted(selected[:3])
        return selected, best_score, {
            "decision": "verified",
            "best_score": float(best_score),
            "best_radius": float(best_radius),
            "close_competitors": int(close_competitors),
            "ordered_verify_top": ordered[:5],
        }

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "total_slots": 0,
            }
            return [], 0, 0.0, verification_data

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots_weighted = 0.0

        stage_frame_sizes: List[int] = []
        stage_bits_per_slot: List[int] = []
        stage_names: List[str] = []

        domain_size = len(all_values)
        coarse_batch_size = min(256, max(8, min(128, domain_size)))
        coarse_frame_size = self._adaptive_frame_size(domain_size, "coarse")

        # --- 交叉部分：融合父代 A 的分批位图并行估计思想与父代 B 的自适应帧长/候选逐级筛选思想 ---
        coarse_counts, coarse_diag, coarse_metrics = self._run_coarse_bitmap_stage(
            tags=tags,
            target_values=all_values,
            frame_size=coarse_frame_size,
            batch_size=coarse_batch_size
        )
        total_frames += coarse_metrics["frames_used"]
        total_bits_sent_by_reader += coarse_metrics["reader_bits"]
        total_slots_weighted += coarse_metrics["total_slots"] * coarse_metrics["bits_per_slot"]
        for _ in range(coarse_metrics["frames_used"]):
            stage_frame_sizes.append(coarse_frame_size)
            stage_bits_per_slot.append(coarse_metrics["bits_per_slot"])
            stage_names.append("coarse_bitmap")

        sorted_coarse = self._sort_desc(coarse_counts)
        coarse_pool_size = min(12, max(4, int(math.sqrt(max(1, domain_size))) + 1))
        coarse_candidates = [v for v, s in sorted_coarse[:coarse_pool_size] if s > 0]
        if not coarse_candidates and sorted_coarse:
            coarse_candidates = [sorted_coarse[0][0]]

        if not coarse_candidates:
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots_weighted * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_metrics["bits_per_slot"],
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "total_slots": coarse_metrics["total_slots"],
                "coarse_top": sorted_coarse[:10],
                "early_stop": True,
            }
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        refine_frame_size = self._adaptive_frame_size(len(coarse_candidates), "refine")
        refine_bits_per_slot = 24
        refine_rounds = 3 if len(coarse_candidates) <= 8 else 4

        coarse_top = sorted_coarse[:min(3, len(sorted_coarse))]
        if len(coarse_top) >= 2 and coarse_top[0][1] > 0:
            coarse_gap = (coarse_top[0][1] - coarse_top[1][1]) / max(coarse_top[0][1], 1e-9)
            if coarse_gap < 0.08:
                refine_rounds += 1

        refine_counts, refine_diag, refine_metrics, unique_counts_per_round = self._run_refine_sog_stage(
            tags=tags,
            candidate_values=coarse_candidates,
            frame_size=refine_frame_size,
            bits_per_slot=refine_bits_per_slot,
            rounds=refine_rounds
        )
        total_frames += refine_metrics["frames_used"]
        total_bits_sent_by_reader += refine_metrics["reader_bits"]
        total_slots_weighted += refine_metrics["total_slots"] * refine_metrics["bits_per_slot"]
        for _ in range(refine_metrics["frames_used"]):
            stage_frame_sizes.append(refine_frame_size)
            stage_bits_per_slot.append(refine_bits_per_slot)
            stage_names.append("refine_sog")

        fused_scores = self._fuse_scores(
            coarse_counts={v: coarse_counts.get(v, 0.0) for v in coarse_candidates},
            refine_counts=refine_counts,
            refine_diag=refine_diag
        )
        fused_rank = self._sort_desc(fused_scores)
        preliminary_verify = self._decide_candidate_pool(fused_scores, max_keep=min(6, len(fused_rank)))

        if not preliminary_verify and fused_rank:
            preliminary_verify = [fused_rank[0][0]]

        need_verify = True
        early_stop = False
        ambiguity_ratio = 1.0
        if len(fused_rank) >= 2 and fused_rank[0][1] > 0:
            ambiguity_ratio = (fused_rank[0][1] - fused_rank[1][1]) / max(fused_rank[0][1], 1e-9)
            if ambiguity_ratio > 0.30 and refine_diag.get(fused_rank[0][0], {}).get("reliability", 0.0) >= 0.75:
                need_verify = False
                early_stop = True
        elif len(fused_rank) == 1:
            need_verify = False
            early_stop = True

        verify_frame_size = self._adaptive_frame_size(max(1, len(preliminary_verify)), "verify")
        verify_rounds = 2 if len(preliminary_verify) <= 4 else 1
        verify_counts: Dict[int, float] = {}
        occupied_map: Dict[int, int] = {}

        # --- 突变部分：引入“歧义感知动态轮数 + 置信半径修正 + 选择性早停/异常候选重检”全新机制 ---
        if need_verify and preliminary_verify:
            verify_counts, occupied_map, verify_metrics = self._run_single_verify_stage(
                tags=tags,
                candidate_values=preliminary_verify,
                frame_size=verify_frame_size,
                rounds=verify_rounds
            )
            total_frames += verify_metrics["frames_used"]
            total_bits_sent_by_reader += verify_metrics["reader_bits"]
            total_slots_weighted += verify_metrics["total_slots"] * verify_metrics["bits_per_slot"]
            for _ in range(verify_metrics["frames_used"]):
                stage_frame_sizes.append(verify_frame_size)
                stage_bits_per_slot.append(verify_metrics["bits_per_slot"])
                stage_names.append("verify_single")
        else:
            verify_metrics = {
                "frames_used": 0,
                "reader_bits": 0,
                "total_slots": 0,
                "bits_per_slot": 1,
            }
            verify_counts = {v: fused_scores.get(v, 0.0) for v in preliminary_verify}
            occupied_map = {}

        estimated_values, metric_score, decision_info = self._conservative_multimode_decision(
            verify_counts=verify_counts,
            verify_frame_size=verify_frame_size,
            verify_rounds=max(1, verify_rounds),
            refine_diag=refine_diag
        )

        extra_recheck_used = False
        recheck_counts: Dict[int, float] = {}
        recheck_frame_size = verify_frame_size

        if need_verify and len(estimated_values) == 1 and len(preliminary_verify) >= 2:
            ordered_verify = self._sort_desc(verify_counts)
            if len(ordered_verify) >= 2 and ordered_verify[0][1] > 0:
                top_gap = (ordered_verify[0][1] - ordered_verify[1][1]) / max(ordered_verify[0][1], 1e-9)
                if top_gap < 0.07:
                    extra_recheck_used = True
                    re_candidates = [ordered_verify[0][0], ordered_verify[1][0]]
                    if len(ordered_verify) >= 3:
                        third_gap = (ordered_verify[0][1] - ordered_verify[2][1]) / max(ordered_verify[0][1], 1e-9)
                        if third_gap < 0.10:
                            re_candidates.append(ordered_verify[2][0])

                    recheck_frame_size = min(32768, max(verify_frame_size, self._adaptive_frame_size(len(re_candidates) * 2, "verify")))
                    recheck_counts, recheck_occ_map, re_metrics = self._run_single_verify_stage(
                        tags=tags,
                        candidate_values=re_candidates,
                        frame_size=recheck_frame_size,
                        rounds=1
                    )
                    total_frames += re_metrics["frames_used"]
                    total_bits_sent_by_reader += re_metrics["reader_bits"]
                    total_slots_weighted += re_metrics["total_slots"] * re_metrics["bits_per_slot"]
                    for _ in range(re_metrics["frames_used"]):
                        stage_frame_sizes.append(recheck_frame_size)
                        stage_bits_per_slot.append(re_metrics["bits_per_slot"])
                        stage_names.append("recheck_single")
                    occupied_map.update(recheck_occ_map)

                    merged_verify = dict(verify_counts)
                    for v in re_candidates:
                        base = verify_counts.get(v, 0.0)
                        extra = recheck_counts.get(v, 0.0)
                        merged_verify[v] = (base * verify_rounds + extra) / float(verify_rounds + 1)

                    verify_counts = merged_verify
                    estimated_values, metric_score, decision_info = self._conservative_multimode_decision(
                        verify_counts=verify_counts,
                        verify_frame_size=recheck_frame_size,
                        verify_rounds=verify_rounds + 1,
                        refine_diag=refine_diag
                    )

        if not estimated_values:
            ordered_fallback = self._sort_desc(verify_counts if verify_counts else fused_scores)
            if ordered_fallback:
                estimated_values = [ordered_fallback[0][0]]
                metric_score = ordered_fallback[0][1]

        estimated_values = sorted(estimated_values[:3])

        total_slots_plain = sum(stage_frame_sizes)
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots_weighted * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": stage_frame_sizes[0] if stage_frame_sizes else 0,
            "bits_per_slot": stage_bits_per_slot[0] if stage_bits_per_slot else 0,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "total_slots": total_slots_plain,
            "coarse_frame_size": coarse_frame_size,
            "coarse_bits_per_slot": coarse_metrics["bits_per_slot"],
            "refine_frame_size": refine_frame_size,
            "refine_bits_per_slot": refine_bits_per_slot,
            "verify_frame_size": verify_frame_size,
            "verify_bits_per_slot": 1,
            "recheck_frame_size": recheck_frame_size if extra_recheck_used else 0,
            "coarse_candidate_pool_size": coarse_pool_size,
            "coarse_top": sorted_coarse[:10],
            "coarse_diagnostics": {k: coarse_diag[k] for k in coarse_candidates[:min(6, len(coarse_candidates))] if k in coarse_diag},
            "coarse_candidates": coarse_candidates,
            "refine_counts": refine_counts,
            "refine_diagnostics": refine_diag,
            "fused_scores": fused_scores,
            "preliminary_verify": preliminary_verify,
            "verify_counts": dict(self._sort_desc(verify_counts)),
            "occupied_slots_final": occupied_map,
            "recheck_counts": recheck_counts,
            "unique_counts_per_round": unique_counts_per_round,
            "decision_info": decision_info,
            "ambiguity_ratio": ambiguity_ratio,
            "need_verify": need_verify,
            "early_stop": early_stop,
            "extra_recheck_used": extra_recheck_used,
            "time_components_ms": {
                "T_reader": T_reader,
                "T_tag": T_tag,
                "T_overhead": T_overhead,
            },
        }

        metric = int(math.ceil(metric_score)) if metric_score > 0 else 0
        return list(estimated_values), metric, total_time_ms, verification_data
