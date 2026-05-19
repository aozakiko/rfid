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


class test_g1_1(IProtocol):
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

    def _estimate_cardinality_from_bitmap(self, zero_count: int, frame_size: int) -> float:
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

    def _simulate_tag_sem_batch_response(
        self,
        tags: List[Tag],
        batch_prices: List[int],
        seed: int,
        frame_size: int
    ) -> Frame:
        price_to_bit_pos = {p: i for i, p in enumerate(batch_prices)}
        batch_set = set(batch_prices)
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=len(batch_prices))
        for tag in tags:
            attr_v = self._get_tag_attr_value(tag)
            if attr_v in batch_set:
                slot_id = simple_hash(tag.id, seed) % frame_size
                bit_pos = price_to_bit_pos[attr_v]
                physical_frame.aggregate(slot_id, 1 << bit_pos)
        return physical_frame

    def _simulate_tag_sog_response(
        self,
        tags: List[Tag],
        seed: int,
        frame_size: int,
        bits_per_slot: int
    ) -> Frame:
        physical_frame = Frame(num_slots=frame_size, bits_per_slot=bits_per_slot)
        max_mask_bit = bits_per_slot - 1
        for tag in tags:
            attr_v = self._get_tag_attr_value(tag)
            if attr_v is None:
                continue
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
            sog = 0 if mu >= bits_per_slot else (1 << (max_mask_bit - mu))
            slot_id = simple_hash(attr_v, seed) % frame_size
            physical_frame.aggregate(slot_id, sog)
        return physical_frame

    def _select_conservative_candidates(
        self,
        scores: Dict[int, float],
        max_candidates: int = 4,
        rel_threshold: float = 0.92
    ) -> List[int]:
        positive_items = [(k, v) for k, v in scores.items() if v is not None and v > 0]
        if not positive_items:
            return []
        positive_items.sort(key=lambda x: (-x[1], x[0]))
        top_score = positive_items[0][1]
        if top_score <= 0:
            return []
        selected = []
        for val, score in positive_items:
            if score >= top_score * rel_threshold:
                selected.append(val)
            if len(selected) >= max_candidates:
                break
        if not selected:
            selected = [positive_items[0][0]]
        return sorted(selected)

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        all_values = list(range(price_min, price_max + 1))
        num_values = len(all_values)
        if num_values <= 0:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 0,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0
            }
            return [], 0, 0.0, verification_data

        stage_frame_sizes = []
        stage_bits_per_slot = []
        stage_names = []

        # --- 交叉部分：融合父代 A 的分批位图并行估计思想与父代 B 的碰撞草图/SOG 多轮统计思想 ---
        # Stage 1: 批量 SEM 位图筛选候选
        stage1_batch_size = min(128, num_values)
        stage1_bits_per_slot = stage1_batch_size
        stage1_frame_size = min(2048, max(256, 8 * stage1_batch_size))
        stage1_frame_size = min(stage1_frame_size, 32768)
        stage1_bits_per_slot = min(stage1_bits_per_slot, 256)

        sem_estimates = {p: 0.0 for p in all_values}
        sem_batches = [all_values[i:i + stage1_batch_size] for i in range(0, num_values, stage1_batch_size)]

        for batch in sem_batches:
            if not batch:
                continue
            seed = generate_random_seed()
            total_frames += 1
            stage_frame_sizes.append(stage1_frame_size)
            stage_bits_per_slot.append(len(batch))
            stage_names.append("sem_batch")

            total_bits_sent_by_reader += (
                seed.bit_length() +
                stage1_frame_size.bit_length() +
                len(batch).bit_length() +
                len(batch) * max(1, price_max.bit_length())
            )
            frame = self._simulate_tag_sem_batch_response(tags, batch, seed, stage1_frame_size)
            total_slots += stage1_frame_size

            price_to_bit_pos = {p: i for i, p in enumerate(batch)}
            for p, bit_pos in price_to_bit_pos.items():
                ones = 0
                for slot_data in frame.slots:
                    ones += (slot_data >> bit_pos) & 1
                zero_count = stage1_frame_size - ones
                sem_estimates[p] = self._estimate_cardinality_from_bitmap(zero_count, stage1_frame_size)

        pre_candidates = self._select_conservative_candidates(
            sem_estimates,
            max_candidates=min(8, max(2, int(math.sqrt(max(1, num_values))))),
            rel_threshold=0.88
        )

        if not pre_candidates:
            verification_data = {
                "frame_size": stage1_frame_size,
                "bits_per_slot": stage1_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "total_frames": total_frames,
                "stage1_sem_estimates": sem_estimates,
                "stage2_sog_estimates": {},
                "final_scores": {},
                "early_stop": True
            }
            T_reader = total_bits_sent_by_reader * 0.025
            avg_bits_per_slot = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
            T_tag = total_slots * avg_bits_per_slot * 0.4
            T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
            return [], 0, T_reader + T_tag + T_overhead, verification_data

        # Stage 2: 多轮 SOG 精细估计，优先解析“唯一映射时隙”
        stage2_bits_per_slot = 32
        stage2_base_frame_size = min(4096, max(512, 4 * len(pre_candidates)))
        stage2_base_frame_size = min(stage2_base_frame_size, 32768)
        stage2_rounds = 4

        sog_stats = {p: {"sum_est": 0.0, "rounds": 0, "unique_hits": 0} for p in pre_candidates}
        round_unique_counts = []

        for _ in range(stage2_rounds):
            seed = generate_random_seed()
            frame_size = stage2_base_frame_size
            total_frames += 1
            stage_frame_sizes.append(frame_size)
            stage_bits_per_slot.append(stage2_bits_per_slot)
            stage_names.append("sog_refine")

            total_bits_sent_by_reader += (
                seed.bit_length() +
                frame_size.bit_length() +
                stage2_bits_per_slot.bit_length()
            )
            frame = self._simulate_tag_sog_response(tags, seed, frame_size, stage2_bits_per_slot)
            total_slots += frame_size

            slot_to_candidates = defaultdict(list)
            for p in pre_candidates:
                slot_to_candidates[simple_hash(p, seed) % frame_size].append(p)

            unique_cnt = 0
            for slot_idx, vals in slot_to_candidates.items():
                if len(vals) == 1:
                    unique_cnt += 1
                    p = vals[0]
                    est = self._estimate_count_from_sog_or(frame.slots[slot_idx], stage2_bits_per_slot)
                    sog_stats[p]["sum_est"] += est
                    sog_stats[p]["rounds"] += 1
                    sog_stats[p]["unique_hits"] += 1
            round_unique_counts.append(unique_cnt)

        sog_estimates = {}
        for p in pre_candidates:
            if sog_stats[p]["rounds"] > 0:
                sog_estimates[p] = sog_stats[p]["sum_est"] / sog_stats[p]["rounds"]
            else:
                sog_estimates[p] = 0.0

        # --- 突变部分：引入动态重检与早停全新机制 ---
        # 机制1：根据第一阶段与第二阶段融合分数排序
        # 机制2：若前两名过近，则对少量候选做第三阶段定向重检
        final_scores = {}
        for p in pre_candidates:
            sem_score = sem_estimates.get(p, 0.0)
            sog_score = sog_estimates.get(p, 0.0)
            if sog_score > 0:
                final_scores[p] = 0.45 * sem_score + 0.55 * sog_score
            else:
                final_scores[p] = 0.8 * sem_score

        ranked = sorted(final_scores.items(), key=lambda x: (-x[1], x[0]))
        need_recheck = False
        if len(ranked) >= 2 and ranked[0][1] > 0:
            ratio = ranked[1][1] / ranked[0][1]
            if ratio >= 0.94:
                need_recheck = True

        recheck_estimates = {}
        early_stop = not need_recheck

        if need_recheck:
            top_recheck = [x[0] for x in ranked[:min(4, len(ranked))]]
            recheck_bits = min(256, len(top_recheck))
            # 动态帧长：候选越少，帧越短
            recheck_frame_size = min(2048, max(256, 16 * len(top_recheck)))
            recheck_frame_size = min(recheck_frame_size, 32768)

            seed = generate_random_seed()
            total_frames += 1
            stage_frame_sizes.append(recheck_frame_size)
            stage_bits_per_slot.append(recheck_bits)
            stage_names.append("sem_recheck")

            total_bits_sent_by_reader += (
                seed.bit_length() +
                recheck_frame_size.bit_length() +
                recheck_bits.bit_length() +
                len(top_recheck) * max(1, price_max.bit_length())
            )
            frame = self._simulate_tag_sem_batch_response(tags, top_recheck, seed, recheck_frame_size)
            total_slots += recheck_frame_size

            val_to_bit = {p: i for i, p in enumerate(top_recheck)}
            for p, bit_pos in val_to_bit.items():
                ones = 0
                for slot_data in frame.slots:
                    ones += (slot_data >> bit_pos) & 1
                zeros = recheck_frame_size - ones
                recheck_estimates[p] = self._estimate_cardinality_from_bitmap(zeros, recheck_frame_size)

            for p in top_recheck:
                final_scores[p] = 0.25 * sem_estimates.get(p, 0.0) + 0.35 * sog_estimates.get(p, 0.0) + 0.40 * recheck_estimates.get(p, 0.0)

        estimated_values = self._select_conservative_candidates(
            final_scores,
            max_candidates=3,
            rel_threshold=0.95 if need_recheck else 0.92
        )

        metric = 0
        if estimated_values:
            metric = int(math.ceil(max(final_scores.get(v, 0.0) for v in estimated_values)))

        T_reader = total_bits_sent_by_reader * 0.025
        avg_bits_per_slot = max(1, int(sum(stage_bits_per_slot) / len(stage_bits_per_slot))) if stage_bits_per_slot else 1
        T_tag = total_slots * avg_bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        total_time_ms = T_reader + T_tag + T_overhead

        verification_data = {
            "frame_size": stage_frame_sizes[0] if stage_frame_sizes else 0,
            "bits_per_slot": stage_bits_per_slot[0] if stage_bits_per_slot else 0,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "stage1_sem_estimates": sem_estimates,
            "stage2_sog_estimates": sog_estimates,
            "recheck_estimates": recheck_estimates,
            "final_scores": final_scores,
            "pre_candidates": pre_candidates,
            "round_unique_counts": round_unique_counts,
            "early_stop": early_stop
        }

        return estimated_values, metric, total_time_ms, verification_data
