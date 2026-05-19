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
            return hash((value, seed)) & 0x7fffffff


class test_g2_2(IProtocol):
    """
    面向离散属性众数/多众数查询的混合式 RFID 协议。
    设计目标：
    1) 继承批量位图并行估计基因；
    2) 继承逐级候选筛选与复核基因；
    3) 通过匿名聚合帧完成众数/多众数保守输出；
    4) 为后续扩展到多种统计任务保留协议骨架。
    """

    def _safe_attr_value(self, tag: Tag) -> Any:
        return tag.__dict__.get("price")

    def _leading_zero_index(self, value: int, width: int) -> int:
        for i in range(width):
            bit = (value >> (width - 1 - i)) & 1
            if bit == 1:
                return i
        return width

    def _build_sog_bit(self, tag_id: Any, seed: int, sog_length: int) -> int:
        rnd = simple_hash(tag_id, seed)
        idx = self._leading_zero_index(rnd, sog_length)
        if idx >= sog_length:
            return 0
        return 1 << (sog_length - 1 - idx)

    def _tag_response_bitmap(
        self,
        tag: Tag,
        target_set: set,
        value_to_bit: Dict[int, int],
        seed: int,
        frame_size: int
    ):
        v = self._safe_attr_value(tag)
        if v not in target_set:
            return None
        slot_id = simple_hash(tag.id, seed) % frame_size
        return slot_id, (1 << value_to_bit[v])

    def _tag_response_sog(
        self,
        tag: Tag,
        target_set: set,
        value_to_bit: Dict[int, int],
        seed: int,
        frame_size: int,
        sog_seed_base: int,
        sog_length: int
    ):
        v = self._safe_attr_value(tag)
        if v not in target_set:
            return None
        slot_seed = simple_hash(seed, v ^ 0x9E3779B1)
        slot_id = simple_hash(tag.id, slot_seed) % frame_size
        bit_pos = value_to_bit[v]
        sog_seed = simple_hash(sog_seed_base, v)
        sog_marker = self._build_sog_bit(tag.id, sog_seed, sog_length)
        strength = 1 if sog_marker != 0 else 0
        return slot_id, (strength << bit_pos)

    def _simulate_batch_frame(
        self,
        tags: List[Tag],
        batch: List[int],
        frame_size: int,
        seed: int,
        use_sog: bool = False,
        sog_length: int = 16
    ) -> Frame:
        bits_per_slot = max(1, len(batch))
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        value_to_bit = {v: i for i, v in enumerate(batch)}
        target_set = set(batch)
        sog_seed_base = simple_hash(seed, 0xA5A5A5A5)

        for tag in tags:
            if use_sog:
                resp = self._tag_response_sog(
                    tag, target_set, value_to_bit, seed, frame_size, sog_seed_base, sog_length
                )
            else:
                resp = self._tag_response_bitmap(
                    tag, target_set, value_to_bit, seed, frame_size
                )
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame

    def _simulate_single_value_frame(
        self,
        tags: List[Tag],
        target_value: int,
        frame_size: int,
        seed: int
    ) -> Frame:
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=1)
        target_set = {target_value}
        value_to_bit = {target_value: 0}
        for tag in tags:
            resp = self._tag_response_bitmap(tag, target_set, value_to_bit, seed, frame_size)
            if resp is not None:
                slot_id, reply_vector = resp
                physical_frame.aggregate(slot_id, reply_vector)
        return physical_frame

    def _estimate_cardinality(self, logical_bits: List[int], frame_size: int) -> float:
        occupied = sum(logical_bits)
        zeros = frame_size - occupied
        if zeros == frame_size:
            return 0.0
        if zeros == 0:
            return float(frame_size) * 2.0
        return -frame_size * math.log(zeros / frame_size)

    def _estimate_with_correction(
        self,
        logical_bits: List[int],
        frame_size: int,
        use_sog: bool = False
    ) -> Tuple[float, Dict[str, float]]:
        occupied = sum(logical_bits)
        zeros = frame_size - occupied
        raw = self._estimate_cardinality(logical_bits, frame_size)
        occupancy_ratio = occupied / frame_size if frame_size > 0 else 0.0
        if use_sog:
            correction = 1.0 + 0.12 * occupancy_ratio
        else:
            correction = 1.0
        return raw * correction, {
            "occupied": float(occupied),
            "zeros": float(zeros),
            "occupancy_ratio": float(occupancy_ratio),
            "correction": float(correction),
        }

    def _adaptive_frame_size(self, candidate_count: int, stage: str) -> int:
        candidate_count = max(1, candidate_count)
        if stage == "coarse":
            base = max(64, min(2048, candidate_count * 4))
        elif stage == "refine":
            base = max(256, min(8192, candidate_count * 32))
        else:
            base = max(512, min(16384, candidate_count * 96))
        power = 1
        while power < base and power < 32768:
            power <<= 1
        return min(32768, max(64, power))

    def _chunk_values(self, values: List[int], batch_size: int) -> List[List[int]]:
        return [values[i:i + batch_size] for i in range(0, len(values), batch_size)]

    def _run_parallel_stage(
        self,
        tags: List[Tag],
        target_values: List[int],
        frame_size: int,
        batch_size: int,
        rounds: int,
        use_sog: bool,
        sog_length: int
    ) -> Tuple[Dict[int, float], Dict[int, Dict[str, float]], Dict[str, int]]:
        aggregated: Dict[int, float] = {}
        seen: Dict[int, int] = {}
        diagnostics: Dict[int, Dict[str, float]] = {}
        total_frames = 0
        total_reader_bits = 0
        total_slots = 0
        bits_per_slot = max(1, min(batch_size, 256))

        batches = self._chunk_values(target_values, batch_size)

        for _ in range(rounds):
            for batch in batches:
                if not batch:
                    continue
                seed = generate_random_seed()
                total_frames += 1
                total_reader_bits += seed.bit_length() + frame_size.bit_length() + len(batch).bit_length() + max(1, sog_length.bit_length())
                total_slots += frame_size

                physical_frame = self._simulate_batch_frame(
                    tags=tags,
                    batch=batch,
                    frame_size=frame_size,
                    seed=seed,
                    use_sog=use_sog,
                    sog_length=sog_length
                )

                for bit_pos, value in enumerate(batch):
                    logical_bits = [((slot_data >> bit_pos) & 1) for slot_data in physical_frame.slots]
                    est, info = self._estimate_with_correction(logical_bits, frame_size, use_sog=use_sog)
                    aggregated[value] = aggregated.get(value, 0.0) + est
                    seen[value] = seen.get(value, 0) + 1
                    prev = diagnostics.get(value, {"occupied": 0.0, "zeros": 0.0, "occupancy_ratio": 0.0, "correction": 0.0})
                    diagnostics[value] = {
                        "occupied": prev["occupied"] + info["occupied"],
                        "zeros": prev["zeros"] + info["zeros"],
                        "occupancy_ratio": prev["occupancy_ratio"] + info["occupancy_ratio"],
                        "correction": prev["correction"] + info["correction"],
                    }

        final_counts: Dict[int, float] = {}
        for v in target_values:
            cnt = seen.get(v, 0)
            if cnt <= 0:
                final_counts[v] = 0.0
                continue
            final_counts[v] = aggregated[v] / cnt
            d = diagnostics.get(v)
            if d is not None:
                diagnostics[v] = {
                    "occupied": d["occupied"] / cnt,
                    "zeros": d["zeros"] / cnt,
                    "occupancy_ratio": d["occupancy_ratio"] / cnt,
                    "correction": d["correction"] / cnt,
                }

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": bits_per_slot,
        }
        return final_counts, diagnostics, metrics

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
            est_acc = 0.0
            occ_acc = 0
            used = 0
            for _ in range(rounds):
                seed = generate_random_seed()
                total_frames += 1
                total_reader_bits += seed.bit_length() + frame_size.bit_length() + max(1, int(value)).bit_length() if isinstance(value, int) and value > 0 else seed.bit_length() + frame_size.bit_length() + 1
                total_slots += frame_size
                physical_frame = self._simulate_single_value_frame(tags, value, frame_size, seed)
                logical_bits = [1 if slot_data != 0 else 0 for slot_data in physical_frame.slots]
                est = self._estimate_cardinality(logical_bits, frame_size)
                est_acc += est
                occ_acc += sum(logical_bits)
                used += 1
            estimates[value] = est_acc / max(1, used)
            occupied_map[value] = int(round(occ_acc / max(1, used)))

        metrics = {
            "frames_used": total_frames,
            "reader_bits": total_reader_bits,
            "total_slots": total_slots,
            "bits_per_slot": 1,
        }
        return estimates, occupied_map, metrics

    def _sort_desc(self, d: Dict[int, float]) -> List[Tuple[int, float]]:
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))

    def _select_top_with_ties(self, estimates: Dict[int, float], limit: int) -> List[int]:
        ordered = self._sort_desc(estimates)
        if not ordered:
            return []
        if len(ordered) <= limit:
            return [v for v, _ in ordered]
        threshold = ordered[limit - 1][1]
        selected = [v for v, s in ordered if s >= threshold]
        return selected[:max(limit + 2, limit)]

    def _confidence_interval_radius(self, est: float, frame_size: int, rounds: int) -> float:
        if est <= 0:
            return 0.5
        variance_proxy = max(1.0, est)
        radius = 1.2 * math.sqrt(variance_proxy / max(1, rounds))
        density_penalty = 0.02 * frame_size / max(64, frame_size)
        return radius + density_penalty

    def _conservative_multimode_decision(
        self,
        refined_counts: Dict[int, float],
        verify_counts: Dict[int, float],
        verify_frame_size: int,
        verify_rounds: int
    ) -> Tuple[List[int], float, Dict[str, Any]]:
        if not verify_counts:
            ordered_ref = self._sort_desc(refined_counts)
            if not ordered_ref:
                return [], 0.0, {"decision": "empty"}
            return [ordered_ref[0][0]], ordered_ref[0][1], {"decision": "refine_fallback"}

        ordered = self._sort_desc(verify_counts)
        best_value, best_score = ordered[0]
        best_radius = self._confidence_interval_radius(best_score, verify_frame_size, verify_rounds)
        selected = [best_value]

        # --- 交叉部分：融合父代 A 的“多轮并行统计估计/SOG 校正”思想与父代 B 的“逐级候选筛选/单值复核”思想 ---
        # --- 突变部分：引入“置信区间修正 + 异常候选重检触发 + 早停”全新机制 ---
        abnormal_gap_trigger = False
        for value, score in ordered[1:]:
            radius = self._confidence_interval_radius(score, verify_frame_size, verify_rounds)
            refined_score = refined_counts.get(value, 0.0)
            refined_best = refined_counts.get(best_value, best_score)
            overlap = (best_score - best_radius) <= (score + radius)
            refined_close = abs(refined_best - refined_score) <= max(best_radius + radius, 0.08 * max(best_score, 1.0))
            if overlap and refined_close:
                selected.append(value)
            else:
                if best_score > 0 and (best_score - score) / best_score < 0.06:
                    abnormal_gap_trigger = True
                break

        selected = sorted(selected[:3])

        decision_info = {
            "decision": "verified",
            "best_score": best_score,
            "best_radius": best_radius,
            "abnormal_gap_trigger": abnormal_gap_trigger,
            "ordered_verify_top": ordered[:5],
        }
        return selected, best_score, decision_info

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        all_values = list(range(price_min, price_max + 1))
        if not all_values:
            verification_data = {
                "frame_size": 1,
                "bits_per_slot": 1,
                "stage_frame_sizes": {},
                "stage_bits_per_slot": {},
                "total_frames": 0,
                "total_slots": 0,
            }
            return [], 0, 0.0, verification_data

        domain_size = len(all_values)
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        coarse_batch_size = min(128, max(8, min(domain_size, 128)))
        coarse_frame_size = self._adaptive_frame_size(domain_size, "coarse")
        coarse_rounds = 1

        coarse_counts, coarse_diag, coarse_metrics = self._run_parallel_stage(
            tags=tags,
            target_values=all_values,
            frame_size=coarse_frame_size,
            batch_size=coarse_batch_size,
            rounds=coarse_rounds,
            use_sog=False,
            sog_length=8
        )
        total_frames += coarse_metrics["frames_used"]
        total_bits_sent_by_reader += coarse_metrics["reader_bits"]
        total_slots += coarse_metrics["total_slots"]

        coarse_pool_size = min(12, max(4, int(math.sqrt(max(1, domain_size))) + 1))
        coarse_candidates = self._select_top_with_ties(coarse_counts, coarse_pool_size)
        if not coarse_candidates:
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_metrics["bits_per_slot"],
                "stage_frame_sizes": {"coarse": coarse_frame_size},
                "stage_bits_per_slot": {"coarse": coarse_metrics["bits_per_slot"]},
                "total_frames": total_frames,
                "total_slots": total_slots,
                "coarse_top": self._sort_desc(coarse_counts)[:10],
            }
            T_reader = total_bits_sent_by_reader * 0.025
            T_tag = total_slots * coarse_metrics["bits_per_slot"] * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        refine_batch_size = min(64, max(4, len(coarse_candidates)))
        refine_frame_size = self._adaptive_frame_size(len(coarse_candidates), "refine")
        refine_rounds = 2

        refined_counts, refined_diag, refine_metrics = self._run_parallel_stage(
            tags=tags,
            target_values=coarse_candidates,
            frame_size=refine_frame_size,
            batch_size=refine_batch_size,
            rounds=refine_rounds,
            use_sog=True,
            sog_length=24
        )
        total_frames += refine_metrics["frames_used"]
        total_bits_sent_by_reader += refine_metrics["reader_bits"]
        total_slots += refine_metrics["total_slots"]

        sorted_refined = self._sort_desc(refined_counts)
        preliminary_verify = [v for v, _ in sorted_refined[:min(6, len(sorted_refined))]]

        if not preliminary_verify and sorted_refined:
            preliminary_verify = [sorted_refined[0][0]]

        if not preliminary_verify:
            verification_data = {
                "frame_size": refine_frame_size,
                "bits_per_slot": refine_metrics["bits_per_slot"],
                "stage_frame_sizes": {
                    "coarse": coarse_frame_size,
                    "refine": refine_frame_size,
                },
                "stage_bits_per_slot": {
                    "coarse": coarse_metrics["bits_per_slot"],
                    "refine": refine_metrics["bits_per_slot"],
                },
                "total_frames": total_frames,
                "total_slots": total_slots,
                "coarse_top": self._sort_desc(coarse_counts)[:10],
                "refined_top": sorted_refined[:10],
            }
            T_reader = total_bits_sent_by_reader * 0.025
            bits_for_time = max(coarse_metrics["bits_per_slot"], refine_metrics["bits_per_slot"])
            T_tag = total_slots * bits_for_time * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        verify_frame_size = self._adaptive_frame_size(len(preliminary_verify), "verify")
        verify_rounds = 2

        verify_counts, occupied_map, verify_metrics = self._run_single_verify_stage(
            tags=tags,
            candidate_values=preliminary_verify,
            frame_size=verify_frame_size,
            rounds=verify_rounds
        )
        total_frames += verify_metrics["frames_used"]
        total_bits_sent_by_reader += verify_metrics["reader_bits"]
        total_slots += verify_metrics["total_slots"]

        estimated_values, metric_score, decision_info = self._conservative_multimode_decision(
            refined_counts=refined_counts,
            verify_counts=verify_counts,
            verify_frame_size=verify_frame_size,
            verify_rounds=verify_rounds
        )

        extra_recheck_used = False
        recheck_counts: Dict[int, float] = {}
        recheck_frame_size = verify_frame_size

        if decision_info.get("abnormal_gap_trigger") and len(estimated_values) == 1 and len(preliminary_verify) >= 2:
            extra_recheck_used = True
            re_candidates = preliminary_verify[:min(3, len(preliminary_verify))]
            recheck_frame_size = min(32768, max(verify_frame_size, self._adaptive_frame_size(len(re_candidates) * 2, "verify")))
            recheck_counts, recheck_occ_map, re_metrics = self._run_single_verify_stage(
                tags=tags,
                candidate_values=re_candidates,
                frame_size=recheck_frame_size,
                rounds=1
            )
            total_frames += re_metrics["frames_used"]
            total_bits_sent_by_reader += re_metrics["reader_bits"]
            total_slots += re_metrics["total_slots"]
            occupied_map.update(recheck_occ_map)

            merged_verify = {}
            for v in re_candidates:
                base = verify_counts.get(v, 0.0)
                extra = recheck_counts.get(v, 0.0)
                merged_verify[v] = (base * verify_rounds + extra) / (verify_rounds + 1)

            estimated_values, metric_score, decision_info = self._conservative_multimode_decision(
                refined_counts=refined_counts,
                verify_counts=merged_verify,
                verify_frame_size=recheck_frame_size,
                verify_rounds=verify_rounds + 1
            )
            verify_counts.update(merged_verify)

        if not estimated_values and sorted_refined:
            estimated_values = [sorted_refined[0][0]]
            metric_score = sorted_refined[0][1]

        estimated_values = sorted(estimated_values[:3])

        bits_per_slot_for_time = max(
            1,
            coarse_metrics["bits_per_slot"],
            refine_metrics["bits_per_slot"],
            verify_metrics["bits_per_slot"]
        )
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot_for_time * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0.0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": verify_frame_size,
            "bits_per_slot": verify_metrics["bits_per_slot"],
            "stage_frame_sizes": {
                "coarse": coarse_frame_size,
                "refine": refine_frame_size,
                "verify": verify_frame_size,
                "recheck": recheck_frame_size if extra_recheck_used else 0,
            },
            "stage_bits_per_slot": {
                "coarse": coarse_metrics["bits_per_slot"],
                "refine": refine_metrics["bits_per_slot"],
                "verify": verify_metrics["bits_per_slot"],
                "recheck": 1 if extra_recheck_used else 0,
            },
            "total_frames": total_frames,
            "total_slots": total_slots,
            "coarse_candidate_pool_size": coarse_pool_size,
            "coarse_top": self._sort_desc(coarse_counts)[:10],
            "refined_top": sorted_refined[:10],
            "verify_counts": dict(self._sort_desc(verify_counts)),
            "occupied_slots_final": occupied_map,
            "coarse_diagnostics": {k: coarse_diag[k] for k in coarse_candidates[:min(6, len(coarse_candidates))] if k in coarse_diag},
            "refined_diagnostics": {k: refined_diag[k] for k in preliminary_verify[:min(6, len(preliminary_verify))] if k in refined_diag},
            "decision_info": decision_info,
            "extra_recheck_used": extra_recheck_used,
        }

        metric = int(math.ceil(metric_score)) if metric_score != float("inf") else int(10 ** 9)
        return list(estimated_values), metric, total_time_ms, verification_data
