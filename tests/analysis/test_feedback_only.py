"""Provider omission cannot alter scores, order, evidence, or original payloads."""
from copy import deepcopy
import unittest

from jsonschema import ValidationError
from voice_coach.six_dimension.pipeline import expand_feedback_response, validate_coaching
from voice_coach.six_dimension.policy import CRITERIA


class FeedbackOnly(unittest.TestCase):
    def setUp(self):
        self.assessment = dict(criteria=[dict(criterionId=key, score=10*i, evidenceIds=['e'+str(i)])
                                        for i,key in enumerate(CRITERIA)],
                               selectedCriterionIds=list(CRITERIA[:3]))
        self.candidate = dict(feedback=[dict(criterionId=key, evidenceIds=['e'+str(i)],
            observation='확인된 측정 방향에 따른 연습입니다.', practice='대상 → 음절 간격을 조절해 읽으세요.')
            for i,key in enumerate(CRITERIA[:3])])

    def test_expansion_preserves_original_server_scores_and_validates_full_contract(self):
        before = deepcopy(self.candidate)
        result = expand_feedback_response(self.candidate, self.assessment)
        validate_coaching(result, self.assessment)
        self.assertEqual([r['score'] for r in result['assessments']], [0,10,20,30,40,50])
        self.assertEqual(result['feedback'], before['feedback'])
        self.assertEqual(self.candidate, before)
        result['feedback'][0]['evidenceIds'].append('changed')
        self.assertEqual(self.candidate, before)

    def test_score_objects_are_owned_by_server_and_detached_from_mutable_output(self):
        legacy = dict(overallScore=91.2, criteria=[dict(level=3)])
        composite = dict(overallScore=80.1)
        visual = dict(grade=dict(overallScore=82.3), targets=[], input={})
        # Audio targets remain selected when there are no visual targets.
        result = expand_feedback_response(self.candidate, self.assessment, legacy, composite, visual)
        self.assertEqual(result['legacyGrade'], legacy)
        self.assertEqual(result['compositeGrade'], composite)
        self.assertEqual(result['audiovisualGrade'], visual['grade'])
        result['legacyGrade']['criteria'][0]['level'] = 0
        self.assertEqual(legacy['criteria'][0]['level'], 3)

    def test_provider_cannot_supply_or_change_scores(self):
        for key in ['assessments','legacyGrade','compositeGrade','audiovisualGrade','overallScore']:
            candidate = dict(self.candidate, **{key: 0})
            with self.subTest(key=key), self.assertRaises(ValidationError):
                expand_feedback_response(candidate, self.assessment)

    def test_wrong_target_order_or_foreign_evidence_fails(self):
        candidate = deepcopy(self.candidate)
        candidate['feedback'].reverse()
        with self.assertRaises(ValidationError):
            expand_feedback_response(candidate, self.assessment)
        candidate = deepcopy(self.candidate)
        candidate['feedback'][0]['evidenceIds'] = ['foreign']
        with self.assertRaises(ValidationError):
            expand_feedback_response(candidate, self.assessment)

    def test_latency_harnesses_keep_existing_model_and_contract_classification(self):
        from voice_coach.backend_analysis.canonical_harness import (
            VOICE_LATENCY, VOICE_LATENCY_INTERNAL, ACTIONABLE_COACHING,
            ACTIONABLE_COACHING_INTERNAL, check_schema_registration, check_result_classification)
        for current,parent in [(VOICE_LATENCY,ACTIONABLE_COACHING),
                               (VOICE_LATENCY_INTERNAL,ACTIONABLE_COACHING_INTERNAL)]:
            self.assertEqual((current.model,current.reasoning_effort,current.service_tier),
                             (parent.model,parent.reasoning_effort,parent.service_tier))
            check_schema_registration(current)
            check_result_classification(current)
