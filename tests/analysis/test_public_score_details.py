"""Public score transport must preserve server values, partial results and old clients."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator
from voice_coach.backend_analysis.canonical_result import _public_score, _public_coaching
from voice_coach.backend_analysis.browser_video_summary import summarize
from voice_coach.six_dimension.policy import CRITERIA

ROOT = Path(__file__).resolve().parents[2]
HARNESS = SimpleNamespace(scoring=True, six_dimension=True)


def expression():
    scores = [80, 0, 60, 70, 50, 40]
    weights = [40, 10, 10, 10, 15, 15]
    criteria = [dict(criterionId=key, label=key, score=score, status='SCORED', reasonCode='MEASURED')
                for key, score in zip(CRITERIA, scores)]
    grade = dict(validationStatus='EXPERT_REVIEW_PENDING', overallScore=58.5,
                 criteria=[dict(criterionId=key, score=score, weight=weight)
                           for key, score, weight in zip(CRITERIA, scores, weights)])
    return dict(sixDimension=dict(assessment=dict(criteria=criteria), compositeGrade=grade),
                scoring=dict(status='READY', result=dict(overallScore=80)), feedback='시험 피드백')


class PublicScoreDetails(unittest.TestCase):
    def validate(self, score, definition):
        for version in ('v4', 'v6'):
            schema = json.loads((ROOT/f'docs/contracts/runpod_result_{version}.schema.json').read_bytes())
            schema['$ref'] = '#/$defs/' + definition
            Draft202012Validator(schema).validate(score)

    def test_audio_scores_and_zero_pass_through_without_mutating_private_result(self):
        record = expression(); before = deepcopy(record)
        score = _public_score(record, None, HARNESS)
        self.assertEqual(score['overallScore'], 58.5)
        self.assertEqual(score['details']['criteria'][1]['score'], 0)
        self.assertEqual([r['criterionId'] for r in score['details']['criteria']], list(CRITERIA))
        self.assertEqual(record, before)
        self.assertNotIn('sixDimension', _public_coaching(record))
        self.validate(score, 'computedScore')

    def test_missing_metric_preserves_available_scores_and_null_total(self):
        record = expression(); six = record['sixDimension']
        six['compositeGrade']['overallScore'] = None
        six['compositeGrade']['criteria'][4]['score'] = None
        six['assessment']['criteria'][4].update(score=None, status='UNSCORABLE', reasonCode='F0_INSUFFICIENT_VALID_FRAMES')
        score = _public_score(record, None, HARNESS)
        self.assertIsNone(score['overallScore'])
        self.assertEqual(score['details']['criteria'][0]['score'], 80)
        self.assertIsNone(score['details']['criteria'][4]['score'])
        self.validate(score, 'unscoredScore')

    def test_audiovisual_total_and_audio_component_weights_are_separate(self):
        record = expression()
        record['sixDimension']['audiovisualGrade'] = dict(overallScore=55.1, audioScore=58.5,
            visualScore=50, audioWeight=60, visualWeight=40, aggregationMode='AUDIO_VISUAL')
        score = _public_score(record, None, HARNESS)
        self.assertEqual(score['overallScore'], 55.1)
        self.assertEqual(score['details']['audioScore'], 58.5)
        self.assertEqual(score['details']['audioWeight'], 60)
        self.assertEqual(score['details']['criteria'][0]['weight'], 40)
        self.validate(score, 'computedScore')

    def test_details_missing_never_blocks_existing_total_or_legacy_phone_criteria(self):
        record = expression(); del record['sixDimension']['assessment']
        score = _public_score(record, None, HARNESS)
        self.assertEqual(score['overallScore'], 58.5)
        self.assertNotIn('details', score)
        self.validate(score, 'computedScore')
        del record['sixDimension']['compositeGrade']
        record['scoring']['result']['criteria'] = [dict(criterionId='vowels', level=4)]
        legacy = _public_score(record, None, HARNESS)
        self.assertEqual(legacy['criteria'][0]['level'], 4)
        self.assertNotIn('details', legacy)

    def test_browser_summary_preserves_six_scores_and_excludes_private_measurements(self):
        score = _public_score(expression(), None, HARNESS)
        expected = deepcopy(score)
        score['details']['privateEvidence'] = {'frames': [1]}
        score['details']['criteria'][0]['rawGop'] = -1.0
        value = dict(schemaVersion='voice-coaching.independent-audiovisual-result.v1',
                     jobId='6cd07d62-c3b5-4a15-bc87-d339542f6ed5',
                     executionId='6cd07d62-c3b5-4a15-bc87-d339542f6ed5',
                     clientRequestId='6cd07d62-c3b5-4a15-bc87-d339542f6ed5',
                     status='COMPLETED', score=score,
                     visual=dict(score=None, scoreKind='NONE', reasonCodes=['NO_FACE'],
                                 phoneAssessments=[{'landmarks': [1]}]),
                     coaching=dict(feedback='마지막까지 고르게 읽으세요.'), failure=None)
        raw = json.dumps(value).encode(); projected, _ = summarize(raw)
        result = json.loads(projected)
        self.assertEqual(result['score'], expected)
        self.assertNotIn('phoneAssessments', result['visual'])
        self.assertNotIn('rawGop', projected.decode())
        self.assertNotIn('privateEvidence', projected.decode())
        schema = json.loads((ROOT/'docs/contracts/browser_video_summary_v1.schema.json').read_bytes())
        Draft202012Validator(schema).validate(result)
        self.assertEqual(json.loads(raw), value)
        del value['score']['details']
        historical, _ = summarize(json.dumps(value).encode())
        self.assertNotIn('details', json.loads(historical)['score'])
        self.assertEqual(json.loads(historical)['score']['overallScore'], 58.5)


if __name__ == '__main__':
    unittest.main()
