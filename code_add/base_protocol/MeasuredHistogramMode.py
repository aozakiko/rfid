"""Reader-only mode baseline; saturated bins remain an estimation limitation."""
from base import IProtocol


class MeasuredHistogramMode(IProtocol):
    def execute_channel(self, channel, price_min, price_max):
        counts = channel.observe(round_index=0, slots_per_value=256)["counts"]
        largest = max(counts)
        return [price_min + i for i, value in enumerate(counts) if value == largest] if largest > 0 else []
