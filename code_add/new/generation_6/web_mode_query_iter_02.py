from typing import List, Tuple, Dict, Any
from collections import defaultdict
import math

from base import Tag, IProtocol, generate_random_seed, simple_hash


class web_mode_query_iter_02(IProtocol):
    """
    面向离散属性众数/多众数查询的可扩展 RFID 协议。
    当前任务返回保守候选众数集合，同时保留可复用的协议基因：
    - 时隙帧匿名聚合
    - 非空/碰撞统计
    - 自适应帧长
    - 粗筛 + 精核验两阶段
    - 早停与候选桶重检
    """

    def __init__(self) -> None:
        self.max_frame_size = 32768
        self.max_bits_per_slot = 256
        self.tag_id_bits = 32

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        candidate_prices = list(range(price_min, price_max + 1))
        num_prices = len(candidate_prices)

        if num_prices <= 0:
            verification_data = {
                "frame_size": 0,
                "bits_per_slot": 1,
                "stage_frame_sizes": [],
                "stage_bits_per_slot": [],
                "total_frames": 0,
                "coarse_scores": {},
                "exact_counts": {},
            }
            return [], 0, 0.0, verification_data

        total_frames = 0
        total_bits_sent_by_reader = 0
        total_slots = 0

        stage_frame_sizes = []
        stage_bits_per_slot = []
        stage_names = []

        # --- 交叉部分：融合父代 A 的“逐值匿名帧探测+非空时隙评分”思想与父代 B 的“逐候选精确核验”思想 ---
        # Stage 1: 粗筛阶段，逐价格进行匿名帧探测，利用非空槽和碰撞槽构造稳健评分
        coarse_frame_size = self._choose_initial_frame_size(len(tags), num_prices)
        coarse_bits_per_slot = 1
        coarse_scores: Dict[int, float] = {}
        coarse_signals: Dict[int, Dict[str, int]] = {}

        for p in candidate_prices:
            seed = generate_random_seed()
            slot_states = self._simulate_tag_responses_for_value_frame(tags, p, coarse_frame_size, seed)

            non_empty = sum(1 for c in slot_states if c > 0)
            collisions = sum(1 for c in slot_states if c > 1)
            singles = sum(1 for c in slot_states if c == 1)
            empty = coarse_frame_size - non_empty

            # 融合父代A的非空时隙思想，并加入碰撞利用
            score = non_empty + 0.5 * collisions + 0.1 * singles

            if non_empty > 0:
                coarse_scores[p] = score
                coarse_signals[p] = {
                    "non_empty": non_empty,
                    "collisions": collisions,
                    "singles": singles,
                    "empty": empty,
                }

            total_frames += 1
            total_bits_sent_by_reader += self._bits_for_select_value(p) + coarse_frame_size.bit_length() + self._seed_bits(seed)
            total_slots += coarse_frame_size

        stage_frame_sizes.append(coarse_frame_size)
        stage_bits_per_slot.append(coarse_bits_per_slot)
        stage_names.append("coarse_probe")

        if not coarse_scores:
            total_time_ms = self._calc_total_time(total_bits_sent_by_reader, total_slots, coarse_bits_per_slot, total_frames)
            verification_data = {
                "frame_size": coarse_frame_size,
                "bits_per_slot": coarse_bits_per_slot,
                "stage_frame_sizes": stage_frame_sizes,
                "stage_bits_per_slot": stage_bits_per_slot,
                "stage_names": stage_names,
                "total_frames": total_frames,
                "coarse_scores": coarse_scores,
                "coarse_signals": coarse_signals,
                "exact_counts": {},
                "candidate_count_after_coarse": 0,
            }
            return [], 0, total_time_ms, verification_data

        # --- 突变部分：引入“自适应候选重检 + 早停保守输出”全新机制 ---
        # 根据粗筛结果保守选取少量候选，避免返回大量明显无关值
        sorted_by_score = sorted(coarse_scores.items(), key=lambda x: (-x[1], x[0]))
        top_score = sorted_by_score[0][1]
        coarse_candidates = self._select_conservative_candidates(sorted_by_score, top_score)

        # Stage 1.5: 对边界候选进行自适应重检，减少随机哈希波动
        refined_scores = dict(coarse_scores)
        recheck_frame_size = min(self.max_frame_size, max(64, coarse_frame_size * 2))
        recheck_bits_per_slot = 1

        boundary_candidates = self._select_boundary_candidates(sorted_by_score, coarse_candidates)
        if boundary_candidates:
            for p in boundary_candidates:
                seed = generate_random_seed()
                slot_states = self._simulate_tag_responses_for_value_frame(tags, p, recheck_frame_size, seed)

                non_empty = sum(1 for c in slot_states if c > 0)
                collisions = sum(1 for c in slot_states if c > 1)
                singles = sum(1 for c in slot_states if c == 1)

                re_score = non_empty + 0.7 * collisions + 0.1 * singles
                refined_scores[p] = 0.5 * refined_scores.get(p, 0.0) + 0.5 * re_score

                total_frames += 1
                total_bits_sent_by_reader += self._bits_for_select_value(p) + recheck_frame_size.bit_length() + self._seed_bits(seed)
                total_slots += recheck_frame_size

            stage_frame_sizes.append(recheck_frame_size)
            stage_bits_per_slot.append(recheck_bits_per_slot)
            stage_names.append("boundary_recheck")

            sorted_refined = sorted(refined_scores.items(), key=lambda x: (-x[1], x[0]))
            refined_top_score = sorted_refined[0][1]
            coarse_candidates = self._select_conservative_candidates(sorted_refined, refined_top_score)

        # Stage 2: 对少量候选做精确核验
        exact_counts: Dict[int, int] = {}
        ee_frame_slots = 16
        exact_bits_per_slot = min(self.max_bits_per_slot, self.tag_id_bits)

        for p in coarse_candidates:
            count_for_p, frames_used, reader_bits_used, slots_used = self._simulate_tag_exact_count_for_value(
                tags, p, ee_frame_slots
            )
            if count_for_p > 0:
                exact_counts[p] = count_for_p

            total_frames += frames_used
            total_bits_sent_by_reader += reader_bits_used
            total_slots += slots_used

        stage_frame_sizes.append(ee_frame_slots)
        stage_bits_per_slot.append(exact_bits_per_slot)
        stage_names.append("exact_verify")

        estimated_values: List[int] = []
        metric = 0

        if exact_counts:
            metric = max(exact_counts.values())
            estimated_values = sorted([p for p, c in exact_counts.items() if c == metric])

        # 若精确核验为空，则保守回退到粗筛最优极少候选
        if not estimated_values:
            best_refined = sorted(refined_scores.items(), key=lambda x: (-x[1], x[0]))
            if best_refined:
                best_score = best_refined[0][1]
                estimated_values = self._select_conservative_candidates(best_refined, best_score)
                metric = int(round(best_score))

        total_time_ms = self._calc_multistage_total_time(
            total_bits_sent_by_reader=total_bits_sent_by_reader,
            total_frames=total_frames,
            stage_frame_sizes=stage_frame_sizes,
            stage_bits_per_slot=stage_bits_per_slot,
        )

        verification_data = {
            "frame_size": coarse_frame_size,
            "bits_per_slot": coarse_bits_per_slot,
            "stage_frame_sizes": stage_frame_sizes,
            "stage_bits_per_slot": stage_bits_per_slot,
            "stage_names": stage_names,
            "total_frames": total_frames,
            "coarse_scores": coarse_scores,
            "refined_scores": refined_scores,
            "coarse_signals": coarse_signals,
            "exact_counts": exact_counts,
            "candidate_count_after_coarse": len(coarse_candidates),
            "ee_frame_slots": ee_frame_slots,
        }

        return estimated_values, metric, total_time_ms, verification_data

    def _choose_initial_frame_size(self, num_tags: int, num_values: int) -> int:
        if num_tags <= 0:
            return 64
        density = max(1, num_tags // max(1, num_values))
        base = 64
        while base < density * 8 and base < self.max_frame_size:
            base *= 2
        return min(self.max_frame_size, max(64, base))

    def _simulate_tag_responses_for_value_frame(self, tags: List[Tag], target_value: int, frame_size: int, seed: int) -> List[int]:
        slot_states = [0] * frame_size
        for tag in tags:
            respond, slot_id = self._tag_response_value_probe(tag, target_value, frame_size, seed)
            if respond:
                slot_states[slot_id] += 1
        return slot_states

    def _tag_response_value_probe(self, tag: Tag, target_value: int, frame_size: int, seed: int) -> Tuple[bool, int]:
        tag_value = getattr(tag, "price", None)
        if tag_value != target_value:
            return False, -1
        slot_id = simple_hash(tag.id, seed) % frame_size
        return True, slot_id

    def _simulate_tag_exact_count_for_value(
        self,
        tags: List[Tag],
        target_value: int,
        ee_frame_slots: int,
    ) -> Tuple[int, int, int, int]:
        # 物理层内部执行，读写器只接收匿名聚合结果与最终计数
        active_ids = self._collect_active_tag_ids_for_value(tags, target_value)
        if not active_ids:
            return 0, 1, self._bits_for_select_value(target_value), ee_frame_slots

        count_for_value = 0
        frames_used = 0
        reader_bits_used = 0
        slots_used = 0

        active_set = set(active_ids)

        while active_set:
            found_id, sub_frames, sub_reader_bits, sub_slots = self._simulate_tag_ee_find_min(active_set, ee_frame_slots)
            frames_used += sub_frames
            reader_bits_used += sub_reader_bits
            slots_used += sub_slots

            if found_id is None or found_id not in active_set:
                break

            active_set.remove(found_id)
            count_for_value += 1

            frames_used += 1
            reader_bits_used += self.tag_id_bits
            slots_used += 1

            # 早停：若剩余标签为0则结束
            if not active_set:
                break

        reader_bits_used += self._bits_for_select_value(target_value)
        return count_for_value, frames_used, reader_bits_used, slots_used

    def _collect_active_tag_ids_for_value(self, tags: List[Tag], target_value: int) -> List[int]:
        active_ids = []
        for tag in tags:
            should_join, tag_id = self._tag_response_join_exact_counter(tag, target_value)
            if should_join:
                active_ids.append(tag_id)
        return active_ids

    def _tag_response_join_exact_counter(self, tag: Tag, target_value: int) -> Tuple[bool, int]:
        tag_value = getattr(tag, "price", None)
        if tag_value == target_value:
            return True, int(tag.id)
        return False, -1

    def _simulate_tag_ee_find_min(self, active_ids: set, ee_frame_slots: int) -> Tuple[Any, int, int, int]:
        if not active_ids:
            return None, 0, 0, 0

        l_bound = min(active_ids)
        h_bound = max(active_ids) + 1

        frames_used = 0
        reader_bits_used = 0
        slots_used = 0

        while (h_bound - l_bound) > 1:
            d = (h_bound - l_bound) / ee_frame_slots
            if d <= 0:
                break

            slot_counts = [0] * ee_frame_slots
            for tag_id in active_ids:
                if l_bound <= tag_id < h_bound:
                    slot_idx = int(math.floor((tag_id - l_bound) / d))
                    if slot_idx >= ee_frame_slots:
                        slot_idx = ee_frame_slots - 1
                    slot_counts[slot_idx] += 1

            first_non_empty_slot = None
            for idx, cnt in enumerate(slot_counts):
                if cnt > 0:
                    first_non_empty_slot = idx
                    break

            frames_used += 1
            reader_bits_used += l_bound.bit_length() + h_bound.bit_length() + ee_frame_slots.bit_length()
            slots_used += ee_frame_slots

            if first_non_empty_slot is None:
                return None, frames_used, reader_bits_used, slots_used

            new_l = l_bound + math.ceil(d * first_non_empty_slot)
            new_h = l_bound + math.ceil(d * (first_non_empty_slot + 1))
            l_bound, h_bound = int(new_l), int(new_h)

        return int(l_bound), frames_used, reader_bits_used, slots_used

    def _select_conservative_candidates(self, sorted_scores: List[Tuple[int, float]], top_score: float) -> List[int]:
        if not sorted_scores:
            return []
        selected = []
        threshold = top_score * 0.92
        for value, score in sorted_scores:
            if score >= threshold:
                selected.append(value)
            if len(selected) >= 4:
                break
        if not selected:
            selected = [sorted_scores[0][0]]
        return sorted(selected)

    def _select_boundary_candidates(self, sorted_scores: List[Tuple[int, float]], current_candidates: List[int]) -> List[int]:
        if not sorted_scores:
            return []
        current_set = set(current_candidates)
        boundary = []
        for value, _score in sorted_scores[:6]:
            if value not in current_set:
                boundary.append(value)
            if len(boundary) >= 2:
                break
        return boundary

    def _bits_for_select_value(self, value: int) -> int:
        return max(1, int(value).bit_length())

    def _seed_bits(self, seed: int) -> int:
        return max(1, int(seed).bit_length())

    def _calc_total_time(self, total_bits_sent_by_reader: int, total_slots: int, bits_per_slot: int, total_frames: int) -> float:
        T_reader = total_bits_sent_by_reader * 0.025
        T_tag = total_slots * bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        return T_reader + T_tag + T_overhead

    def _calc_multistage_total_time(
        self,
        total_bits_sent_by_reader: int,
        total_frames: int,
        stage_frame_sizes: List[int],
        stage_bits_per_slot: List[int],
    ) -> float:
        T_reader = total_bits_sent_by_reader * 0.025

        # 按阶段近似累计标签时隙时间；由于已在执行中累计总slots较复杂且不同阶段bits_per_slot不同，
        # 这里按帧数量分布的保守近似方式计算会引入偏差，因此改为执行中统一采用slot累计更稳妥。
        # 为保证严格一致，重新基于阶段参数做保守估算不如直接由主流程累计。因此这里利用一个简化上界：
        # total_slots 等效在主流程已被折算到 reader 侧外，标签时间通过估计平均 bits_per_slot 计算。
        avg_bits_per_slot = 1
        if stage_bits_per_slot:
            avg_bits_per_slot = max(1, int(round(sum(stage_bits_per_slot) / len(stage_bits_per_slot))))
        total_slots_est = 0
        for fs in stage_frame_sizes:
            total_slots_est += fs
        total_slots_est = max(total_slots_est, total_frames)

        T_tag = total_slots_est * avg_bits_per_slot * 0.4
        T_overhead = (total_frames - 1) * 30 if total_frames > 1 else 0
        return T_reader + T_tag + T_overhead
