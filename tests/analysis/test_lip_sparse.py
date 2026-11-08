"""30fps shape observations are distinct from motion, without provider calls."""
from dataclasses import replace, asdict
from copy import deepcopy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_lip_scoring import (trajectory, reference, frames, POLICY, DISTANCE, audio_assessment,
                              visual_input)
from voice_coach.visual_feedback.audiovisual_evidence import PhoneAnchor, VerifiedTimeMap, assess_phone
from voice_coach.visual_feedback.lip_scoring import score_phone, enrich_phone
from voice_coach.visual_feedback.reference_distance import Trajectory, UnscorableTrajectory, compare_trajectories
from voice_coach.six_dimension.visual import visual_context, feedback_targets
from voice_coach.backend_analysis.browser_video_summary import summarize
from voice_coach.backend_analysis.canonical_harness import (SPARSE_LIP, SPARSE_LIP_INTERNAL,
    check_schema_registration,check_result_classification)


def sparse(n=1,phone='ㅜ'):
    t=trajectory()
    return Trajectory(tuple((i+1)/30 for i in range(n)),t.landmarks[:n],t.features[:n])


class SparseLip(unittest.TestCase):
    def anchor(self, phone='ㅜ',role='nucleus'):
        return PhoneAnchor(0,phone,role,'word:구',0,.1,'MFA_LOCATION_ONLY')

    def assess(self,fs, *, allow=True,phone='ㅜ',role='nucleus',refs=None):
        a=self.anchor(phone,role)
        row=assess_phone(a,frames=fs,pcm_sha256='a'*64,video_sha256='b'*64,
            time_map=VerifiedTimeMap('a'*64,'b'*64,1,0,'synthetic'),mapping_revision=reference().mapping_revision,
            references=tuple(refs or []),policy=DISTANCE,source_sha256='d'*64,allow_sparse_frames=allow)
        enrich_phone(row,a,fs,references=refs or [],policy=POLICY,video_sha256='b'*64,
            source_sha256='d'*64,allow_sparse_frames=allow)
        return row

    def test_one_frame_can_score_aperture_and_width_but_never_timing(self):
        row=self.assess(frames(sparse()),refs=[reference()])
        self.assertEqual(row['status'],'SCORED')
        self.assertEqual(row['frameCount'],1)
        self.assertTrue(all(s['score'] is not None for s in row['subScores'][:2]))
        self.assertIsNone(row['subScores'][3]['score'])
        self.assertIn('SPARSE_STATIC_OBSERVATION',row['reasonCodes'])
        self.assertEqual(self.assess(frames(sparse()),allow=False)['status'],'UNSCORABLE')

    def test_two_frame_closure_proxy_and_no_motion_timing(self):
        row=self.assess(frames(sparse(2)),phone='ㅁ',role='onset',refs=[reference(phone='ㅁ',role='onset')])
        self.assertIsNotNone(row['subScores'][2]['score'])
        self.assertIsNone(row['subScores'][3]['score'])
        one=self.assess(frames(sparse()),phone='ㅁ',role='onset',refs=[reference(phone='ㅁ',role='onset')])
        self.assertIsNone(one['score'])

    def test_sparse_reference_supports_static_comparison_without_fake_motion(self):
        row=self.assess(frames(sparse()),refs=[reference(sparse())])
        self.assertEqual(row['score'],100)
        self.assertIsNone(row['subScores'][3]['score'])

    def test_sparse_reference_catalog_is_versioned_and_cannot_be_calibrated(self):
        from voice_coach.visual_feedback.reference_preparation import compile_designated_catalog, encode
        from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
        from voice_coach.six_dimension.policy import sha
        t=sparse()
        capture=dict(schemaVersion='voice-coaching.lip-reference-capture.v2',sourceSha256='f'*64,
            videoSha256='e'*64,preparedWavSha256='a'*64,coreSha256='b'*64,modelSha256='c'*64,
            mappingRevision=reference().mapping_revision,alignmentScope='CANONICAL_MFA_DETECTOR_PHONES_V1',
            unresolvedExpectedIndices=[],phones=[dict(expectedIndex=0,expectedPhone='ㅜ',role='nucleus',
                context='word:구',audioStartSeconds=0,audioEndSeconds=.1,videoStartSeconds=0,
                videoEndSeconds=.1,observable=True,trajectory=dict(times=t.times,landmarks=t.landmarks,features=t.features))])
        root=Path(__file__).parents[2]
        raw=compile_designated_catalog(capture_raw=encode(capture),base_raw=(root/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes(),
            policy_raw=encode(asdict(POLICY)),source_sha256='f'*64,model_sha256='c'*64,revision='synthetic-sparse')
        self.assertEqual(json.loads(raw)['schemaVersion'],'voice-coaching.lip-reference-catalog.v4')
        catalog=ReferenceCatalog.from_bytes(raw,sha(raw))
        self.assertEqual(len(catalog.references[0].trajectory.times),1)
        old=json.loads(raw);old['schemaVersion']='voice-coaching.lip-reference-catalog.v3'
        with self.assertRaises(ValueError):ReferenceCatalog.from_bytes(encode(old),sha(encode(old)))
        promoted=json.loads(raw);promoted['references'][0]['expertReviewId']='synthetic-review'
        promoted['references'][0]['referenceAuthority']='EXPERT_REVIEWED'
        promoted['calibrations']=[dict(revision='invalid',validationReceipt='synthetic',expectedPhone='ㅜ',role='nucleus',
            context='word:구',referenceIds=[catalog.references[0].reference_id],tau=1,knots=[])]
        with self.assertRaises(ValueError):ReferenceCatalog.from_bytes(encode(promoted),sha(encode(promoted)))

    def test_missing_reference_and_zero_frames_remain_null(self):
        self.assertIsNone(self.assess(frames(sparse()))['score'])
        row=self.assess((),refs=[reference()])
        self.assertIsNone(row['score'])
        self.assertEqual(row['reasonCodes'],['NO_FRAME_IN_ALIGNED_SPAN'])

    def test_self_reference_never_becomes_a_score(self):
        row=self.assess(frames(sparse()),refs=[replace(reference(),video_sha256='b'*64)])
        self.assertIsNone(row['score'])
        self.assertEqual(row['reasonCodes'],['REFERENCE_SELF_COMPARISON'])

    def test_occluded_frames_allow_only_measured_visible_shape(self):
        fs=list(frames(sparse(2)));fs[1]=replace(fs[1],observable=False)
        row=self.assess(tuple(fs),refs=[reference()])
        self.assertIsNotNone(row['score'])
        self.assertEqual(row['frameCount'],1)
        self.assertIn('PARTIAL_VISIBILITY_STATIC_ONLY',row['reasonCodes'])
        self.assertIsNone(row['subScores'][3]['score'])
        self.assertIsNone(self.assess(tuple(fs),allow=False,refs=[reference()])['score'])
        fs[0]=replace(fs[0],observable=False)
        self.assertIsNone(self.assess(tuple(fs),refs=[reference()])['score'])

    def test_dtw_still_requires_three_real_frames(self):
        with self.assertRaises(UnscorableTrajectory):
            compare_trajectories(sparse(),reference().trajectory,DISTANCE)

    def test_observation_feedback_does_not_claim_a_reference_or_change_grade(self):
        v=visual_input();v.update(status='OBSERVED',score=None,scoreKind='NONE',
            comparisonPolicy='PHONE_ROLE_SPARSE_30FPS_V1',reasonCodes=['REFERENCE_MISSING'])
        v['phones']=[dict(v['phones'][0],score=None,scoreKind='NONE',status='OBSERVED',
                          frameCount=1,observations=[{'code':'LIP_APERTURE_RANGE'}],allowedActionIds=[])]
        context=visual_context(audio_assessment(),v)
        self.assertEqual(context['targets'][0]['practiceScope'],'OBSERVE_VISIBLE_SHAPE_ONLY')
        self.assertIsNone(context['targets'][0]['score'])
        self.assertEqual(context['grade']['overallScore'],70.5)
        self.assertEqual(len(feedback_targets(audio_assessment(),context)),3)

    def test_capture_guidance_has_no_fabricated_phone(self):
        v=visual_input();v.update(status='UNSCORABLE',score=None,scoreKind='NONE',phones=[],
            comparisonPolicy='PHONE_ROLE_SPARSE_30FPS_V1',reasonCodes=['NO_FACE'])
        target=visual_context(audio_assessment(),v)['targets'][0]
        self.assertEqual(target['practiceScope'],'CAPTURE_VISIBILITY_ONLY')
        self.assertNotIn('expectedPhone',target)

    def test_both_harness_lanes_register_and_classify(self):
        from voice_coach.backend_analysis.native_execution import APPROVED_NATIVE_EXECUTIONS
        for identity in (SPARSE_LIP,SPARSE_LIP_INTERNAL):
            check_schema_registration(identity);check_result_classification(identity)
            self.assertIn((identity.core_sha256,identity.manifest_sha256),APPROVED_NATIVE_EXECUTIONS)

    def test_single_frame_evidence_replay_and_reference_capture_preserve_core(self):
        from test_lip_scoring import REQUEST,EXECUTION
        from voice_coach.visual_feedback.canonical_service import assess_aligned,SPARSE_ASSESSMENT
        from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
        from voice_coach.visual_feedback.feature_protocol import MAPPING,PROTOCOL
        from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
        from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
        from voice_coach.visual_feedback.reference_preparation import capture_reference
        from voice_coach.six_dimension.policy import encode
        catalog=ReferenceCatalog('c'*64,'synthetic',DISTANCE,(reference(),),(),.6,POLICY)
        receipt=dict(sourceSha256='d'*64,canonicalPcmSha256='a'*64,canonicalVideoSha256='b'*64)
        request=SimpleNamespace(event_id=REQUEST,execution_id=EXECUTION,audio_sha256='a'*64,
            visual_input=SimpleNamespace(sha256='b'*64),media_preparation={'receipt':receipt})
        core=dict(request_id=REQUEST,source_audio_sha256='a'*64,prepared_wav_sha256='a'*64,
            mfa_receipt={},expected_phone_count=4,mfa_phone_alignment=dict(
                schemaVersion='voice-coaching.canonical-mfa-phone-alignment.v1',indexNamespace='DETECTOR_METADATA_V2',
                rows=[dict(expected_index=i,expected_phone='ㅜ',role='nucleus',word='구',mfa_ipa='u',
                    localization_status='EXACT_COUNT_WORD_INDEX',word_start_s=i/10,phone_start_s=i/10,
                    phone_end_s=i/10+.08,word_end_s=i/10+.08) for i in range(4)]))
        raw=encode(core);pcm=dict(sha256='a'*64,frames=16000,sampleRate=16000)
        features=dict(videoSha256='b'*64,modelSha256='e'*64,decodedPcmSha256='a'*64,
            decodedPcmFrames=16000,decodedPcmSampleRate=16000,
            audioClock=dict(firstPtsNumerator=0,firstPtsDenominator=1,source='CONTINUOUS_DECODED_AUDIO_FRAMES',sampleRate=16000),
            features=dict(protocol=PROTOCOL,mappingRevision=MAPPING.revision,frames=[dict(
                pts=f.pts_s,landmarks=[list(p) for p in f.landmarks],features=list(f.features),reason=None)
                for f in frames(sparse())]))
        e=assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,prepared_pcm=pcm,
            catalog=catalog,core_raw=raw,assessment_policy=SPARSE_ASSESSMENT)
        verify_visual_evidence(encode(e),core=core,request=request,catalog=catalog,core_raw=raw,
            expected_model_sha256='e'*64,prepared_audio=SimpleNamespace(sha256='a'*64,frame_count=16000,sample_rate=16000))
        v=prepare_visual_coaching(encode(e),core_bytes=raw)
        self.assertEqual(v['phones'][0]['frameCount'],1)
        self.assertEqual(v['partialScore']['scope'],'ASSESSED_PHONES_ONLY')
        self.assertIsNone(v['score'])
        audio=audio_assessment();audio['association']['coreSha256']=v['coreSha256']
        context=visual_context(audio,v)
        self.assertEqual(context['targets'][0]['practiceScope'],'COMPARE_REFERENCE_VISIBLE_SHAPE_ONLY')
        capture=capture_reference(core=core,core_raw=raw,receipt=receipt,features=features,source_pcm=pcm,
            prepared_pcm=pcm,catalog=catalog,allow_sparse_frames=True)
        self.assertEqual(capture['schemaVersion'],'voice-coaching.lip-reference-capture.v2')
        self.assertTrue(capture['phones'][0]['observable'])
        self.assertEqual(len(capture['phones'][0]['trajectory']['times']),1)
        self.assertEqual(encode(core),raw)


class BrowserSummary(unittest.TestCase):
    def test_only_final_scores_and_feedback_cross_display_boundary(self):
        v=dict(schemaVersion='voice-coaching.independent-audiovisual-result.v1',jobId='job',
            executionId='execution',clientRequestId='attempt',status='COMPLETED',failure=None,
            score=dict(overallScore=0,validity='RUBRIC_COMPUTED',reason='EVIDENCE_BOUND_PRACTICE_SCORE',details={'private':1}),
            coaching=dict(feedback='* 입술 → 짧게 읽으며 입모양을 확인하세요.',selection={'private':1}),
            visual=dict(score=0,scoreKind='PROVISIONAL',reasonCodes=['PARTIAL_REFERENCE_COVERAGE'],
                        phoneAssessments=[{'private':1}],subScores=[{'private':1}]))
        raw=json.dumps(v).encode();original=deepcopy(v)
        result,pin=summarize(raw);d=json.loads(result)
        self.assertEqual(v,original)
        self.assertEqual(d['score']['overallScore'],0)
        self.assertEqual(d['visual']['scoreScope'],'ASSESSED_PHONES_ONLY')
        self.assertEqual(d['clientRequestId'],'attempt')
        self.assertNotIn('private',result.decode())
        self.assertEqual(set(d['coaching']),{'feedback'})
        import hashlib
        self.assertEqual(hashlib.sha256(result).hexdigest(),pin)
        self.assertNotEqual(hashlib.sha256(raw).hexdigest(),pin)
