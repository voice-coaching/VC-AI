"""Synthetic regression only: never a measured AI-Hub catalog or GPT quality QA."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import io
import json
import math
from pathlib import Path
import statistics
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
import asyncio
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from voice_coach.visual_feedback.corpus_reference import (ASSESSMENT,MEASUREMENTS,CorpusPolicy,
    CorpusReference,build_population,encode,measure_phone,neighbour_context,sha,speaker_split)
from voice_coach.visual_feedback.corpus_dataset import (PLAN_SCHEMA,index_sources,verify_sources,
    contained,private_write,plan_from_labels,ordered_tar_parts)
from voice_coach.visual_feedback.feature_protocol import MAPPING,PROTOCOL
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.audiovisual_evidence import LipFrame,PhoneAnchor
from voice_coach.visual_feedback.canonical_service import assess_aligned
from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
from voice_coach.six_dimension.visual import visual_context

POLICY=CorpusPolicy(minimum_train_speakers=3,minimum_train_occurrences=6,
                    minimum_calibration_speakers=1,minimum_test_speakers=1)


def speaker(split,n):
    return [sha(f'synthetic:{i}'.encode()) for i in range(200) if speaker_split(sha(f'synthetic:{i}'.encode()))==split][n]


def records():
    result=[]
    for split,n in [('TRAIN',3),('CALIBRATION',1),('TEST',1)]:
        for i in range(n):
            for j in range(2):
                result.append(dict(schemaVersion=MEASUREMENTS,datasetId='538',sourcePartition='TRAIN',
                    speakerType='EXPERT',view='A',noise='1',datasetManifestSha256='a'*64,
                    modelSha256='e'*64,qualitySha256='f'*64,mappingRevision=MAPPING.revision,
                    speakerSha256=speaker(split,i),sourceVideoSha256=sha((split+str(i)).encode()),
                    normalizedVideoSha256=sha((split+str(i)+str(j)).encode()),
                    entryId=sha((split+str(i)+str(j)).encode()),
                    occurrenceId=sha((split+str(i)+str(j)).encode()),coreSha256='b'*64,alignmentSha256='c'*64,
                    phone='ㅏ',role='nucleus',metrics=[dict(criterionId='lip_aperture',phaseBin=str(k),
                        contextId='ANY',value=.1+i*.01+j*.001) for k in range(4)]))
    return result


def population(rows=None):
    return build_population(rows if rows is not None else records(),dataset_manifest_sha256='a'*64,
        model_sha256='e'*64,quality_sha256='f'*64,revision='synthetic-not-real',policy=POLICY)


def catalog():
    value=json.loads((ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes())
    value.update(schemaVersion='voice-coaching.lip-reference-catalog.v5',revision='synthetic-not-real',
        contextPolicy='CORPUS_PHASE_AND_NEIGHBOUR_V1',minimumCoverage=.6,population=population())
    raw=encode(value);return ReferenceCatalog.from_bytes(raw,sha(raw))


def research_model():
    from voice_coach.visual_feedback.corpus_research_model import fit
    rows=[]
    base=records()
    for i in range(50):
        row=deepcopy(base[i%len(base)])
        row['entryId']=sha(f'research-sentence:{i}'.encode())
        row['occurrenceId']=sha(f'research-occurrence:{i}'.encode())
        rows.append(row)
    return fit(rows)


def frame(t,aperture=.1,visible=True):
    return LipFrame(t,tuple((i/100,.1) for i in range(20)) if visible else (),
                    (aperture,.2,.01) if visible else (),visible)


def fixture(catalog,reason=None):
    request=SimpleNamespace(event_id='10000000-0000-4000-8000-000000000001',
        execution_id='10000000-0000-4000-8000-000000000002',audio_sha256='1'*64,
        visual_input=SimpleNamespace(sha256='2'*64),media_preparation={'receipt':{'sourceSha256':'3'*64}})
    core=dict(request_id=request.event_id,source_audio_sha256='1'*64,prepared_wav_sha256='1'*64,
        expected_phone_count=1,mfa_receipt={'synthetic':True},mfa_phone_alignment=dict(
            schemaVersion='voice-coaching.canonical-mfa-phone-alignment.v1',indexNamespace='DETECTOR_METADATA_V2',
            rows=[dict(expected_index=0,expected_phone='ㅏ',role='nucleus',word='가',mfa_ipa='a',
                localization_status='EXACT_COUNT_WORD_INDEX',word_start_s=0,phone_start_s=0,
                phone_end_s=.1,word_end_s=.1)]))
    pcm=dict(sha256='1'*64,frames=16000,sampleRate=16000)
    mean=catalog.corpus.value['statistics'][0]['mean']
    fs=[frame(.01,mean),frame(.04,mean,reason is None),frame(.07,mean)]
    feature=dict(videoSha256='2'*64,modelSha256='e'*64,decodedPcmSha256='1'*64,
        decodedPcmFrames=16000,decodedPcmSampleRate=16000,
        audioClock=dict(firstPtsNumerator=0,firstPtsDenominator=1,source='CONTINUOUS_DECODED_AUDIO_FRAMES',sampleRate=16000),
        features=dict(protocol=PROTOCOL,mappingRevision=MAPPING.revision,frames=[dict(
            pts=f.pts_s,landmarks=list(map(list,f.landmarks)),features=list(f.features),
            reason=reason if not f.observable else None) for f in fs]))
    return core,request,pcm,feature


class CorpusScoring(unittest.TestCase):
    def test_serving_identities_match_internal_schema_and_single_call_settings(self):
        from voice_coach.backend_analysis.canonical_harness import AIHUB_LIP,AIHUB_LIP_INTERNAL,check_schema_registration,check_result_classification
        from voice_coach.backend_analysis.native_execution import APPROVED_NATIVE_EXECUTIONS
        for identity in (AIHUB_LIP,AIHUB_LIP_INTERNAL):
            check_schema_registration(identity);check_result_classification(identity)
            self.assertIn((identity.core_sha256,identity.manifest_sha256),APPROVED_NATIVE_EXECUTIONS)
            self.assertEqual((identity.model,identity.reasoning_effort,identity.service_tier),('gpt-6-luna','none','default'))

    def test_research_serving_preserves_status_and_replays_real_scoring_contract(self):
        from voice_coach.visual_feedback.corpus_research_model import fit
        from voice_coach.visual_feedback.research_reference import ASSESSMENT as research_policy
        value=json.loads((ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes())
        model=research_model()
        value.update(schemaVersion='voice-coaching.lip-reference-catalog.v6',researchModel=model,
            researchModelSha256=sha(encode(model)),contextPolicy='CORPUS_PHASE_AND_NEIGHBOUR_V1')
        raw=encode(value);c=ReferenceCatalog.from_bytes(raw,sha(raw))
        self.assertEqual(c.assessment_policy,research_policy)
        self.assertEqual(c.corpus.value['validationStatus'],'RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION')
        core,request,pcm,features=fixture(c);core_raw=encode(core)
        measured=assess_aligned(core=core,core_raw=core_raw,request=request,feature_value=features,
            source_pcm=pcm,prepared_pcm=pcm,catalog=c,assessment_policy=research_policy)
        prepared=SimpleNamespace(sha256=pcm['sha256'],frame_count=pcm['frames'],sample_rate=16000)
        checked=verify_visual_evidence(encode(measured),core=core,core_raw=core_raw,request=request,
            catalog=c,expected_model_sha256='e'*64,prepared_audio=prepared)
        coaching=prepare_visual_coaching(encode(checked),core_bytes=core_raw)
        self.assertEqual(coaching['comparisonPolicy'],research_policy)
        comparisons=coaching['phones'][0]['corpusComparisons']
        self.assertEqual(comparisons[0]['calibrationSpeakerCount'],0)
        self.assertEqual(comparisons[0]['testSpeakerCount'],0)
        assessment=dict(association=dict(coreSha256=sha(core_raw),requestId=request.event_id),
            auxiliary=dict(composite=dict(result=dict(overallScore=80))))
        context=visual_context(assessment,coaching)
        self.assertEqual(context['targets'][0]['practiceScope'],'CORPUS_VISIBLE_SHAPE_PRACTICE')
        self.assertEqual(context['grade']['visualWeight'],40)
        tampered=deepcopy(measured);tampered['visual']['score']=12
        with self.assertRaises(ValueError):verify_visual_evidence(encode(tampered),core=core,core_raw=core_raw,
            request=request,catalog=c,expected_model_sha256='e'*64,prepared_audio=prepared)
        value['researchModel']['statistics'][0]['mean']=.8
        with self.assertRaises(ValueError):ReferenceCatalog.from_bytes(encode(value),sha(encode(value)))

    def test_research_reference_cannot_be_relabelled_or_used_with_population_policy(self):
        from voice_coach.visual_feedback.corpus_research_model import fit
        from voice_coach.visual_feedback.research_reference import ResearchReference
        m=research_model();m['validationStatus']='PASSED'
        with self.assertRaises(ValueError):ResearchReference.load(m)
        m=research_model();m['statistics'][0]['standardDeviation']=0
        with self.assertRaises(ValueError):ResearchReference.load(m)
        m=research_model();m['sourceSpeakerCount']=1
        with self.assertRaises(ValueError):ResearchReference.load(m)
        c=replace(catalog(),corpus=ResearchReference.load(research_model()))
        core,request,pcm,features=fixture(c)
        with self.assertRaisesRegex(ValueError,'CATALOG_MISMATCH'):
            assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
                prepared_pcm=pcm,catalog=c,assessment_policy=ASSESSMENT)

    def test_cross_speaker_research_check_never_trains_on_held_speaker(self):
        from voice_coach.visual_feedback.corpus_research_model import cross_speaker_diagnostics
        r=cross_speaker_diagnostics(records(),minimum_observations=2)
        self.assertEqual(r['status'],'EVALUATED')
        for fold in r['folds']:
            if fold['status']!='EVALUATED':continue
            self.assertNotIn(fold['heldOutSpeakerSha256'],fold['trainSpeakerSha256s'])
            self.assertEqual(len(fold['trainSpeakerSha256s']),4)
        self.assertEqual(cross_speaker_diagnostics(records()[:2])['status'],'INSUFFICIENT_SPEAKERS')

    def test_research_model_declares_sentence_split_without_claiming_independent_speakers(self):
        from voice_coach.visual_feedback.corpus_research_model import fit,evaluate
        rows=[]
        for i in range(50):
            row=deepcopy(records()[0]);row['entryId']=sha(('sentence'+str(i)).encode())
            row['occurrenceId']=sha(('occurrence'+str(i)).encode())
            for m in row['metrics']:m['value']=.10+(i%9)*.001
            rows.append(row)
        model=fit(rows);self.assertEqual(model['sourceSpeakerCount'],1)
        self.assertFalse(model['heldOutSentenceSplitIsSpeakerIndependent'])
        self.assertGreater(model['heldOutSentenceCount'],0)
        self.assertEqual(model['sourceSentenceCount'],model['trainSentenceCount']+model['heldOutSentenceCount'])
        self.assertEqual(model['validationStatus'],'RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION')
        with self.assertRaises(ValueError):CorpusReference.load(model)
        c=catalog();core,request,pcm,features=fixture(c)
        measured=assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
            prepared_pcm=pcm,catalog=c,assessment_policy=ASSESSMENT)
        tested=evaluate(model,measured)
        self.assertEqual(tested['scoredPhoneCount'],1)
        self.assertTrue(0<=tested['score']<=100)
        self.assertEqual(tested['providerPosts'],0)

    def test_population_fits_train_only_and_counts_speakers(self):
        p=population();r=p['statistics'][0]
        self.assertAlmostEqual(r['mean'],.1105)
        self.assertEqual(r['trainSpeakerCount'],3);self.assertEqual(r['trainOccurrenceCount'],6)
        self.assertEqual(r['calibrationSpeakerCount'],1);self.assertEqual(r['testSpeakerCount'],1)
        self.assertAlmostEqual(r['standardDeviation']**2,.0001+.0000005)
        for phase in p['statistics']:self.assertEqual(phase['trainOccurrenceCount'],6)

    def test_holding_out_same_speaker_is_deterministic(self):
        rows=records();before=population(rows)['statistics'][0]['mean']
        for row in rows:
            if speaker_split(row['speakerSha256'])!='TRAIN':
                for metric in row['metrics']:metric['value']=8
        after=population(rows)['statistics'][0]
        self.assertEqual(before,after['mean']);self.assertGreater(after['test']['meanAbsoluteZ'],100)

    def test_repetitive_speaker_does_not_dominate_mean(self):
        rows=records();original=population(rows)['statistics'][0]['mean']
        for j in range(100):
            row=deepcopy(rows[0]);row['occurrenceId']=sha(('extra'+str(j)).encode())
            for m in row['metrics']:m['value']=.1005
            rows.append(row)
        self.assertAlmostEqual(population(rows)['statistics'][0]['mean'],original)

    def test_missing_speakers_or_zero_variance_do_not_create_catalog(self):
        for rows in (records()[:2],deepcopy(records())):
            if len(rows)>2:
                for row in rows:
                    for m in row['metrics']:m['value']=.1
            p=population(rows);self.assertEqual(p['statistics'],[])
            with self.assertRaisesRegex(ValueError,'CORPUS_REFERENCE_DATA_REQUIRED'):CorpusReference.load(p)

    def test_duplicate_occurrence_and_wrong_cohort_rejected(self):
        rows=records()
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):population(rows+[deepcopy(rows[0])])
        for field,value in [('speakerType','GENERAL'),('datasetId','71469'),('sourcePartition','TEST'),('view','B')]:
            bad=deepcopy(rows);bad[0][field]=value
            with self.assertRaisesRegex(ValueError,'COHORT'):population(bad)

    def test_model_or_geometry_mismatch_rejected(self):
        for field in ('modelSha256','qualitySha256','mappingRevision','datasetManifestSha256'):
            bad=records();bad[0][field]='0'*64
            with self.assertRaisesRegex(ValueError,'PROTOCOL'):population(bad)

    def test_30fps_one_shape_frame_no_interpolation_or_motion(self):
        a=PhoneAnchor(0,'ㅏ','nucleus','word:가',0,.02,'MFA_LOCATION_ONLY')
        metrics,_=measure_phone(a,[frame(.01)],start=0,end=.02,context='a'*64,policy=POLICY)
        self.assertEqual(set(metrics),{('lip_aperture','2','ANY')})
        b=replace(a,expected_phone='ㅁ',role='onset')
        self.assertEqual(measure_phone(b,[frame(.01)],start=0,end=.02,context='a'*64,policy=POLICY)[0],{})

    def test_two_frames_closure_three_frames_timing_with_context(self):
        a=PhoneAnchor(0,'ㅁ','onset','word:마',0,.1,'MFA_LOCATION_ONLY')
        fs=[frame(.01,.1),frame(.04,.01)]
        m,_=measure_phone(a,fs,start=0,end=.1,context='a'*64,policy=POLICY)
        self.assertEqual(m,{('lip_closure','WHOLE','ANY'):.01})
        fs.append(frame(.07,.08));m,_=measure_phone(a,fs,start=0,end=.1,context='a'*64,policy=POLICY)
        self.assertAlmostEqual(m['lip_timing','WHOLE','a'*64],.4)
        fs.append(frame(.08,visible=False))
        self.assertEqual(measure_phone(a,fs,start=0,end=.1,context='a'*64,policy=POLICY)[0],{})

    def test_exponential_uses_actual_sd_without_dead_band(self):
        c=catalog().corpus;a=PhoneAnchor(0,'ㅏ','nucleus','word:가',0,.02,'MFA_LOCATION_ONLY')
        r=c.value['statistics'][0]
        scores=[]
        for z in (0,1,2):
            row=dict(videoStartSeconds=0,videoEndSeconds=.02,frameCount=1,status='OBSERVED')
            evidence=c.assess(row,a,[frame(.01,r['mean']+z*r['standardDeviation'])],context='a'*64,
                video_sha256='2'*64,source_sha256='3'*64)
            scores.append(row['score']);self.assertAlmostEqual(evidence[0]['zScore'],z)
        self.assertEqual(scores,[100,60.7,13.5])
        self.assertFalse(hasattr(c.policy,'shape_tolerance'))

    def test_unknown_phase_and_self_source_stay_null(self):
        c=catalog().corpus;a=PhoneAnchor(0,'ㅓ','nucleus','word:거',0,.02,'MFA_LOCATION_ONLY')
        for video in ('2'*64,c.value['sourceVideoSha256s'][0]):
            row=dict(videoStartSeconds=0,videoEndSeconds=.02,frameCount=1,status='OBSERVED',score=None)
            c.assess(row,a,[frame(.01)],context='a'*64,video_sha256=video,source_sha256='3'*64)
            self.assertIsNone(row['score'])

    def test_population_cannot_mix_legacy_exemplar(self):
        c=catalog();self.assertTrue(c.scoring_enabled);self.assertEqual(c.references,())
        value=json.loads((ROOT/'deploy/dlpc/audiovisual/reference-catalog.json').read_bytes())
        value.update(schemaVersion='voice-coaching.lip-reference-catalog.v5',contextPolicy='CORPUS_PHASE_AND_NEIGHBOUR_V1',
            population=population(),references=[{'fake':'exemplar'}])
        raw=encode(value)
        with self.assertRaises(ValueError):ReferenceCatalog.from_bytes(raw,sha(raw))

    def test_full_replay_selection_and_private_public_boundary(self):
        c=catalog();core,request,pcm,features=fixture(c);raw=encode(core)
        result=assess_aligned(core=core,core_raw=raw,request=request,feature_value=features,
            source_pcm=pcm,prepared_pcm=pcm,catalog=c,assessment_policy=ASSESSMENT)
        self.assertEqual(result['visual']['score'],100)
        verify_visual_evidence(encode(result),core=core,core_raw=raw,request=request,catalog=c,
            prepared_audio=SimpleNamespace(sha256='1'*64,frame_count=16000,sample_rate=16000),expected_model_sha256='e'*64)
        projected=prepare_visual_coaching(encode(result),core_bytes=raw)
        self.assertTrue(projected['phones'][0]['corpusComparisons'])
        assessment=dict(association=dict(coreSha256=sha(raw),requestId=request.event_id),
            auxiliary=dict(composite=dict(result=dict(overallScore=70))))
        context=visual_context(assessment,projected)
        self.assertEqual(context['grade']['overallScore'],82)
        self.assertEqual(context['targets'][0]['practiceScope'],'CORPUS_VISIBLE_SHAPE_PRACTICE')
        from voice_coach.backend_analysis.canonical_audiovisual_result import public_visual
        self.assertNotIn('corpusComparisons',public_visual(result['visual']))
        self.assertEqual(raw,encode(core))
        tampered=deepcopy(result);tampered['corpusComparisons']['0'][0]['zScore']=8
        with self.assertRaises(ValueError):verify_visual_evidence(encode(tampered),core=core,core_raw=raw,
            request=request,catalog=c,expected_model_sha256='e'*64,
            prepared_audio=SimpleNamespace(sha256='1'*64,frame_count=16000,sample_rate=16000))

    def test_multiple_faces_not_used_as_population_measurement(self):
        c=catalog();core,request,pcm,features=fixture(c,reason='MULTIPLE_FACES')
        result=assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
            prepared_pcm=pcm,catalog=c,assessment_policy=ASSESSMENT)
        self.assertIsNone(result['visual']['score']);self.assertEqual(result['corpusComparisons'],{})

    def test_partially_occluded_vowel_retains_shape_only(self):
        c=catalog();core,request,pcm,features=fixture(c,reason='LOW_VISIBILITY')
        result=assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
            prepared_pcm=pcm,catalog=c,assessment_policy=ASSESSMENT)
        self.assertEqual(result['visual']['score'],100)
        self.assertTrue(all(m['criterionId']=='lip_aperture' for m in result['corpusComparisons']['0']))


class CorpusSources(unittest.TestCase):
    def test_official_byte_offset_part_names_and_gap_rejection(self):
        parts=[]
        for offset in (0,1073741824,2147483648):
            p=tarfile.TarInfo('TS50.tar.part'+str(offset));p.size=1073741824;parts.append(p)
        self.assertEqual(ordered_tar_parts(parts[::-1]),parts)
        with self.assertRaisesRegex(ValueError,'PART_MISSING'):ordered_tar_parts([parts[0],parts[2]])

    def test_precore_recovery_preserves_scratch_and_completed_entries(self):
        from voice_coach.visual_feedback.corpus_recovery import recover
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';source.mkdir();plan=self.create(source)
            manifest=index_sources(plan,source);workspace=root/'workspace';workspace.mkdir()
            (workspace/'.corpus-resident.lock').write_bytes(b'')
            measurements=workspace/'measurements';measurements.mkdir()
            target=measurements/manifest['entries'][0]['entryId'];target.mkdir();(target/'partial').write_bytes(b'preserve')
            config=dict(residentWorkspaceRoot=str(workspace))
            report=asyncio.run(recover(encode(manifest),source,measurements,config,b''))
            self.assertEqual(report['precoreScratchPreserved'],1)
            self.assertEqual(list(measurements.glob('*.precore-*/partial'))[0].read_bytes(),b'preserve')
            target.mkdir();(target/'corpus-receipt.json').write_bytes(b'completed')
            report=asyncio.run(recover(encode(manifest),source,measurements,config,b''))
            self.assertEqual((target/'corpus-receipt.json').read_bytes(),b'completed')
            self.assertEqual(report['recoveredWithoutCoreReplay'],0)

    def test_ambiguous_native_execution_is_marked_without_replay(self):
        from voice_coach.visual_feedback.corpus_recovery import recover
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';source.mkdir();plan=self.create(source)
            manifest=index_sources(plan,source);workspace=root/'workspace';workspace.mkdir()
            (workspace/'.corpus-resident.lock').write_bytes(b'');measurements=workspace/'measurements';measurements.mkdir()
            target=measurements/manifest['entries'][0]['entryId'];target.mkdir()
            (target/'producer-started.json').write_bytes(encode(dict(executionId='10000000-0000-4000-8000-000000000001',
                requestId='10000000-0000-4000-8000-000000000002')))
            report=asyncio.run(recover(encode(manifest),source,measurements,dict(residentWorkspaceRoot=str(workspace)),b''))
            self.assertEqual(report['nonReplayableFailures'],1)
            self.assertTrue(json.loads((target/'failure.json').read_bytes())['coreReplayForbidden'])
            self.assertTrue((target/'producer-started.json').is_file())

    def create(self,root):
        (root/'Training').mkdir();(root/'Training/clip.mp4').write_bytes(b'synthetic-not-video')
        (root/'Training/label.json').write_bytes(encode(dict(speaker='synthetic-id',kind='E',
            view='A',noise='1',text='synthetic text',start=0,end=1)))
        pointers=dict(speakerId='/speaker',speakerType='/kind',view='/view',noise='/noise',
            text='/text',startSeconds='/start',endSeconds='/end')
        return dict(schemaVersion=PLAN_SCHEMA,datasetId='538',entries=[dict(video='Training/clip.mp4',
            label='Training/label.json',pointers=pointers,cohortValues=dict(speakerType='E',view='A',noise='1'))])

    def test_manifest_rederived_from_hash_bound_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan=self.create(root);m=index_sources(plan,root);verify_sources(m,root)
            self.assertNotIn('synthetic-id',json.dumps(m))
            changed=deepcopy(m);changed['entries'][0]['scriptText']='changed'
            with self.assertRaisesRegex(ValueError,'MANIFEST_MISMATCH'):verify_sources(changed,root)
            (root/'Training/clip.mp4').write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_sources(m,root)

    def test_private_output_no_overwrite_or_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'output.json';private_write(p,b'private')
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):private_write(p,b'replacement')
            with self.assertRaises(ValueError):contained(root,'../escape')

    def test_source_plan_cannot_just_assert_expert_or_test_train(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);plan=self.create(root)
            label=root/'Training/label.json';m=json.loads(label.read_bytes());m['kind']='G';label.write_bytes(encode(m))
            with self.assertRaisesRegex(ValueError,'COHORT'):index_sources(plan,root)
            m['kind']='E';label.write_bytes(encode(m));(root/'Training').rename(root/'Validation')
            for k in ('video','label'):plan['entries'][0][k]=plan['entries'][0][k].replace('Training','Validation')
            with self.assertRaisesRegex(ValueError,'TRAIN_REQUIRED'):index_sources(plan,root)

    def test_real_label_shape_adapter_ignores_receipts_and_short_sentences(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder=root/'1.Training';folder.mkdir()
            (folder/'lip_sample.mp4').write_bytes(b'synthetic')
            (folder/'lip_sample.mp4.acquisition.json').write_bytes(b'{}')
            value=[dict(speaker_info=dict(speaker_ID='synthetic',Specificity='E'),Audio_env=dict(Noise=1),
                Video_env=dict(Angle='A'),Video_info=dict(video_Name='lip_sample.mp4'),Sentence_info=[
                    dict(sentence_text='synthetic',start_time=0,end_time=1),
                    dict(sentence_text='short',start_time=1,end_time=1.1)])]
            (folder/'lip_sample.json').write_bytes(encode(value))
            plan,report=plan_from_labels(root)
            self.assertEqual(report['matchedSentences'],1);self.assertEqual(report['excludedSentenceIntervals'],1)
            self.assertEqual(len(index_sources(plan,root)['entries']),1)

    def test_stream_collection_handles_tar_parts_without_dropping_bytes(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from collect_aihub538_frontals import stream_collect
        inner=io.BytesIO()
        with tarfile.open(fileobj=inner,mode='w') as t:
            for name,payload in [('A.mp4',b'a'*12345),('skip.mp4',b'x'*20000),('B.mp4',b'b'*1234)]:
                m=tarfile.TarInfo('source/'+name);m.size=len(payload);t.addfile(m,io.BytesIO(payload))
        raw=inner.getvalue();outer=io.BytesIO()
        with tarfile.open(fileobj=outer,mode='w') as t:
            chunks=[raw[:15001],raw[15001:]]
            for i,payload in enumerate(chunks):
                m=tarfile.TarInfo('Training/bundle.tar.part'+str(i));m.size=len(payload);t.addfile(m,io.BytesIO(payload))
        class Response(io.BytesIO):status=200
        class Opener:
            def open(self,*args,**kwargs):return Response(outer.getvalue())
        remote=SimpleNamespace(recheck=lambda:None,opener=Opener(),file_key='55026',key='synthetic',network_bytes=0)
        with tempfile.TemporaryDirectory() as d,patch('builtins.print'):
            result=stream_collect(remote,{'A.mp4':{},'B.mp4':{}},Path(d),100000)
            self.assertEqual(result['videoCount'],2)
            self.assertEqual((Path(d)/'A.mp4').read_bytes(),b'a'*12345)
            self.assertEqual((Path(d)/'B.mp4').read_bytes(),b'b'*1234)
            self.assertFalse((Path(d)/'skip.mp4').exists())

    def test_completed_archive_joins_lexically_ordered_parts_numerically(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from collect_aihub538_frontals import from_download
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);labels=root/'labels';labels.mkdir();payload=b'a'*25000
            (labels/'lip_test.json').write_bytes(encode([dict(speaker_info=dict(speaker_ID='synthetic',Specificity='E'),
                Audio_env=dict(Noise=1),Video_env=dict(Angle='A'),Video_info=dict(video_Name='A.mp4'))]))
            inner=io.BytesIO()
            with tarfile.open(fileobj=inner,mode='w') as t:
                m=tarfile.TarInfo('source/A.mp4');m.size=len(payload);t.addfile(m,io.BytesIO(payload))
            raw=inner.getvalue();chunks={f'bundle.tar.part{i}':raw[offset:offset+1501]
                for i,offset in enumerate(range(0,len(raw),1501))}
            archive=root/'download.tar'
            with tarfile.open(archive,'w') as t:
                for name,data in sorted(chunks.items()):
                    m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
            receipt=root/'receipt.json';receipt.write_bytes(encode(dict(
                schema_version='voice_coach.aihub_secure_download_receipt.v1',dataset_key='538',
                file_keys=['55026'],archive_size_bytes=archive.stat().st_size,archive_sha256=sha(archive.read_bytes()))))
            result=from_download(archive,receipt,labels,root/'output',1,100000)
            self.assertEqual(result['videoCount'],1);self.assertEqual((root/'output/A.mp4').read_bytes(),payload)


if __name__=='__main__':unittest.main()
