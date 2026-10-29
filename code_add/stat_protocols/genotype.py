"""Small statistics-only operator adapter, not a replacement evolution engine."""
from __future__ import annotations


def compile_candidate(record, *, name="llm_random"):
    from .protocols import SharedSketchProtocol
    for key in ("candidate_id", "model", "prompt_hash", "response_hash"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError(f"missing provenance: {key}")
    genotype = record.get("genotype")
    if not isinstance(genotype, dict) or set(genotype) - {"operator", "rounds", "slots_per_value", "threshold"}:
        raise ValueError("unknown genotype fields")
    if genotype.get("operator") != "anonymous_histogram":
        raise ValueError("operator must be anonymous_histogram")
    rounds = genotype.get("rounds")
    slots = genotype.get("slots_per_value")
    threshold = genotype.get("threshold")
    if type(rounds) is not int or not 1 <= rounds <= 4:
        raise ValueError("rounds must be integer 1..4")
    if type(slots) is not int or slots not in (16, 32, 64, 128, 256, 512):
        raise ValueError("invalid slots_per_value")
    if threshold is not None and (type(threshold) not in (int, float) or not .25 <= threshold <= 4):
        raise ValueError("invalid threshold")
    return SharedSketchProtocol(rounds=rounds, threshold=threshold, slots_per_value=slots, name=name)


BASELINE_PROMPT = '''Generate ONE random statistical RFID candidate. No fitness selection,
no crossover, no repair and no benchmark feedback. Return only a JSON object
with these fields: operator="anonymous_histogram", rounds (integer 1..4),
slots_per_value (one of 16,32,64,128,256,512), threshold (null or number 0.25..4).
The shared anonymous OR occupancy histogram estimates mean, population variance,
present-tag population cardinality, and Top-k on public integer domain 1..1000.
Frame costs are counted by the simulator, not supplied by the candidate.
This is a one-shot LLM baseline in a restricted statistics operator space.'''
