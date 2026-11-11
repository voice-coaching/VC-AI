"""Clock/word/reference regression; synthetic fixtures, no provider requests."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from uuid import uuid4
import wave

from test_lip_scoring import (trajectory,reference,frames,DISTANCE,POLICY,REQUEST,
    EXECUTION,audio_assessment)
from voice_coach.visual_feedback.audio_timeline import sample_clock
from voice_coach.visual_feedback.alignment_recovery import recover_word_alignment
from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures
from voice_coach.visual_feedback.audiovisual_evidence import PhoneAnchor,assess_phone
from voice_coach.visual_feedback.lip_scoring import enrich_phone
from voice_coach.visual_feedback.canonical_service import assess_aligned,PARTIAL_ASSESSMENT
from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
from voice_coach.visual_feedback.feature_protocol import MAPPING,PROTOCOL
from voice_coach.backend_analysis.browser_video_normalize import normalize
from voice_coach.six_dimension.visual import visual_context,feedback_targets
from voice_coach.six_dimension.policy import encode,sha


class ClockRegression(unittest.TestCase):
    def test_native_cpu_restriction_is_local_to_the_visual_child(self):
        before=os.sched_getaffinity(0)
        r=subprocess.run([sys.executable,'-B','-c',
            'import os; from voice_coach.visual_feedback.adapters.canonical_feature_worker import constrain_native_worker_cpu; '
            'allowed=os.sched_getaffinity(0); constrain_native_worker_cpu(); '
            'assert os.sched_getaffinity(0)=={min(allowed)}'],
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[2]/'src')),
            capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr.decode())
        self.assertEqual(os.sched_getaffinity(0),before)

    def test_browser_jitter_is_classified_for_derivative_not_accepted_as_exact(self):
        stream=dict(index=1,sample_rate='16000',time_base='1/16000')
        rows=[dict(stream_index=1,best_effort_timestamp=p,nb_samples=1024)
              for p in [256,1280,2291,3327,4339]]
        r=sample_clock(stream,rows)
        self.assertTrue(r['requiresRegularization'])
        self.assertLessEqual(r['maximumJitterSeconds'],.002)
        self.assertEqual((r['firstPtsNumerator'],r['firstPtsDenominator']),(2,125))
        self.assertIsNone(CanonicalVideoFeatures._audio_clock(stream,rows))

    def test_local_tolerance_does_not_hide_accumulating_drift_or_real_gap(self):
        stream=dict(index=0,sample_rate='16000',time_base='1/16000')
        for pts in [[i*(1024+10) for i in range(12)], [0,1024,3000], [0,1024,1024]]:
            rows=[dict(stream_index=0,best_effort_timestamp=p,nb_samples=1024) for p in pts]
            self.assertIsNone(sample_clock(stream,rows))


class WordRecovery(unittest.TestCase):
    def fixture(self):
        rows=[]
        for wi,(word,count) in enumerate([('가',2),('2배',2),('모아',4)]):
            for j in range(count):
                rows.append(dict(expected_index=len(rows),word_index=wi,word=word,
                    role='onset' if j%2==0 else 'nucleus',expected_phone='ㅁ' if j%2==0 else 'ㅏ',
                    localization_status='WORD_TEXT_UNRESOLVED' if j%2==0 else 'VOWEL_LOCATION_UNRESOLVED',
                    phone_start_s=None,phone_end_s=None,mfa_ipa=None,word_start_s=None,word_end_s=None))
        words=[dict(label=w,start_s=Decimal(str(i)),end_s=Decimal(str(i+1))) for i,w in enumerate(['가','2','배','모아'])]
        phones=[]
        for i,count in enumerate([2,1,1,4]):
            for j in range(count):
                phones.append(dict(label='spn' if i in [1,2] else 'm' if j%2==0 else 'a',
                    start_s=Decimal(i)+Decimal(j)/count,end_s=Decimal(i)+Decimal(j+1)/count))
        return dict(rows=rows),dict(word_intervals=words,phone_intervals=phones)

    def test_split_number_does_not_shift_later_word_or_assign_unknown_times(self):
        a,r=self.fixture();original=deepcopy(a)
        fixed=recover_word_alignment(a,r)
        self.assertEqual(a,original)
        later=[x for x in fixed['rows'] if x['word_index']==2 and x['role']=='onset']
        self.assertEqual([x['phone_start_s'] for x in later],[3,3.5])
        self.assertTrue(all(x['phone_start_s'] is None for x in fixed['rows'] if x['word_index']==1))
        self.assertTrue(all(x['phone_start_s'] is None for x in fixed['rows'] if x['role']=='nucleus'))

    def test_nonmatching_word_sequence_does_not_guess(self):
        a,r=self.fixture();r['word_intervals'][2]['label']='다름'
        self.assertEqual(recover_word_alignment(a,r),a)


class CrossWordScoring(unittest.TestCase):
    def row(self,refs,**kwargs):
        a=PhoneAnchor(0,'ㅜ','nucleus','word:구름',0,.6,'MFA_LOCATION_ONLY')
        fs=frames(trajectory())
        from voice_coach.visual_feedback.audiovisual_evidence import VerifiedTimeMap
        row=assess_phone(a,frames=fs,pcm_sha256='a'*64,video_sha256='b'*64,
            time_map=VerifiedTimeMap('a'*64,'b'*64,1,0,'synthetic'),mapping_revision=MAPPING.revision,
            references=refs,policy=DISTANCE,source_sha256='c'*64)
        enrich_phone(row,a,fs,references=refs,policy=POLICY,video_sha256='b'*64,
            source_sha256='c'*64,**kwargs)
        return row

    def test_cross_word_is_explicit_opt_in_and_has_no_timing_score(self):
        self.assertIsNone(self.row([reference()])['score'])
        row=self.row([reference()],allow_cross_word=True)
        self.assertEqual(row['scoreKind'],'PROVISIONAL')
        self.assertEqual(row['score'],100)
        self.assertIn('CROSS_WORD_PROVISIONAL_REFERENCE',row['reasonCodes'])
        self.assertIsNone(row['subScores'][3]['score'])

    def test_no_reference_wrong_role_and_self_comparison_are_not_scores(self):
        for refs in [[],[reference(role='coda')],[replace(reference(),video_sha256='b'*64)]]:
            self.assertIsNone(self.row(refs,allow_cross_word=True)['score'])

    def test_exact_word_reference_takes_priority_over_cross_word(self):
        exact=replace(reference(),reference_id='exact',context='word:구름')
        row=self.row([reference(),exact],allow_cross_word=True)
        self.assertEqual(row['referenceIds'],['exact'])
        self.assertNotIn('CROSS_WORD_PROVISIONAL_REFERENCE',row['reasonCodes'])

    def test_partial_score_replay_public_schema_and_lip_feedback_targets(self):
        refs=(reference(),)
        catalog=ReferenceCatalog('c'*64,'synthetic',DISTANCE,refs,(),.6,POLICY)
        request=SimpleNamespace(event_id=REQUEST,execution_id=EXECUTION,audio_sha256='a'*64,
            visual_input=SimpleNamespace(sha256='b'*64),media_preparation={'receipt':{'sourceSha256':'d'*64}})
        rows=[dict(expected_index=i,expected_phone='ㅜ',role='nucleus',word='구름',
            mfa_ipa='u',localization_status='EXACT_COUNT_WORD_INDEX',word_start_s=i,
            phone_start_s=i,phone_end_s=i+.6,word_end_s=i+.6) for i in range(4)]
        core=dict(request_id=REQUEST,source_audio_sha256='a'*64,prepared_wav_sha256='a'*64,
            mfa_receipt={},expected_phone_count=4,mfa_phone_alignment=dict(
                schemaVersion='voice-coaching.canonical-mfa-phone-alignment.v1',
                indexNamespace='DETECTOR_METADATA_V2',rows=rows))
        raw=encode(core)
        pcm=dict(sha256='a'*64,frames=64000,sampleRate=16000)
        features=dict(videoSha256='b'*64,modelSha256='e'*64,decodedPcmSha256='a'*64,
            decodedPcmFrames=64000,decodedPcmSampleRate=16000,
            audioClock=dict(firstPtsNumerator=0,firstPtsDenominator=1,source='CONTINUOUS_DECODED_AUDIO_FRAMES',sampleRate=16000),
            features=dict(protocol=PROTOCOL,mappingRevision=MAPPING.revision,frames=[dict(
                pts=f.pts_s,landmarks=[list(p) for p in f.landmarks],features=list(f.features),reason=None)
                for f in frames(trajectory())]))
        e=assess_aligned(core=core,request=request,feature_value=features,source_pcm=pcm,
            prepared_pcm=pcm,catalog=catalog,core_raw=raw,assessment_policy=PARTIAL_ASSESSMENT)
        self.assertEqual(encode(core),raw)
        self.assertEqual(e['visual']['score'],100)
        self.assertEqual(e['visual']['coverage']['coverage'],.25)
        self.assertIn('PARTIAL_REFERENCE_COVERAGE',e['visual']['reasonCodes'])
        verify_visual_evidence(encode(e),core=core,request=request,catalog=catalog,expected_model_sha256='e'*64,
            prepared_audio=SimpleNamespace(sha256='a'*64,frame_count=64000,sample_rate=16000),core_raw=raw)
        payload=prepare_visual_coaching(encode(e),core_bytes=raw)
        self.assertEqual(payload['coverage']['scoredCount'],1)
        self.assertEqual(payload['partialScore']['score'],100)
        self.assertIsNone(payload['score'])
        self.assertIn('subScores',payload['phones'][0])
        audio=audio_assessment();audio['association']['coreSha256']=sha(raw)
        context=visual_context(audio,payload)
        self.assertEqual(context['grade']['overallScore'],70.5)
        self.assertEqual(context['grade']['visualWeight'],0)
        selected=feedback_targets(audio,context)
        self.assertEqual(selected[0]['criterionId'],'lip_articulation:0')
        # Existing Frontend-facing visual schema; no new required fields.
        import jsonschema
        schema=json.loads((Path(__file__).parents[2]/'docs/contracts/runpod_result_v6.schema.json').read_text())
        from voice_coach.backend_analysis.canonical_audiovisual_result import public_visual
        jsonschema.Draft202012Validator(dict(schema,**{'$ref':'#/$defs/visual'})).validate(public_visual(e['visual']))


class RealCodecRegression(unittest.IsolatedAsyncioTestCase):
    async def test_opus_mp4_with_quantized_capture_pts_gets_verified_sample_clock(self):
        with tempfile.TemporaryDirectory(dir='/home/2020067/.cache') as tmp:
            root=Path(tmp);source=root/'original-video'
            subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
                'color=c=blue:s=320x240:r=30:d=2','-itsoffset','0.125','-f','lavfi','-i',
                'sine=frequency=440:sample_rate=48000:duration=1.5',
                '-af','asetpts=round(PTS*TB*1000)/1000/TB','-c:v','libx264','-threads','1',
                '-c:a','libopus','-strict','experimental','-f','mp4',str(source)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            raw=source.read_bytes()
            metadata=dict(clientRequestId=str(uuid4()),scriptText='시험 문장',contentId=None,
                source=dict(sha256=hashlib.sha256(raw).hexdigest(),fileSizeBytes=len(raw),mimeType='video/mp4'))
            normalized=await normalize(root,metadata,deadline=time.monotonic()+120)
            probe=json.loads(subprocess.check_output(['/usr/bin/ffprobe','-v','error','-show_streams','-show_frames',
                '-show_entries','stream=index,codec_type,sample_rate,time_base:frame=stream_index,best_effort_timestamp,nb_samples',
                '-of','json',str(root/'video.mp4')]))
            stream=next(s for s in probe['streams'] if s['codec_type']=='audio')
            clock=CanonicalVideoFeatures._audio_clock(stream,probe['frames'])
            self.assertIsNotNone(clock)
            self.assertGreater(clock['firstPtsNumerator']/clock['firstPtsDenominator'],.05)
            pcm=subprocess.check_output(['/usr/bin/ffmpeg','-nostdin','-v','error','-i',str(root/'video.mp4'),
                '-map','0:a:0','-ac','1','-ar','16000','-f','s16le','pipe:1'])
            with wave.open(str(root/'audio.wav')) as wav:self.assertEqual(wav.readframes(wav.getnframes()),pcm)
            self.assertEqual(source.read_bytes(),raw)
            self.assertTrue(json.loads((root/'normalization-observation.json').read_bytes())['audioClockRegularized'])
            self.assertEqual(normalized['mediaPreparation']['receipt']['normalizationRevision'],'ai-original-video-pcm-20261111-v4')


if __name__=='__main__':unittest.main()
