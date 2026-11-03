"""Deterministic constrained Pareto selection with auditable diversity decisions."""
import ast
import hashlib
import math

OBJECTIVES = {"quality_only", "precision_constrained_cost", "pareto_diversity"}


def structural_hash(source, protocol_name):
    """Ignore formatting, comments, docstrings and the candidate class name."""
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == protocol_name:
                node.name = "_Candidate"
            if isinstance(node, ast.Name) and node.id == protocol_name:
                node.id = "_Candidate"
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                    node.body.pop(0)
        text = ast.dump(tree, include_attributes=False)
    except (SyntaxError, UnicodeError):
        text = str(source)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector(row):
    return (row["quality_pass_rate"], row["quality_score"], -row["mean_communication_cost_ms"])


def dominates(a, b):
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def fronts(names, rows):
    remaining, layers = list(names), []
    while remaining:
        front = [name for name in remaining if not any(dominates(vector(rows[other]), vector(rows[name])) for other in remaining if other != name)]
        layers.append(front)
        remaining = [name for name in remaining if name not in front]
    return layers


def crowding(front, rows):
    distance = {name: 0. for name in front}
    for axis in range(3):
        ordered = sorted(front, key=lambda name: vector(rows[name])[axis])
        low, high = vector(rows[ordered[0]])[axis], vector(rows[ordered[-1]])[axis]
        if low == high:
            continue
        distance[ordered[0]] = distance[ordered[-1]] = math.inf
        for index in range(1, len(ordered) - 1):
            distance[ordered[index]] += (vector(rows[ordered[index+1]])[axis] - vector(rows[ordered[index-1]])[axis]) / (high-low)
    return distance


def select(names, evaluations, size, *, objective="quality_only", minimum_pass_rate=1., incumbents=None):
    if objective not in OBJECTIVES or type(size) is not int or size < 1:
        raise ValueError("invalid selection objective or population size")
    names = list(dict.fromkeys(names))
    rows = {name: evaluations[name]["summary"] for name in names}
    comparable = [name for name in names if rows[name]["selection_eligible"]]
    if len({tuple(rows[name]["dataset_hashes"]) for name in comparable}) > 1:
        raise ValueError("candidate datasets differ; population selection refused")
    if objective != "quality_only" and len({rows[name].get("channel_model_id") for name in comparable if rows[name].get("communication_eligible")}) > 1:
        raise ValueError("channel models differ; cost selection refused")
    incumbents = set(names if incumbents is None else incumbents)
    original = {name: index for index, name in enumerate(names)}
    decisions = {name: {"name": name, "retained": False, "reason": "not_selected",
        "selection_eligible": bool(rows[name]["selection_eligible"]), "feasible": False,
        "quality_pass_rate": rows[name].get("quality_pass_rate"), "quality_score": rows[name].get("quality_score"),
        "mean_communication_cost_ms": rows[name].get("mean_communication_cost_ms"),
        "pareto_rank": None, "crowding_distance": None, "boundary": False, "duplicate_of": None} for name in names}
    feasible = [name for name in comparable if rows[name].get("communication_eligible") and rows[name]["quality_pass_rate"] >= minimum_pass_rate]
    for name in feasible:
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector(rows[name])) or rows[name]["mean_communication_cost_ms"] <= 0:
            raise ValueError("non-finite or invalid measured objectives")
        decisions[name]["feasible"] = True
    unique, duplicates, seen = [], [], {}
    for name in sorted(names, key=lambda name: (name not in incumbents, original[name])):
        fingerprint = rows[name].get("structural_sha256") or rows[name].get("source_sha256") or name
        if objective == "pareto_diversity" and fingerprint in seen:
            decisions[name].update(reason="duplicate_structure", duplicate_of=seen[fingerprint])
            duplicates.append(name)
        else:
            seen[fingerprint] = name
            unique.append(name)
    layers = fronts([name for name in feasible if name in unique], rows) if objective == "pareto_diversity" else []
    ranked = []
    if objective == "pareto_diversity":
        for rank, front in enumerate(layers, 1):
            distances = crowding(front, rows)
            # A single representative is chosen before holdout by cost, then quality.
            order = (lambda name: (rows[name]["mean_communication_cost_ms"], -rows[name]["quality_score"], name not in incumbents, original[name])) if size == 1 else (
                lambda name: (-distances[name], name not in incumbents, original[name]))
            for name in sorted(front, key=order):
                decisions[name].update(pareto_rank=rank, crowding_distance=distances[name] if math.isfinite(distances[name]) else None,
                    boundary=math.isinf(distances[name]), reason="pareto_front" if rank == 1 else "dominated_front")
                ranked.append(name)
        exploration = [name for name in comparable if name in unique and name not in feasible]
        exploration.sort(key=lambda name: (-rows[name]["quality_pass_rate"], -rows[name]["quality_score"], name not in incumbents, original[name]))
        ranked += exploration
        for name in exploration:
            decisions[name]["reason"] = "exploration_below_precision" if rows[name].get("communication_eligible") else "exploration_unmeasured"
    elif objective == "precision_constrained_cost":
        ranked = sorted(comparable, key=lambda name: (0, rows[name]["mean_communication_cost_ms"], -rows[name]["quality_score"]) if name in feasible else (1, -rows[name]["quality_pass_rate"], -rows[name]["quality_score"]))
    else:
        ranked = sorted(comparable, key=lambda name: (-rows[name]["quality_pass_rate"], -rows[name]["quality_score"]))
    selected = ranked[:size]
    fallback = [name for name in names if name not in comparable and name in incumbents and name not in duplicates]
    selected += fallback[:max(0, size - len(selected))]
    for name in names:
        item = decisions[name]
        item["retained"] = name in selected
        if name in duplicates:
            continue
        if not rows[name]["selection_eligible"]:
            item["reason"] = "unranked_source_parent" if name in selected else "validation_failed"
        elif objective != "pareto_diversity":
            item["reason"] = "ranked_by_" + objective if name in selected else "population_capacity"
        elif name not in selected and item["reason"] == "pareto_front":
            item["reason"] = "crowding_capacity"
    return {"schema_version": "1.0", "objective": objective, "population": selected,
        "fronts": layers, "decisions": [decisions[name] for name in names], "duplicates_removed": len(duplicates),
        "unique_count": len(unique), "feasible_count": len([name for name in feasible if name not in duplicates]),
        "diversity_policy": "normalized crowding; structural deduplication; stable incumbent ties" if objective == "pareto_diversity" else "stable incumbent ties",
        "representative_policy": "maximum semantic pass rate then quality" if objective == "quality_only" else
            "minimum measured cost among precision-feasible first-front candidates; quality breaks cost ties; infeasible candidates remain exploratory"}


def signature(population, evaluations, minimum_pass_rate):
    report = select(population, evaluations, max(1, len(population)), objective="pareto_diversity", minimum_pass_rate=minimum_pass_rate)
    return sorted(set(vector(evaluations[name]["summary"]) for name in (report["fronts"][0] if report["fronts"] else [])))
