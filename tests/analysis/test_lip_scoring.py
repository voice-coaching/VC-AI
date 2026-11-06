"""Synthetic evidence regression; these tests are not real video/GPT QA."""
from copy import deepcopy
from dataclasses import asdict
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

import jsonschema
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from voice_coach.visual_feedback.audiovisual_evidence import (
    PhoneAnchor, LipReference, LipFrame, VerifiedTimeMap, assess_phone, aggregate)
from voice_coach.visual_feedback.feature_protocol import MAPPING, PROTOCOL
from voice_coach.visual_feedback.reference_distance import DistancePolicy, Trajectory
from voice_coach.visual_feedback.lip_scoring import LipScorePolicy, score_phone, enrich_phone
from voice_coach.visual_feedback.canonical_service import assess_aligned
from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
from voice_coach.six_dimension.visual import visual_context, feedback_targets
from voice_coach.six_dimension.pipeline import evaluate
from voice_coach.six_dimension.policy import encode, sha, load_policy

REQUEST = "10000000-0000-4000-8000-000000000001"
EXECUTION = "10000000-0000-4000-8000-000000000002"
POLICY = LipScorePolicy()
DISTANCE = DistancePolicy("test-distance", 0, (1, 1, 1), (1, 1, 1), 0.25, 1, 2)


def trajectory(aperture=(0.01, 0.02, 0.08, 0.04, 0.02), width=0.20):
    return Trajectory((0.1, 0.2, 0.3, 0.4, 0.5),
        tuple(tuple((i / 100, 0.1) for i in range(20)) for _ in aperture),
        tuple((a, width, a * width) for a in aperture))


def reference(t=None, phone="ㅜ", role="nucleus"):
    return LipReference("synthetic-ref", "e"*64, phone, role, "word:구", MAPPING.revision,
                        t or trajectory(), "synthetic-review-not-real", "f"*64)


def frames(t):
    return tuple(LipFrame(time, points, feature, True) for time, points, feature
                 in zip(t.times, t.landmarks, t.features))


def visual_input(score=80):
    return dict(status="SCORED", score=score, scoreKind="PROVISIONAL", requestId=REQUEST,
        executionId=EXECUTION, coreSha256="a"*64, visualEvidenceSha256="d"*64,
        phones=[dict(expectedIndex=i, expectedPhone="ㅜ", role="nucleus", score=s,
            allowedActionIds=['COMPARE_REFERENCE'],
            status="SCORED", scoreKind="PROVISIONAL", audioStartSeconds=0.1, audioEndSeconds=0.6)
            for i, s in enumerate((40, 30, 90))])


def audio_assessment(total=70.5):
    keys = ["pronunciation_accuracy", "speaking_rate", "articulation_rate", "pauses", "pitch", "intensity"]
    return dict(association=dict(coreSha256="a"*64, requestId=REQUEST),
        criteria=[dict(criterionId=k, score=s, evidenceIds=["test:"+k]) for k, s in zip(keys, (90,80,70,60,50,40))],
        selectedCriterionIds=["intensity", "pitch", "pauses"],
        auxiliary=dict(composite=dict(result=dict(overallScore=total))))


class LipScoring(unittest.TestCase):
    def test_empty_phone_interval_is_not_reported_as_face_occlusion(self):
        a=PhoneAnchor(0,'ㅜ','nucleus','word:구',0,0.05,'MFA_LOCATION_ONLY')
        result=assess_phone(a,frames=frames(trajectory()),pcm_sha256='a'*64,
            video_sha256='b'*64,time_map=VerifiedTimeMap('a'*64,'b'*64,1,0,'synthetic'),
            mapping_revision=MAPPING.revision,references=(),policy=DISTANCE,source_sha256='d'*64)
        self.assertEqual(result['reasonCodes'],['NO_FRAME_IN_ALIGNED_SPAN'])
        self.assertIsNone(result['score'])

    def test_local_lip_practice_survives_insufficient_overall_coverage(self):
        audio=audio_assessment()
        visual=visual_input()
        visual.update(status='OBSERVED',score=None,scoreKind='NONE')
        visual['phones']=visual['phones'][:1]
        context=visual_context(audio,visual)
        self.assertEqual(context['grade']['overallScore'],70.5)
        self.assertIsNone(context['grade']['visualScore'])
        self.assertEqual([r['criterionId'] for r in feedback_targets(audio,context)],
                         ['lip_articulation:0','intensity','pitch'])
        visual['phones'][0]['allowedActionIds']=[]
        self.assertEqual(visual_context(audio,visual)['targets'],[])

    def test_equal_reference_and_dead_band_are_not_penalized(self):
        a = PhoneAnchor(0, "ㅜ", "nucleus", "word:구", 0, 0.6, "MFA_LOCATION_ONLY")
        subs, total = score_phone(a, frames(trajectory()), [reference()], policy=POLICY)
        self.assertEqual(total, 100)
        self.assertEqual([r["score"] for r in subs], [100,100,None,100])

    def test_shape_deviation_is_penalized_exponentially(self):
        a = PhoneAnchor(0,"ㅜ","nucleus","word:구",0,0.6,"MFA_LOCATION_ONLY")
        scores=[]
        for width in (0.20,0.25,0.29):
            subs,_=score_phone(a,frames(trajectory(width=width)),[reference()],policy=POLICY)
            scores.append(subs[1]["score"])
        self.assertGreater(scores[0],scores[1]);self.assertGreater(scores[1],scores[2])
        self.assertAlmostEqual(scores[1],60.7)

    def test_static_phone_has_no_timing_score_and_no_fabricated_closure(self):
        t=trajectory((0.1,)*5)
        a=PhoneAnchor(0,"ㅏ","nucleus","word:구",0,0.6,"MFA_LOCATION_ONLY")
        subs,_=score_phone(a,frames(t),[reference(t,phone="ㅏ")],policy=POLICY)
        self.assertIsNone(subs[3]["score"]);self.assertIsNone(subs[2]["score"])

    def test_bilabial_closure_uses_minimum_not_mean_aperture(self):
        a=PhoneAnchor(0,"ㅁ","onset","word:구",0,0.6,"MFA_LOCATION_ONLY")
        t=trajectory((0.08,0.01,0,0.04,0.08))
        subs,_=score_phone(a,frames(t),[reference(t,"ㅁ","onset")],policy=POLICY)
        self.assertEqual(subs[2]["measurements"][0]["value"],0)
        self.assertEqual(subs[2]["score"],100)
        self.assertIsNone(subs[0]["score"])

    def test_event_phase_compares_to_reference_not_zero_audio_lag(self):
        a=PhoneAnchor(0,"ㅜ","nucleus","word:구",0,0.6,"MFA_LOCATION_ONLY")
        t=trajectory((0.01,0.02,0.03,0.08,0.01))
        subs,_=score_phone(a,frames(t),[reference(t)],policy=POLICY)
        self.assertAlmostEqual(subs[3]["measurements"][0]["referenceMedian"],0.75)
        self.assertEqual(subs[3]["score"],100)

    def test_missing_reference_and_self_comparison_remain_unscorable(self):
        a=PhoneAnchor(0,"ㅜ","nucleus","word:구",0,0.6,"MFA_LOCATION_ONLY")
        fs=frames(trajectory())
        for refs in ((),(reference(),)):
            row=assess_phone(a,frames=fs,pcm_sha256="a"*64,video_sha256="e"*64,
                time_map=VerifiedTimeMap("a"*64,"e"*64,1,0,"synthetic"),mapping_revision=MAPPING.revision,
                references=refs,policy=DISTANCE,source_sha256="b"*64)
            enrich_phone(row,a,fs,references=refs,policy=POLICY,video_sha256="e"*64,source_sha256="b"*64)
            self.assertIsNone(row["score"])

    def test_provisional_is_opt_in_for_aggregation_and_zero_is_a_score(self):
        row=dict(expectedIndex=0,score=0,status="SCORED",scoreKind="PROVISIONAL",frameCount=5)
        kwargs=dict(target_count=1,visually_eligible_count=1,minimum_coverage=1)
        self.assertIsNone(aggregate([row],**kwargs)["score"])
        self.assertEqual(aggregate([row],**kwargs,allow_provisional=True)["score"],0)
        partial=aggregate([row],target_count=2,visually_eligible_count=2,minimum_coverage=0.6,
                          allow_provisional=True)
        self.assertIsNone(partial['score'])
        self.assertEqual(partial['coverage'],0.5)

    def test_component_measurements_do_not_require_a_valid_legacy_dtw_path(self):
        anchor=PhoneAnchor(0,'ㅜ','nucleus','word:구',0,0.6,'MFA_LOCATION_ONLY')
        row=dict(frameCount=5,reasonCodes=['NO_ADMISSIBLE_DTW_PATH'],videoStartSeconds=0,
                 videoEndSeconds=0.6,scoreKind='NONE',score=None)
        enrich_phone(row,anchor,frames(trajectory()),references=[reference()],policy=POLICY,
                     video_sha256='b'*64,source_sha256='c'*64)
        self.assertEqual(row['score'],100)
        self.assertEqual(row['scoreKind'],'PROVISIONAL')

    def test_frozen_bridge_loads_locked_visual_module_before_pipeline(self):
        from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalCoaching
        names=('policy','assessment','composite','native_rubric','pipeline','visual')
        files={'bridge/'+name+'.py':(ROOT/'src/voice_coach/six_dimension'/(name+'.py')).read_bytes() for name in names}
        files['bridge/phone_rubric.py']=(ROOT/'src/voice_coach/backend_analysis/phone_rubric.py').read_bytes()
        files['bridge/adapter.py']=(ROOT/'deploy/runpod/harnesses/six_dimension_bridge.py').read_bytes().replace(
            b'__CORE_MANIFEST__',b'0'*64)
        files['contracts/llm/six_dimension_policy.json']=(ROOT/'configs/pronunciation_feedback/six_dimension_v1.json').read_bytes()
        lock=dict(files={key:sha(value) for key,value in files.items()})
        lock_raw=encode(lock)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for key,value in files.items():
                path=root/key;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
            (root/'contracts/llm/dependency_lock.json').write_bytes(lock_raw)
            identity=SimpleNamespace(scoring=True,native=True,gpt_grading=True,gop_grading=True,
                six_dimension=True,lock_sha256=sha(lock_raw))
            artifacts=SimpleNamespace(identity=identity,llm_root=root,verify=lambda:lock)
            bridge=FrozenCanonicalCoaching(artifacts)._bridge()
            self.assertEqual(bridge.scoring_policy()['revision'],'six-dimension-experimental-v1')

    def test_aligned_production_replay_and_public_schema(self):
        t=trajectory();fs=frames(t)
        catalog=ReferenceCatalog("c"*64,"synthetic",DISTANCE,(reference(),),(),1,POLICY)
        request=SimpleNamespace(event_id=REQUEST,execution_id=EXECUTION,audio_sha256="a"*64,
            visual_input=SimpleNamespace(sha256="b"*64),media_preparation=dict(receipt=dict(sourceSha256="d"*64)))
        core=dict(request_id=REQUEST,source_audio_sha256="a"*64,prepared_wav_sha256="a"*64,
            expected_phone_count=1,mfa_receipt={"synthetic":True},mfa_phone_alignment=dict(
                schemaVersion="voice-coaching.canonical-mfa-phone-alignment.v1",indexNamespace="DETECTOR_METADATA_V2",
                rows=[dict(expected_index=0,expected_phone="ㅜ",role="nucleus",word="구",mfa_ipa="u",
                    localization_status="EXACT_COUNT_WORD_INDEX",word_start_s=0,phone_start_s=0,
                    phone_end_s=0.6,word_end_s=0.6)]))
        raw=encode(core)
        pcm=dict(sha256="a"*64,frames=16000,sampleRate=16000)
        features=dict(videoSha256="b"*64,modelSha256="e"*64,
            decodedPcmSha256="a"*64,decodedPcmFrames=16000,decodedPcmSampleRate=16000,
            audioClock=dict(firstPtsNumerator=0,firstPtsDenominator=1,
                source="CONTINUOUS_DECODED_AUDIO_FRAMES",sampleRate=16000),
            features=dict(protocol=PROTOCOL,mappingRevision=MAPPING.revision,frames=[dict(
                pts=f.pts_s,landmarks=[list(p) for p in f.landmarks],features=list(f.features),reason=None) for f in fs]))
        evidence=assess_aligned(core=core,request=request,feature_value=features,
            source_pcm=pcm,prepared_pcm=pcm,catalog=catalog)
        self.assertEqual(evidence["visual"]["score"],100)
        self.assertEqual(evidence["visual"]["scoreKind"],"PROVISIONAL")
        self.assertFalse(evidence["visual"]["correctiveClaimsAllowed"])
        verified=verify_visual_evidence(encode(evidence),core=core,request=request,catalog=catalog,
            expected_model_sha256="e"*64,prepared_audio=SimpleNamespace(sha256="a"*64,frame_count=16000,sample_rate=16000))
        self.assertEqual(verified,evidence)
        payload=prepare_visual_coaching(encode(evidence),core_bytes=raw)
        self.assertEqual(payload["selectionPolicy"],"LOWEST_12_SCORED_THEN_OBSERVED_V1")
        # Reference/self-comparison clips can have observations without a score.
        # Multiple rows must still sort by index rather than compare functions.
        observed=deepcopy(evidence)
        observed['visual'].update(status='OBSERVED',score=None,scoreKind='NONE')
        first=observed['visual']['phoneAssessments'][0]
        first.update(status='OBSERVED',score=None,scoreKind='NONE',allowedActionIds=[])
        observed['visual']['phoneAssessments']=[dict(first,expectedIndex=2),first]
        unscored=prepare_visual_coaching(encode(observed),core_bytes=raw)
        self.assertEqual([p['expectedIndex'] for p in unscored['phones']],[0,2])
        self.assertEqual(unscored['selectionPolicy'],'FIRST_12_OBSERVED_MFA_INDICES_V1')
        schema=json.loads((ROOT/'docs/contracts/runpod_result_v6.schema.json').read_text())
        registry=Registry().with_resource(schema['$id'],Resource.from_contents(schema))
        visual=dict(evidence['visual'],omittedPhoneAssessmentCount=0)
        jsonschema.Draft202012Validator({'$ref':schema['$id']+'#/$defs/visual'},registry=registry).validate(visual)
        self.assertEqual(raw,encode(core))
        core.pop('mfa_phone_alignment')
        result=assess_aligned(core=core,request=request,feature_value=features,
            source_pcm=pcm,prepared_pcm=pcm,catalog=catalog)
        self.assertIsNone(result['visual']['score'])
        self.assertIn('ALIGNMENT_UNAVAILABLE',result['visual']['reasonCodes'])
        original_raw=encode(core)
        sidecar=dict(schemaVersion='voice-coaching.mfa-alignment-sidecar.v1',requestId=REQUEST,
            coreSha256=sha(original_raw),sourceAudioSha256='a'*64,preparedWavSha256='a'*64,
            mfaReceiptSha256=sha(json.dumps(core['mfa_receipt'],ensure_ascii=False,sort_keys=True,
                separators=(',',':')).encode()),alignment=evidence.get('alignmentSidecar',
                    json.loads(raw)['mfa_phone_alignment']))
        with_sidecar=assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
            prepared_pcm=pcm,catalog=catalog,alignment_evidence=sidecar,core_raw=original_raw)
        self.assertEqual(with_sidecar['visual']['score'],100)
        verify_visual_evidence(encode(with_sidecar),core=core,request=request,catalog=catalog,
            expected_model_sha256='e'*64,prepared_audio=SimpleNamespace(sha256='a'*64,frame_count=16000,sample_rate=16000),
            core_raw=original_raw)
        with self.assertRaisesRegex(ValueError,'MFA_ATTEMPT_MISMATCH'):
            assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
                prepared_pcm=pcm,catalog=catalog,alignment_evidence=sidecar,core_raw=original_raw+b' ')
        self.assertEqual(encode(core),original_raw)

    def test_catalog_requires_actual_references_and_preserves_old_catalog(self):
        from voice_coach.visual_feedback.reference_preparation import enable_provisional_scoring
        base=(ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes()
        policy=encode(asdict(POLICY))
        old=ReferenceCatalog.from_bytes(base,sha(base))
        self.assertIsNone(old.lip_policy)
        with self.assertRaisesRegex(ValueError,'REFERENCE_CATALOG_INVALID'):
            enable_provisional_scoring(base,policy)
        self.assertEqual(sha(base),sha((ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes()))
        data=json.loads(base)
        t=trajectory()
        data['references']=[dict(referenceId='synthetic-ref',videoSha256='e'*64,expectedPhone='ㅜ',
            role='nucleus',context='word:구',expertReviewId='synthetic-review-not-real',
            trajectory=dict(times=t.times,landmarks=t.landmarks,features=t.features),
            provenance=dict(sourceSha256='f'*64,coreSha256='a'*64,preparedWavSha256='b'*64,
                captureSha256='c'*64,expectedIndex=0,audioStartSeconds=0,audioEndSeconds=0.6,
                videoStartSeconds=0,videoEndSeconds=0.6))]
        v3=enable_provisional_scoring(encode(data),policy)
        loaded=ReferenceCatalog.from_bytes(v3,sha(v3))
        self.assertEqual(loaded.lip_policy,POLICY)
        self.assertEqual(loaded.minimum_coverage,0.6)

    def test_user_designation_is_provisional_and_cannot_create_expert_calibration(self):
        from voice_coach.visual_feedback.reference_preparation import compile_designated_catalog
        t=trajectory()
        capture=dict(schemaVersion='voice-coaching.lip-reference-capture.v1',sourceSha256='f'*64,
            videoSha256='e'*64,preparedWavSha256='a'*64,coreSha256='b'*64,modelSha256='c'*64,
            mappingRevision=MAPPING.revision,alignmentScope='CANONICAL_MFA_DETECTOR_PHONES_V1',
            unresolvedExpectedIndices=[],phones=[dict(expectedIndex=0,expectedPhone='ㅜ',role='nucleus',
                context='word:구',audioStartSeconds=0,audioEndSeconds=0.6,videoStartSeconds=0,
                videoEndSeconds=0.6,observable=True,trajectory=dict(times=t.times,landmarks=t.landmarks,features=t.features))])
        base=(ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes()
        raw=compile_designated_catalog(capture_raw=encode(capture),base_raw=base,policy_raw=encode(asdict(POLICY)),
            source_sha256='f'*64,model_sha256='c'*64,revision='synthetic-user-designation')
        catalog=ReferenceCatalog.from_bytes(raw,sha(raw))
        self.assertEqual(catalog.references[0].authority,'USER_DESIGNATED_EXEMPLAR')
        self.assertFalse(catalog.references[0].expert_review_id)
        self.assertEqual(catalog.calibrations,())
        anchor=PhoneAnchor(0,'ㅜ','nucleus','word:구',0,0.6,'MFA_LOCATION_ONLY')
        row=dict(frameCount=5,reasonCodes=['REFERENCE_MISSING'],videoStartSeconds=0,
                 videoEndSeconds=0.6,scoreKind='NONE',score=None)
        enrich_phone(row,anchor,frames(t),references=catalog.references,policy=POLICY,
                     video_sha256='0'*64,source_sha256='1'*64)
        self.assertEqual(row['score'],100)
        self.assertEqual(row['scoreKind'],'PROVISIONAL')
        tampered=json.loads(raw)
        tampered['calibrations']=[dict(revision='bad-promotion',validationReceipt='synthetic',expectedPhone='ㅜ',
            role='nucleus',context='word:구',referenceIds=[catalog.references[0].reference_id],tau=1,knots=[])]
        with self.assertRaises(ValueError):ReferenceCatalog.from_bytes(encode(tampered),sha(encode(tampered)))

    def test_cleanup_drains_full_output_pipes_before_waiting(self):
        from voice_coach.visual_feedback.adapters.canonical_video_features import _drain_cleanup
        async def check():
            process=await asyncio.create_subprocess_exec(sys.executable,'-c',
                'import os,time;os.write(1,b"x"*2097152);time.sleep(30)',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            await asyncio.sleep(0.1)
            task=asyncio.create_task(_drain_cleanup([process],[]))
            done,_=await asyncio.wait({task},timeout=3)
            completed=bool(done)
            if not completed:
                await process.stdout.read()
            await task
            self.assertTrue(completed,'Full stdout must not leave cleanup waiting forever')
            self.assertIsNotNone(process.returncode)
        asyncio.run(check())

    def test_textgrid_microsecond_rounding_is_clipped_but_larger_errors_are_rejected(self):
        from pydantic import BaseModel
        class Interval(BaseModel):
            label: str
            start_s: float
            end_s: float
        spec=importlib.util.spec_from_file_location('synthetic_mfa_bounds',ROOT/'deploy/dlpc/audiovisual/mfa_time_bounds.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        original=Interval(label='synthetic',start_s=4.8,end_s=4.842688)
        parsed=SimpleNamespace(word_intervals=[original],phone_intervals=[original],tier_names=('words','phones'))
        result=module.normalize_result(parsed,audio_duration_s=4.8426875)
        self.assertEqual(result.word_intervals[0].end_s,4.8426875)
        self.assertEqual(original.end_s,4.842688)
        self.assertEqual(result.bounds_normalization['clippedIntervalCount'],2)
        self.assertAlmostEqual(result.bounds_normalization['maximumClippedSeconds'],0.0000005)
        malformed=SimpleNamespace(word_intervals=[Interval(label='synthetic',start_s=4.8,end_s=4.843)],
                                  phone_intervals=[],tier_names=('words','phones'))
        with self.assertRaisesRegex(ValueError,'MFA_TIME_BOUNDS_EXCEEDED'):
            module.normalize_result(malformed,audio_duration_s=4.8426875)

    def test_public_overall_score_uses_combined_grade_with_original_field_names(self):
        from voice_coach.backend_analysis.canonical_result import _public_score
        combined=visual_context(audio_assessment(),visual_input())['grade']
        expression=dict(sixDimension=dict(compositeGrade=dict(overallScore=70.5),audiovisualGrade=combined))
        public=_public_score(expression,None,SimpleNamespace(scoring=True,six_dimension=True))
        self.assertEqual(public,dict(overallScore=74.3,validity='RUBRIC_COMPUTED',
                                    reason='EVIDENCE_BOUND_PRACTICE_SCORE'))

    def test_sidecar_export_keeps_original_file_bytes_and_binds_receipt(self):
        def load(name,path):
            spec=importlib.util.spec_from_file_location(name,path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            return module
        alignment=load('canonical_mfa_alignment',ROOT/'deploy/dlpc/audiovisual/mfa_alignment.py')
        with patch.dict(sys.modules,{'canonical_mfa_alignment':alignment}):
            exporter=load('synthetic_mfa_sidecar_export',ROOT/'deploy/dlpc/audiovisual/mfa_sidecar_export.py')
        # Export helper runs after CORE serialization; the locator is separate.
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'core.json'
            raw=json.dumps(dict(request_id=REQUEST,source_audio_sha256='a'*64,prepared_wav_sha256='b'*64,
                mfa_receipt=dict(worker=dict(request_id=REQUEST))),indent=3).encode()
            path.write_bytes(raw)
            with patch.object(exporter,'export_alignment',return_value=dict(
                    schemaVersion='voice-coaching.canonical-mfa-phone-alignment.v1',indexNamespace='DETECTOR_METADATA_V2',rows=[])):
                exporter.write_sidecar(path,{'phone_contract':object()},{},None)
            self.assertEqual(path.read_bytes(),raw)
            sidecar=json.loads(path.with_suffix('.alignment.json').read_bytes())
            self.assertEqual(sidecar['coreSha256'],sha(raw))
            self.assertEqual(path.with_suffix('.alignment.json').stat().st_mode & 0o777,0o600)
        builder=load('synthetic_sidecar_builder',ROOT/'deploy/dlpc/build_lip_sidecar_core.py')
        original=("def run():\n    for case in cases:\n        if admitted:\n"
            "            dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, default=str)+'\\n')\n").encode()
        updated=builder.patch_runner(original)
        compile(updated,'synthetic-sidecar-runner','exec')
        self.assertEqual(updated.count(b'write_sidecar(dest, state, legacy, p1.get_locations)'),1)
        self.assertIn(b"dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, default=str)+'\\n')",updated)

    def test_combined_total_audio_preservation_feedback_priority_and_nulls(self):
        audio=audio_assessment();before=deepcopy(audio)
        context=visual_context(audio,visual_input())
        self.assertEqual(context['grade']['overallScore'],74.3)
        self.assertEqual([r['criterionId'] for r in feedback_targets(audio,context)],
                         ['lip_articulation:1','lip_articulation:0','intensity'])
        self.assertEqual(audio,before)
        partial=visual_context(audio_assessment(None),visual_input())['grade']
        self.assertIsNone(partial['overallScore'])
        self.assertEqual(partial['visualScore'],80)
        self.assertEqual((partial['audioWeight'],partial['visualWeight']),(60,40))
        self.assertEqual(visual_context(audio,dict(visual_input(),status='TIMEOUT',score=None,scoreKind='NONE'))['grade']['overallScore'],70.5)
        self.assertEqual(visual_context(audio_assessment(0),visual_input(0))['grade']['overallScore'],0)
        with self.assertRaisesRegex(ValueError,'VISUAL_CORE_BINDING'):
            visual_context(audio,dict(visual_input(),coreSha256='0'*64))

    def test_one_provider_call_raw_core_and_scores_survive_provider_failure(self):
        policy,digest=load_policy(ROOT/'configs/pronunciation_feedback/six_dimension_v1.json')
        script='구름을 보며 문장을 읽어요'
        core=dict(request_id=REQUEST,source_audio_sha256='a'*64,prepared_wav_sha256='b'*64,
            input_validation=dict(status='ACCEPT'))
        # Non-canonical whitespace is intentional: preserve bytes, not re-encoding.
        raw=json.dumps(core,ensure_ascii=False,indent=3).encode()
        assessment=audio_assessment()
        assessment.update(status='READY',policyRevision='synthetic',validationStatus='EXPERT_REVIEW_PENDING',
            policySha256=digest,evidence=[],limitations=['합성 검사'],dependencies=[])
        assessment['association']=dict(coreSha256=sha(raw),scriptSha256=sha(script.encode()),requestId=REQUEST,
            sourceAudioSha256='a'*64,preparedWavSha256='b'*64)
        visual=dict(visual_input(),coreSha256=sha(raw))
        for row in assessment['criteria']:
            row.update(label=row['criterionId'],status='SCORED',reasonCode='SYNTHETIC',components=[],scope='SYNTHETIC')
        context=visual_context(assessment,visual)
        calls=[]
        def transport(payload,**kwargs):
            calls.append(json.loads(payload))
            data=json.loads(calls[-1]['messages'][1]['content'])
            schema=data['OUTPUT_SCHEMA']
            candidate=dict(assessments=[dict(criterionId=r['criterionId'],score=r['score'],
                evidenceIds=r['evidenceIds'],observation='합성 근거') for r in assessment['criteria']],
                feedback=[dict(criterionId=r['criterionId'],evidenceIds=r['evidenceIds'],
                    observation='합성 근거',practice='문장 → 입술 움직임을 참조와 비교해 보세요')
                    for r in feedback_targets(assessment,context)],audiovisualGrade=context['grade'])
            jsonschema.Draft202012Validator(schema).validate(candidate)
            return encode(dict(model=policy['request']['model'],choices=[dict(finish_reason='stop',
                message=dict(content=encode(candidate).decode()))]))
        with patch('voice_coach.six_dimension.pipeline.prepare_assessment',return_value=(assessment,None)):
            result=evaluate(core_bytes=raw,script=script,binding=assessment['association'],policy=policy,
                policy_sha256=digest,prompt='합성 검사용 프롬프트',transport=transport,visual=visual)
            self.assertEqual(result['generationStatus'],'STRUCTURALLY_VALID')
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['messages'][-1]['content'].encode(),raw)
            self.assertEqual(result['audiovisualGrade']['overallScore'],74.3)
            failures=[]
            def fail(*args,**kwargs):
                failures.append(1);raise TimeoutError('synthetic failure')
            failed=evaluate(core_bytes=raw,script=script,binding=assessment['association'],policy=policy,
                policy_sha256=digest,prompt='합성 검사용 프롬프트',transport=fail,visual=visual)
            self.assertEqual(len(failures),1)
            self.assertEqual(failed['generationStatus'],'FAILED')
            self.assertEqual(failed['audiovisualGrade']['overallScore'],74.3)


if __name__ == '__main__':
    unittest.main()
