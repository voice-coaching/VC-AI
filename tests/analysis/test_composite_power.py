"""Fractional distance scoring and raw-metric provenance, without inference/GPT."""
from copy import deepcopy
from decimal import Decimal
import json
import math
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from voice_coach.backend_analysis import phone_rubric
from voice_coach.six_dimension.assessment import assess
from voice_coach.six_dimension.composite import (
    LEGACY_REVISION, REVISION, composite_assessment, exponential_score,
    load_composite_policy, validate_composite_policy,
)
from voice_coach.six_dimension.policy import load_policy

ROOT = Path(__file__).resolve().parents[2]
COEFFICIENT = 0.2231435513142097


def fixture(version='v3'):
    policy, _ = load_composite_policy(ROOT / f'configs/pronunciation_feedback/composite_exponential_{version}.json')
    reference = policy['reference']
    metrics = {name: stats['mean'] for name, stats in reference['statistics'].items()}
    metrics.update(metric_version=reference['metricVersion'], status='ok', f0_coverage=1.0)
    metrics.update(speech_rate=6.436781609195402, articulation_rate=6.603773584905661)
    comparison = {key: reference[name] for key, name in {
        'reference_version': 'version', 'reference_group': 'group',
        'reference_file_sha256': 'fileSha256', 'reference_aggregation': 'aggregation',
        'metric_version': 'metricVersion', 'analysis_window_source': 'analysisWindowSource',
        'reference_window_source': 'referenceWindowSource', 'extractor_compatibility': 'extractorCompatibility',
    }.items()}
    comparison['metrics'] = [dict(metric_name=name, user_value=metrics[name], reference_n=stats['n'],
        **{'reference_'+key: stats[key] for key in ('p10', 'p25', 'p50', 'p75', 'p90')})
        for name, stats in reference['statistics'].items()]
    core = dict(input_validation=dict(status='ACCEPT'), core_result=dict(prosody=dict(
        aihub71557_compatible_metrics_v2=dict(metrics=metrics, analysis_start_s=0.0, analysis_end_s=5.0),
        aihub71557_reference_comparison_v1=comparison)))
    base_policy, digest = load_policy(ROOT / 'configs/pronunciation_feedback/six_dimension_v1.json')
    base = assess(core, '가나다라마', {}, base_policy, digest)
    rows = [dict(criterionId=key, sampleCount=int(key in ('vowels', 'coverage')),
                 **{state: int(key in ('vowels', 'coverage') and state == 'matchedClear')
                    for state in phone_rubric.STATES}) for key, _, _ in phone_rubric.CRITERIA]
    scoring = phone_rubric.worksheet(rows, evidence_units=phone_rubric.EVIDENCE_UNITS)
    return base, core, scoring, policy


class CompositeDistancePower(unittest.TestCase):
    def test_specific_feedback_preserves_the_numerical_execution(self):
        from voice_coach.backend_analysis.canonical_harness import (
            COMPOSITE_POWER, COMPOSITE_POWER_INTERNAL, SPECIFIC_FEEDBACK, SPECIFIC_FEEDBACK_INTERNAL,
            SPECIFIC_FEEDBACK_V2, SPECIFIC_FEEDBACK_V2_INTERNAL,
            check_schema_registration, check_result_classification,
        )
        from voice_coach.backend_analysis.native_execution import APPROVED_NATIVE_EXECUTIONS
        for parent, candidate in ((COMPOSITE_POWER, SPECIFIC_FEEDBACK),
                                  (COMPOSITE_POWER_INTERNAL, SPECIFIC_FEEDBACK_INTERNAL),
                                  (COMPOSITE_POWER, SPECIFIC_FEEDBACK_V2),
                                  (COMPOSITE_POWER_INTERNAL, SPECIFIC_FEEDBACK_V2_INTERNAL)):
            self.assertEqual(parent.core_sha256, candidate.core_sha256)
            self.assertEqual(parent.model, candidate.model)
            self.assertEqual(parent.reasoning_effort, candidate.reasoning_effort)
            self.assertNotEqual(parent.prompt_sha256, candidate.prompt_sha256)
            self.assertIn((candidate.core_sha256, candidate.manifest_sha256), APPROVED_NATIVE_EXECUTIONS)
            check_schema_registration(candidate)
            check_result_classification(candidate)

    def test_new_harnesses_are_registered_for_both_lanes(self):
        from voice_coach.backend_analysis.canonical_harness import (
            YUNET_LIP, YUNET_LIP_INTERNAL, COMPOSITE_POWER, COMPOSITE_POWER_INTERNAL,
            check_schema_registration, check_result_classification,
        )
        from voice_coach.backend_analysis.native_execution import APPROVED_NATIVE_EXECUTIONS
        for parent, candidate in ((YUNET_LIP, COMPOSITE_POWER),
                                  (YUNET_LIP_INTERNAL, COMPOSITE_POWER_INTERNAL)):
            self.assertEqual(parent.core_sha256, candidate.core_sha256)
            self.assertEqual(parent.model, candidate.model)
            self.assertEqual(parent.reasoning_effort, candidate.reasoning_effort)
            self.assertTrue(candidate.audiovisual)
            self.assertIn((candidate.core_sha256, candidate.manifest_sha256), APPROVED_NATIVE_EXECUTIONS)
            check_schema_registration(candidate)
            check_result_classification(candidate)

    def test_fractional_power_at_exact_reference_distances(self):
        for distance, expected in [(0, 100), (1, 80), (8, 40.96), (27, 13.4217728)]:
            for sign in (-1, 1):
                with self.subTest(distance=distance, sign=sign):
                    score, evidence = exponential_score(sign*distance, 0, 1, COEFFICIENT)
                    self.assertAlmostEqual(float(score), expected, places=10)
                    self.assertEqual(evidence['distancePowerNumerator'], 2)
                    self.assertEqual(evidence['distancePowerDenominator'], 3)

    def test_score_is_symmetric_bounded_and_decreases_with_distance(self):
        previous = Decimal(101)
        for distance in (0, .001, .1, .5, 1, 2, 3, 8, 100, 1e308):
            positive, _ = exponential_score(distance, 0, 1, COEFFICIENT)
            negative, _ = exponential_score(-distance, 0, 1, COEFFICIENT)
            self.assertEqual(positive, negative)
            self.assertTrue(0 <= positive <= 100)
            self.assertLess(positive, previous)
            previous = positive

    def test_curve_crosses_old_policy_at_one_standard_deviation(self):
        for distance, relation in ((.5, 'less'), (1, 'equal'), (2, 'greater')):
            new, _ = exponential_score(distance, 0, 1, COEFFICIENT)
            old, _ = exponential_score(distance, 0, 1, COEFFICIENT, revision=LEGACY_REVISION)
            if relation == 'less': self.assertLess(new, old)
            elif relation == 'greater': self.assertGreater(new, old)
            else: self.assertEqual(new, old)

    def test_invalid_measurements_or_policy_never_become_full_scores(self):
        for args in ((None, 0, 1, COEFFICIENT), (math.nan, 0, 1, COEFFICIENT),
                     (1, 0, 0, COEFFICIENT), (1, 0, -1, COEFFICIENT), (1, 0, 1, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                exponential_score(*args)

    def test_v2_reproduces_the_recorded_speed_scores(self):
        for value, mean, std, expected in (
            (6.436781609195402, 5.076102297846724, .38302090244895665, 6.0),
            (6.603773584905661, 5.385001229547837, .4156146542379195, 14.7),
        ):
            _, row = exponential_score(value, mean, std, COEFFICIENT, revision=LEGACY_REVISION)
            self.assertEqual(row['score'], expected)
            self.assertNotIn('poweredNormalizedDeviation', row)

    def test_revision_formula_and_rational_power_must_agree(self):
        _, _, _, policy = fixture()
        for field, value in (('distancePower', {'numerator': 3, 'denominator': 2}),
                             ('revision', LEGACY_REVISION), ('formula', 'old formula')):
            bad = deepcopy(policy); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_composite_policy(bad)

    def test_only_requested_policy_fields_changed(self):
        _, _, _, new = fixture()
        _, _, _, old = fixture('v2')
        new = deepcopy(new)
        self.assertEqual(new.pop('distancePower'), {'numerator': 2, 'denominator': 3})
        new['formula'] = old['formula']; new['revision'] = old['revision']
        self.assertEqual(new, old)

    def test_base_scores_are_replaced_not_penalized_again(self):
        base, core, scoring, policy = fixture()
        originals = deepcopy((base, core, scoring, policy))
        result = composite_assessment(base, core, scoring, policy)
        for row in base['criteria']:
            row['score'] = 0.0
            for part in row['components']: part['score'] = 0.0
        again = composite_assessment(base, core, scoring, policy)
        self.assertEqual(result['criteria'], again['criteria'])
        self.assertEqual(result['auxiliary']['composite'], again['auxiliary']['composite'])
        self.assertEqual((core, scoring, policy), originals[1:])
        self.assertEqual(result['criteria'][0]['score'], scoring['RUBRIC_RESULT']['overallScore'])
        for row in result['auxiliary']['composite']['worksheet']:
            self.assertEqual(row['userValue'], core['core_result']['prosody']['aihub71557_compatible_metrics_v2']['metrics'][row['metric']])

    def test_private_schema_keeps_both_revisions_and_checks_new_evidence(self):
        schema = json.loads((ROOT/'docs/contracts/six_dimension_result_v1.schema.json').read_bytes())
        Draft202012Validator.check_schema(schema)
        auxiliary = schema['properties']['assessment']['properties']['auxiliary']['properties']['composite']
        for version, revision in (('v2', LEGACY_REVISION), ('v3', REVISION)):
            result = composite_assessment(*fixture(version))
            composite = result['auxiliary']['composite']
            self.assertEqual(composite['result']['rubricRevision'], revision)
            scoped = dict(auxiliary, **{'$defs': schema['$defs']})
            Draft202012Validator(scoped).validate(composite)
            if version == 'v3':
                bad = deepcopy(composite); bad['worksheet'][0]['distancePowerDenominator'] = 2
                self.assertFalse(Draft202012Validator(scoped).is_valid(bad))
                validator = Draft202012Validator({'allOf': schema['allOf'][-2:]})
                envelope = dict(assessment=result, compositeGrade=composite['result'])
                validator.validate(envelope)
                bad = deepcopy(envelope)
                del bad['assessment']['auxiliary']['composite']['worksheet'][0]['poweredNormalizedDeviation']
                self.assertFalse(validator.is_valid(bad))


if __name__ == '__main__': unittest.main()
