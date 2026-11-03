"""Reader-only mean baseline for the platform's anonymous software channel."""
from base import IProtocol


class MeasuredHistogramMean(IProtocol):
    def execute_channel(self, channel, price_min, price_max):
        counts = channel.observe(round_index=0, slots_per_value=256)["counts"]
        total = sum(counts)
        return [sum((price_min + i) * value for i, value in enumerate(counts)) / total] if total else []
