"""Regression for the real 97.2 score rejected after successful voice/GPT work."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from voice_coach.backend_analysis import phone_rubric

spec = importlib.util.spec_from_file_location('voice_coach.six_dimension._decimal_bridge',
    ROOT / 'deploy/runpod/harnesses/six_dimension_bridge.py')
bridge_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_module)


class BoundaryBridge(bridge_module.Bridge):
    def native_rubric_policy(self):
        return None

    def read(self, name):
        if name == 'llm_output.schema.json':
            return {'properties': {'scoring': {'type': 'object'}}}
        return {}


class PhoneRubricDecimalRegression(unittest.TestCase):
    def setUp(self):
        rows = []
        for key, _, _ in phone_rubric.CRITERIA[:-1]:
            counts = dict.fromkeys(phone_rubric.STATES, 0)
            counts['matchedClear'] = 0 if key == 'affricates' else 4 if key == 'fricatives' else 1
            counts['matchedFlagged'] = 1 if key == 'fricatives' else 0
            rows.append(dict(criterionId=key, sampleCount=sum(counts.values()), **counts))
        counts = {s: sum(r[s] for r in rows) for s in phone_rubric.STATES}
        rows.append(dict(criterionId='coverage', sampleCount=sum(counts.values()), **counts))
        self.grade = phone_rubric.worksheet(rows, evidence_units=phone_rubric.EVIDENCE_UNITS)['RUBRIC_RESULT']
        self.bridge = BoundaryBridge(ROOT, expected_lock_sha256='unused-in-this-boundary-test')

    def verify(self, record):
        with patch.object(bridge_module, 'rubric_context', return_value={'RUBRIC_RESULT': self.grade}):
            self.bridge.verify_scoring({'input_validation': {'status': 'ACCEPT'}}, record)

    def test_service_decimal_codec_accepts_the_same_97_2_grade(self):
        self.assertEqual(self.grade['overallScore'], 97.2)
        record = json.loads(json.dumps({'status': 'READY', 'result': self.grade}), parse_float=Decimal)
        self.assertNotEqual(record['result']['overallScore'], self.grade['overallScore'])
        self.verify(record)

    def test_changed_numeric_score_is_rejected(self):
        record = json.loads(json.dumps({'status': 'READY', 'result': self.grade}), parse_float=Decimal)
        record['result']['overallScore'] = Decimal('97.3')
        with self.assertRaisesRegex(ValueError, 'SCORING_RESPONSE_TOTAL'):
            self.verify(record)

    def test_changed_phone_level_is_rejected(self):
        record = json.loads(json.dumps({'status': 'READY', 'result': self.grade}), parse_float=Decimal)
        record['result']['criteria'][0]['level'] = 3
        with self.assertRaisesRegex(ValueError, 'SCORING_RESPONSE_LEVEL'):
            self.verify(record)


if __name__ == '__main__':
    unittest.main()
