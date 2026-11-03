"""Worker-owned anonymous channel for generated reader algorithms.

The facade exposes aggregate observations only. Costs are charged by the worker,
including repeated observations, using an explicit software model (not RF time).
"""
from types import SimpleNamespace

from stat_protocols.channel import HistogramChannel
from stat_protocols.contracts import CostModel

MODEL_ID = "histogram_or_v1_slot001"
MODEL = CostModel(slot_ms=.001)
FIELDS = ("reader_bits", "tag_bits", "slots", "frames", "rounds")


def create_channel(tags, low, high, seed):
    if not 0 <= low <= high < 2**31 or high - low + 1 > 10000:
        raise ValueError("measured channel domain must contain 1..10000 nonnegative bins")
    if any(not low <= tag.price <= high for tag in tags):
        raise ValueError("tag outside channel domain")
    channel = HistogramChannel(tags, SimpleNamespace(price_min=low, price_max=high, seed=seed))
    events = []

    class ReaderChannel:
        __slots__ = ()

        def __getattribute__(self, name):
            if name != "observe":
                raise PermissionError("channel_attribute_not_allowed")
            return object.__getattribute__(self, name)

        def observe(self, round_index=0, slots_per_value=256):
            if type(round_index) is not int or not 0 <= round_index <= 1000000:
                raise ValueError("round_index must be an integer in 0..1000000")
            if type(slots_per_value) is not int or not 2 <= slots_per_value <= 32768:
                raise ValueError("slots_per_value must be an integer in 2..32768")
            if len(events) >= 128:
                raise ValueError("channel event limit exceeded")
            row = channel.observe(round_index, slots_per_value)
            events.append({**{key: getattr(row, key) for key in FIELDS},
                           "round_index": round_index, "slots_per_value": slots_per_value})
            return {"counts": row.counts.tolist(), "variance": row.variance.tolist(),
                    "saturated_bins": row.saturated_bins}

    def evidence():
        counters = {key: sum(event[key] for event in events) for key in FIELDS}
        return {"model_id": MODEL_ID, "cost_model": MODEL.to_dict(),
                "communication_cost_ms": MODEL.estimate_ms(**counters),
                "counters": counters, "events": events,
                "frame_size": max((event["slots_per_value"] for event in events), default=0)}

    return ReaderChannel(), evidence
