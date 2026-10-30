from __future__ import annotations

import json
import unittest

from generic_tasks import (
    SEMANTIC_EXECUTION,
    SEMANTIC_LEGACY,
    SEMANTIC_STATISTICAL,
    TaskProfile,
    UnknownProfileError,
    deserialize_profile,
    get_profile,
    list_profiles,
    resolve_canonical_tasks,
    serialize_profile,
    validate_profile_id,
)


EXPECTED_PROFILE_IDS = (
    "mode_query",
    "mean_estimation",
    "variance_estimation",
    "median_quantile",
    "cardinality_estimation",
    "missing_stocktaking",
    "custom_task",
    "top_k",
)

REQUIRED_FIELDS = {
    "profile_id",
    "title",
    "description",
    "semantic_scope",
    "input_kind",
    "output_kind",
    "truth_builder",
    "metric_kind",
    "canonical_tasks",
    "supported_distributions",
    "supported_protocol_types",
    "prompt_guidance",
    "validation_rules",
}


class GenericTaskContractTests(unittest.TestCase):
    def test_registration_exposes_all_required_profiles_and_fields(self):
        profiles = list_profiles()
        self.assertEqual(tuple(profile.profile_id for profile in profiles), EXPECTED_PROFILE_IDS)
        for profile in profiles:
            self.assertIsInstance(profile, TaskProfile)
            self.assertTrue(REQUIRED_FIELDS.issubset(profile.to_dict()))
            self.assertEqual(validate_profile_id(profile.profile_id), profile.profile_id)
            self.assertIs(get_profile(profile.profile_id), profile)

    def test_unknown_task_is_rejected_explicitly(self):
        for operation in (
            lambda: validate_profile_id("unknown_task"),
            lambda: get_profile("unknown_task"),
            lambda: resolve_canonical_tasks("unknown_task"),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(UnknownProfileError):
                    operation()
                with self.assertRaises((KeyError, ValueError)):
                    operation()

    def test_custom_task_cannot_claim_accuracy(self):
        custom_task = get_profile("custom_task")
        self.assertEqual(custom_task.semantic_scope, SEMANTIC_EXECUTION)
        self.assertNotEqual(custom_task.metric_kind, "accuracy")
        self.assertNotIn("accuracy", custom_task.metric_kind.lower())

        invalid = custom_task.to_dict()
        invalid["metric_kind"] = "accuracy"
        with self.assertRaisesRegex(ValueError, "must not declare an accuracy metric"):
            TaskProfile.from_mapping(invalid)

    def test_statistical_and_legacy_profiles_resolve_expected_canonical_tasks(self):
        expected_mapping = {
            "mode_query": ("legacy_mode_query",),
            "mean_estimation": ("mean",),
            "variance_estimation": ("variance",),
            "median_quantile": ("legacy_median_quantile",),
            "cardinality_estimation": ("cardinality",),
            "missing_stocktaking": ("legacy_missing_stocktaking",),
            "custom_task": ("custom_task",),
            "top_k": ("top_k",),
        }
        for profile_id, canonical_tasks in expected_mapping.items():
            with self.subTest(profile_id=profile_id):
                self.assertEqual(resolve_canonical_tasks(profile_id), list(canonical_tasks))

        self.assertEqual(get_profile("mean_estimation").semantic_scope, SEMANTIC_STATISTICAL)
        self.assertEqual(get_profile("variance_estimation").semantic_scope, SEMANTIC_STATISTICAL)
        self.assertEqual(get_profile("cardinality_estimation").semantic_scope, SEMANTIC_STATISTICAL)
        self.assertEqual(get_profile("top_k").semantic_scope, SEMANTIC_STATISTICAL)
        self.assertEqual(get_profile("mode_query").semantic_scope, SEMANTIC_LEGACY)
        self.assertEqual(get_profile("median_quantile").semantic_scope, SEMANTIC_LEGACY)
        self.assertEqual(get_profile("missing_stocktaking").semantic_scope, SEMANTIC_LEGACY)

        self.assertEqual(
            resolve_canonical_tasks(("mean_estimation", "top_k", "mean_estimation")),
            ["mean", "top_k"],
        )

    def test_profiles_are_json_serializable_and_round_trip(self):
        for profile in list_profiles():
            with self.subTest(profile_id=profile.profile_id):
                payload = profile.to_dict()
                json.dumps(payload, ensure_ascii=False, allow_nan=False)
                self.assertEqual(TaskProfile.from_mapping(payload), profile)

                encoded = serialize_profile(profile)
                self.assertEqual(deserialize_profile(encoded), profile)


if __name__ == "__main__":
    unittest.main()
