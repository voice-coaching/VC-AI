"""Speech variation is scored; malformed evidence remains a failed calculation."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from voice_coach.six_dimension.native_rubric import rubric_context

RUBRIC=json.loads((ROOT/'configs/pronunciation_feedback/phone_rubric_20261003_v1.json').read_bytes())
V1=json.loads((ROOT/'configs/pronunciation_feedback/native_phone_rubric_evidence_v1.json').read_bytes())
V2=json.loads((ROOT/'configs/pronunciation_feedback/native_phone_rubric_evidence_v2.json').read_bytes())


def core(*,delete=(),substitute=(),insert=False,ambiguous=False,compound=False):
    # Synthetic protocol fixture, never a user analysis or acoustic inference.
    phones=[('ㄱ','onset','G',9),('ㅏ','nucleus','A',0),('ㄴ','coda','N',17),
            ('ㅙ','nucleus','oE',35) if compound else ('ㅏ','nucleus','A',0)]
    samples=[]
    observed=[]
    alignment=[]
    for i,(phone,role,token,token_id) in enumerate(phones):
        samples.append(dict(index=i,sourceIndex=i,phone=phone,role=role,wordIndex=0,
            syllableIndex=i,token=token,tokenId=token_id,indistinguishablePhones=[phone],
            gop=-1.0,logReference=-10.0,logAlternatives=-9.0))
        if i in delete:
            alignment.append(dict(expectedIndex=i,observedIndex=None,operation='DELETION'))
        else:
            index=len(observed)
            observed.append(dict(is_phone=True,token='M' if i in substitute else token,
                                 token_id=16 if i in substitute else token_id))
            alignment.append(dict(expectedIndex=i,observedIndex=index,
                operation='SUBSTITUTION' if i in substitute else 'MATCH'))
    if insert:
        alignment.append(dict(expectedIndex=None,observedIndex=len(observed),operation='INSERTION'))
        observed.append(dict(is_phone=True,token='S',token_id=23))
    return dict(input_validation=dict(status='ACCEPT'),expected_phone_count=5 if compound else 4,
        native=dict(ctc_runs=deepcopy(observed)),phonemeAssessment=dict(revision='gop-ctc-af-sd-native-v1',
            samples=samples,observed=observed,alignment=alignment,alignmentAmbiguous=ambiguous,
            excludedSilentOnsetSourceIndices=[],coverage=dict(sampleCount=4,deletedCount=len(delete),
                evidenceUnits=4*(4-len(delete)),possibleUnits=16)))


def grade(document,policy=V2):
    return rubric_context(document,RUBRIC,policy,{})


class NativeRubricVariation(unittest.TestCase):
    def test_deletion_lowers_grade_and_coverage_instead_of_cancelling(self):
        result=grade(core(delete=(2,)))
        self.assertEqual(result['RUBRIC_RESULT']['overallScore'],72.7)
        coverage=result['INPUT_DATA']['criteria'][-1]
        self.assertEqual(coverage['deletedClear'],1)
        self.assertEqual(coverage['evidenceUnits'],12)
        self.assertEqual(result['RUBRIC_RESULT']['criteria'][-1]['level'],2)

    def test_substitution_uses_existing_rubric_units(self):
        self.assertEqual(grade(core(substitute=(2,)))['RUBRIC_RESULT']['overallScore'],90.9)

    def test_all_recorded_deletions_are_a_zero_score(self):
        result=grade(core(delete=(0,1,2,3)))
        self.assertEqual(result['RUBRIC_RESULT']['overallScore'],0.0)
        self.assertEqual(result['RUBRIC_EVIDENCE']['status'],'READY')

    def test_reference_representation_difference_is_not_a_deletion(self):
        result=grade(core(compound=True))
        self.assertEqual(result['RUBRIC_RESULT']['overallScore'],100.0)
        self.assertEqual(result['RUBRIC_EVIDENCE']['referenceCounts']['legacyDetectorExpectedCount'],5)
        self.assertEqual(result['RUBRIC_EVIDENCE']['referenceCounts']['gopReferenceSampleCount'],4)
        self.assertEqual(result['RUBRIC_EVIDENCE']['alignment']['operationCounts']['DELETION'],0)

    def test_reported_ambiguous_alignment_is_scored_with_warning(self):
        result=grade(core(delete=(2,),ambiguous=True))
        self.assertEqual(result['RUBRIC_RESULT']['overallScore'],72.7)
        self.assertEqual(result['RUBRIC_EVIDENCE']['warnings'],['MODEL_TOKEN_ALIGNMENT_AMBIGUOUS'])
        self.assertFalse(result['RUBRIC_EVIDENCE']['alignment']['confirmedLocalDeletionClaimsAllowed'])

    def test_reference_difference_ambiguity_and_deletion_can_coexist(self):
        result=grade(core(delete=(2,),ambiguous=True,compound=True))
        self.assertEqual(result['RUBRIC_RESULT']['overallScore'],72.7)
        self.assertEqual(result['RUBRIC_EVIDENCE']['alignment']['operationCounts']['DELETION'],1)
        self.assertEqual(result['RUBRIC_EVIDENCE']['warnings'],['MODEL_TOKEN_ALIGNMENT_AMBIGUOUS'])

    def test_insertion_preserves_the_existing_coverage_rule(self):
        result=grade(core(insert=True))
        self.assertEqual(result['RUBRIC_RESULT']['overallScore'],100.0)
        self.assertEqual(result['RUBRIC_EVIDENCE']['alignment']['operationCounts']['INSERTION'],1)

    def test_missing_alignment_rows_are_not_invented(self):
        document=core()
        document['phonemeAssessment']['alignment'].pop()
        result=grade(document)
        self.assertEqual(result['RUBRIC_RESULT'],{'status':'unscorable'})
        self.assertEqual(result['SCORING_FAILURE'],'NATIVE_RUBRIC_ALIGNMENT_INCOMPLETE')

    def test_invalid_gop_does_not_become_a_zero_or_full_score(self):
        document=core()
        document['phonemeAssessment']['samples'][0]['gop']=None
        result=grade(document)
        self.assertEqual(result['RUBRIC_RESULT'],{'status':'unscorable'})
        self.assertEqual(result['SCORING_FAILURE'],'NATIVE_RUBRIC_GOP_UNAVAILABLE_OR_INCONSISTENT')

    def test_coverage_tampering_is_rejected(self):
        document=core(delete=(2,))
        document['phonemeAssessment']['coverage']['deletedCount']=0
        self.assertEqual(grade(document)['SCORING_FAILURE'],'NATIVE_RUBRIC_COVERAGE_MISMATCH')

    def test_original_core_is_unchanged(self):
        document=core(delete=(2,),ambiguous=True,compound=True)
        before=deepcopy(document)
        grade(document)
        self.assertEqual(document,before)

    def test_old_policy_remains_reproducible(self):
        self.assertEqual(grade(core(ambiguous=True),V1)['SCORING_FAILURE'],'NATIVE_RUBRIC_ALIGNMENT_AMBIGUOUS')
        self.assertEqual(grade(core(compound=True),V1)['SCORING_FAILURE'],'NATIVE_RUBRIC_EXPECTED_COUNT')


if __name__=='__main__':unittest.main()
