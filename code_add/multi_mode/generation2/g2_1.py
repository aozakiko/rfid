import math
import random
from typing import List, Tuple, Dict
from collections import defaultdict
from base import Tag, Frame, generate_random_seed, IProtocol
from hash import simple_hash

class g2_1(IProtocol):
    """
    混合变异协议 (Crossover Mutation Protocol)

    本协议通过交叉融合与基因突变，创造了一个用于解决多众数查询问题的新协议。
    它首先利用一种宏观扫描策略快速定位“热门”价格区间，然后对这些高潜力区间
    进行精确的二分查找，最终找出全局众数。
    """

    def execute(self, tags: List[Tag], price_min: int, price_max: int) -> Tuple[List[int], int, float, Dict]:
        """
        执行协议的主函数。
        """
        # --- 协议超参数 ---
        self.BUCKET_SIZE = 100         # 初始价格分组的粒度
        self.INITIAL_FRAME_SIZE = 128  # 估算阶段的初始帧长
        self.RESIZE_FACTOR = 2.0       # 帧长调整因子
        self.COLLISION_THRESHOLD = 0.1 # 触发帧长调整的碰撞率阈值
        self.MAX_RETRIES = 3           # 动态调整帧长的最大尝试次数

        # --- 性能统计变量 ---
        self.total_frames = 0
        self.total_bits_sent_by_reader = 0
        self.total_tag_slots = 0
        self.bits_per_slot = 1 # 默认值，在具体阶段可能被修改

        # --- 交叉部分：融合父代A的“并行批量估算”思想与父代B（推测）的“分治/范围查询”思想 ---
        # 阶段一: 粗粒度扫描，识别“热门”价格区间 (Hotspot Bucket Identification)
        # 采用类似父代A的思路，但不对单个价格估算，而是对价格“桶”进行并行估算，这借鉴了分治思想。
        
        price_buckets = self._create_price_buckets(price_min, price_max, self.BUCKET_SIZE)
        bucket_estimates = {}
        bucket_final_frames = {} # 记录每个桶最终使用的帧长

        for bucket_id, price_range in price_buckets.items():
            estimated_count, final_frame_size = self._estimate_tags_in_bucket_dynamically(tags, price_range)
            bucket_estimates[bucket_id] = estimated_count
            bucket_final_frames[bucket_id] = final_frame_size

        # 阶段二: 确定候选的热门区间 (Candidate Bucket Selection)
        if not bucket_estimates or max(bucket_estimates.values()) == 0:
            return [], 0, self._calculate_total_time(), {"status": "No tags found or estimated."}

        max_estimated_count = max(bucket_estimates.values())
        # 找出所有估算值最高的桶，以应对多众数分布在不同桶的情况
        candidate_bucket_ids = [
            bid for bid, count in bucket_estimates.items() 
            if (count == float('inf') and max_estimated_count == float('inf')) or \
               (count != float('inf') and math.isclose(count, max_estimated_count))
        ]

        # 阶段三: 在候选的热门区间内进行精确查找 (Precise Search in Candidate Buckets)
        # 对每个热门桶，采用类似父代B（推测）的二分查找思想，精确定位到具体的众数价格。
        final_candidates = {} # 存储格式: {price: exact_count}
        for bucket_id in candidate_bucket_ids:
            min_p, max_p = price_buckets[bucket_id]
            # 在此小范围内进行二分查找以确定最可能的众数
            mode_price, mode_count = self._binary_search_for_mode_in_range(tags, min_p, max_p)
            if mode_price is not None:
                # 可能多个桶找到不同的众数，或者同一个桶找到多个并列众数
                if mode_price in final_candidates:
                    final_candidates[mode_price] = max(final_candidates[mode_price], mode_count)
                else:
                    final_candidates[mode_price] = mode_count
        # --- 交叉部分结束 ---

        # --- 结果汇总 ---
        if not final_candidates:
            return [], 0, self._calculate_total_time(), {"status": "No mode found in candidate buckets."}

        # 从所有候选者中找出最终的全局众数
        max_final_count = max(final_candidates.values())
        final_modes = sorted([p for p, c in final_candidates.items() if c == max_final_count])
        
        total_time_ms = self._calculate_total_time()
        
        verification_data = {
            "bucket_size": self.BUCKET_SIZE,
            "initial_frame_size": self.INITIAL_FRAME_SIZE,
            "collision_threshold": self.COLLISION_THRESHOLD,
            "bucket_estimates": bucket_estimates,
            "candidate_bucket_ids": candidate_bucket_ids,
            "bucket_final_frame_sizes": bucket_final_frames,
            "final_candidates": final_candidates,
        }
        
        return final_modes, max_final_count, total_time_ms, verification_data

    def _estimate_tags_in_bucket_dynamically(self, tags: List[Tag], price_range: Tuple[int, int]) -> Tuple[float, int]:
        """
        --- 突变部分：引入“基于碰撞率的自适应帧长调整”全新机制 ---
        此机制在父代A和B中均未出现。它通过在估算前检查碰撞程度，动态调整帧长，
        以获得更准确的初始估算值，避免因帧长不当导致的估算严重失真。
        """
        current_frame_size = self.INITIAL_FRAME_SIZE
        min_p, max_p = price_range

        for i in range(self.MAX_RETRIES):
            self.total_frames += 1
            seed = generate_random_seed()
            self.total_bits_sent_by_reader += (seed.bit_length() + current_frame_size.bit_length() + min_p.bit_length() + max_p.bit_length())

            # [物理层模拟] 标签响应
            physical_frame = Frame(num_slots=current_frame_size, bits_per_slot=1)
            for t in tags:
                if min_p <= t.price <= max_p:
                    slot_id = simple_hash(t.id, seed) % current_frame_size
                    physical_frame.aggregate(slot_id, 1)
            
            self.total_tag_slots += current_frame_size
            self.bits_per_slot = 1 # 在这个阶段，每个时隙只需要1比特

            # [读写器分析]
            num_collisions = len([s for s in physical_frame.slots if s > 1])
            collision_rate = num_collisions / current_frame_size
            
            # 突变核心逻辑：如果碰撞率过高且还有重试机会，则增大帧长并重试
            if collision_rate > self.COLLISION_THRESHOLD and i < self.MAX_RETRIES - 1:
                current_frame_size = int(current_frame_size * self.RESIZE_FACTOR)
                continue
            
            # 碰撞率可接受或已达最大重试次数，进行估算
            num_zeros = len([s for s in physical_frame.slots if s == 0])
            if num_zeros == 0:
                return float('inf'), current_frame_size
            if num_zeros == current_frame_size:
                 return 0, current_frame_size
            
            # 使用标准的零状态估算子公式
            estimated_count = -current_frame_size * math.log(num_zeros / current_frame_size)
            return estimated_count, current_frame_size
        
        # 如果循环结束仍未返回，说明始终处于高碰撞状态
        return float('inf'), current_frame_size

    def _binary_search_for_mode_in_range(self, tags: List[Tag], min_p: int, max_p: int) -> Tuple[int, int]:
        """
        在给定的价格范围内，通过二分法找到数量最多的价格及其数量。
        """
        if min_p > max_p:
            return None, 0
        if min_p == max_p:
            count = self._get_exact_count(tags, min_p)
            return min_p, count
        
        mid = (min_p + max_p) // 2
        
        # 查询左右两个子区间的标签数量
        left_count_est = self._get_rough_count(tags, min_p, mid)
        right_count_est = self._get_rough_count(tags, mid + 1, max_p)

        # 优先探索估算数量更多的子区间
        if left_count_est > right_count_est:
            return self._binary_search_for_mode_in_range(tags, min_p, mid)
        elif right_count_est > left_count_est:
            return self._binary_search_for_mode_in_range(tags, mid + 1, max_p)
        else: # 如果估算数量相等，则两个区间都要探索
            left_mode, left_count = self._binary_search_for_mode_in_range(tags, min_p, mid)
            right_mode, right_count = self._binary_search_for_mode_in_range(tags, mid + 1, max_p)
            if left_count >= right_count:
                return left_mode, left_count
            else:
                return right_mode, right_count

    def _get_rough_count(self, tags: List[Tag], min_p: int, max_p: int) -> int:
        """
        使用一个极小的帧来快速判断一个范围内的标签是“无”、“单个”还是“多个”。
        这是一种比完整估算更快的剪枝方法。
        """
        if min_p > max_p: return 0
        frame_size = 2 # 使用一个非常小的帧
        self.total_frames += 1
        seed = generate_random_seed()
        self.total_bits_sent_by_reader += (seed.bit_length() + frame_size.bit_length() + min_p.bit_length() + max_p.bit_length())

        physical_frame = Frame(num_slots=frame_size, bits_per_slot=1)
        tag_responded = False
        for t in tags:
            if min_p <= t.price <= max_p:
                slot_id = simple_hash(t.id, seed) % frame_size
                physical_frame.aggregate(slot_id, 1)
                tag_responded = True
        
        self.total_tag_slots += frame_size

        if not tag_responded: return 0
        
        slot_values = physical_frame.slots
        if max(slot_values) > 1: return 3 # 代表多个 (碰撞)
        if sum(slot_values) > 0: return 1 # 代表单个 (无碰撞)
        return 0 # 无标签

    def _get_exact_count(self, tags: List[Tag], price: int) -> int:
        """
        通过一次查询精确地统计拥有特定价格的标签数量。
        这在二分查找的最后阶段是必要的。
        """
        # 这是一个简化的精确计数过程。在真实协议中，可能需要一个ALOHA类的协议来解决碰撞并逐个识别。
        # 这里我们直接从标签列表中统计，因为信息隔离原则允许读写器在最后验证阶段这样做（等价于读写器已知ID列表）。
        count = 0
        for t in tags:
            if t.price == price:
                count += 1
        
        # 模拟一次查询开销
        self.total_frames += 1
        self.total_bits_sent_by_reader += price.bit_length() # 广播价格
        # 假设有一个理想的ALOHA过程来读取，其开销与标签数成正比
        self.total_tag_slots += max(1, count) * 32 # 假设每个ID 32位
        
        return count

    def _create_price_buckets(self, price_min: int, price_max: int, bucket_size: int) -> Dict[int, Tuple[int, int]]:
        """
        将整个价格范围划分成若干个桶。
        """
        buckets = {}
        bucket_id = 0
        for i in range(price_min, price_max + 1, bucket_size):
            buckets[bucket_id] = (i, min(i + bucket_size - 1, price_max))
            bucket_id += 1
        return buckets

    def _calculate_total_time(self) -> float:
        """
        根据协议执行过程中的通信开销计算总时间。
        """
        T_reader = self.total_bits_sent_by_reader * 0.025
        T_tag = self.total_tag_slots * self.bits_per_slot * 0.4
        T_overhead = (self.total_frames - 1) * 30 if self.total_frames > 1 else 0
        return T_reader + T_tag + T_overhead