"""Built-in generic task profile declarations."""
from __future__ import annotations

from .registry import (
    SEMANTIC_EXECUTION,
    SEMANTIC_LEGACY,
    SEMANTIC_STATISTICAL,
    TaskProfile,
    register_profile,
)

SUPPORTED_DISTRIBUTIONS = (
    "single_peak",
    "multi_peak",
    "gaussian",
    "long_tail",
    "power_law",
    "uniform",
)
STATISTICAL_PROTOCOL = "statistical"
LEGACY_PROTOCOL = "legacy"
PROMPT_PROTOCOL = "prompt_exploration"


def _statistical_profile(
    *,
    profile_id: str,
    title: str,
    description: str,
    input_kind: str,
    output_kind: str,
    truth_builder: str,
    metric_kind: str,
    canonical_task: str,
    objective: str,
    output_contract: str,
    rules: tuple[str, ...],
) -> TaskProfile:
    return TaskProfile(
        profile_id=profile_id,
        title=title,
        description=description,
        semantic_scope=SEMANTIC_STATISTICAL,
        input_kind=input_kind,
        output_kind=output_kind,
        truth_builder=truth_builder,
        metric_kind=metric_kind,
        canonical_tasks=(canonical_task,),
        supported_distributions=SUPPORTED_DISTRIBUTIONS,
        supported_protocol_types=(STATISTICAL_PROTOCOL,),
        prompt_guidance={
            "objective": objective,
            "output_contract": output_contract,
        },
        validation_rules=rules,
    )


def _legacy_profile(
    *,
    profile_id: str,
    title: str,
    description: str,
    input_kind: str,
    output_kind: str,
    truth_builder: str,
    metric_kind: str,
    canonical_task: str,
    objective: str,
    output_contract: str,
    rules: tuple[str, ...],
) -> TaskProfile:
    return TaskProfile(
        profile_id=profile_id,
        title=title,
        description=description,
        semantic_scope=SEMANTIC_LEGACY,
        input_kind=input_kind,
        output_kind=output_kind,
        truth_builder=truth_builder,
        metric_kind=metric_kind,
        canonical_tasks=(canonical_task,),
        supported_distributions=SUPPORTED_DISTRIBUTIONS,
        supported_protocol_types=(LEGACY_PROTOCOL,),
        prompt_guidance={
            "objective": objective,
            "output_contract": output_contract,
        },
        validation_rules=rules,
    )


BUILTIN_PROFILES: tuple[TaskProfile, ...] = (
    _legacy_profile(
        profile_id="mode_query",
        title="Discrete mode query",
        description="Find the most frequent discrete attribute values under anonymous RFID collection constraints.",
        input_kind="categorical_values",
        output_kind="value_list",
        truth_builder="legacy_mode_truth",
        metric_kind="set",
        canonical_task="legacy_mode_query",
        objective="Estimate the true mode set without exposing individual tag values.",
        output_contract="Return a list containing candidate mode values.",
        rules=("output_must_be_a_list", "mode_scores_must_not_reveal_individual_values"),
    ),
    _statistical_profile(
        profile_id="mean_estimation",
        title="Numeric mean estimation",
        description="Estimate the population mean of numeric tag attributes.",
        input_kind="numeric_values",
        output_kind="scalar_list",
        truth_builder="statistical_mean",
        metric_kind="relative_error",
        canonical_task="mean",
        objective="Estimate the population mean while controlling communication cost.",
        output_contract="Return a one-element list containing the estimated mean.",
        rules=("output_must_be_a_one_element_numeric_list", "estimate_must_be_finite"),
    ),
    _statistical_profile(
        profile_id="variance_estimation",
        title="Numeric variance estimation",
        description="Estimate population variance or numeric dispersion.",
        input_kind="numeric_values",
        output_kind="scalar_list",
        truth_builder="statistical_variance",
        metric_kind="relative_error",
        canonical_task="variance",
        objective="Estimate numeric dispersion for stability and anomaly monitoring.",
        output_contract="Return a one-element list containing the estimated variance.",
        rules=("output_must_be_a_one_element_numeric_list", "estimate_must_be_finite_and_non_negative"),
    ),
    _legacy_profile(
        profile_id="median_quantile",
        title="Median and quantile estimation",
        description="Estimate a requested quantile from a possibly skewed numeric population.",
        input_kind="numeric_values",
        output_kind="scalar_list",
        truth_builder="legacy_quantile_truth",
        metric_kind="relative_error",
        canonical_task="legacy_median_quantile",
        objective="Estimate the requested quantile without assuming a symmetric distribution.",
        output_contract="Return a one-element list containing the requested quantile estimate.",
        rules=("output_must_be_a_one_element_numeric_list", "quantile_parameter_must_be_in_unit_interval"),
    ),
    _statistical_profile(
        profile_id="cardinality_estimation",
        title="Tag cardinality estimation",
        description="Estimate the number of distinct tags or inventory members.",
        input_kind="presence_values",
        output_kind="scalar_list",
        truth_builder="statistical_cardinality",
        metric_kind="relative_error",
        canonical_task="cardinality",
        objective="Estimate population cardinality under collision and anonymous aggregation constraints.",
        output_contract="Return a one-element list containing the estimated cardinality.",
        rules=("output_must_be_a_one_element_numeric_list", "estimate_must_be_non_negative"),
    ),
    _legacy_profile(
        profile_id="missing_stocktaking",
        title="Missing stocktaking",
        description="Identify likely missing inventory identifiers under a high-loss RFID scenario.",
        input_kind="tag_ids_and_presence",
        output_kind="id_list",
        truth_builder="legacy_missing_truth",
        metric_kind="set",
        canonical_task="legacy_missing_stocktaking",
        objective="Recover a candidate set of missing inventory identifiers from aggregate evidence.",
        output_contract="Return a list of candidate missing identifiers.",
        rules=("output_must_be_an_id_list", "candidate_ids_must_be_unique"),
    ),
    TaskProfile(
        profile_id="custom_task",
        title="Custom task",
        description="Explore an arbitrary user-defined task under an explicit execution and output contract.",
        semantic_scope=SEMANTIC_EXECUTION,
        input_kind="declared_by_task_text",
        output_kind="declared_by_output_contract",
        truth_builder="not_available",
        metric_kind="not_available",
        canonical_tasks=("custom_task",),
        supported_distributions=SUPPORTED_DISTRIBUTIONS,
        supported_protocol_types=(STATISTICAL_PROTOCOL, LEGACY_PROTOCOL, PROMPT_PROTOCOL),
        prompt_guidance={
            "objective": "State the business target, input, output contract, accuracy expectations, and constraints.",
            "output_contract": "Follow the user-supplied contract exactly; semantic accuracy is not automatically evaluated.",
        },
        validation_rules=(
            "task_text_must_be_specific",
            "output_contract_must_be_declared",
            "accuracy_must_not_be_reported_without_a_truth_definition",
        ),
    ),
    _statistical_profile(
        profile_id="top_k",
        title="Top-k value query",
        description="Return the highest-frequency values under strict top-k set evaluation.",
        input_kind="categorical_values",
        output_kind="ranked_value_list",
        truth_builder="statistical_top_k",
        metric_kind="set",
        canonical_task="top_k",
        objective="Recover the highest-frequency values with explicit precision and recall evaluation.",
        output_contract="Return a list of at most k candidate values ranked by estimated frequency.",
        rules=("output_must_be_a_list", "k_must_be_positive", "returned_values_must_be_unique"),
    ),
)

for profile in BUILTIN_PROFILES:
    register_profile(profile)
