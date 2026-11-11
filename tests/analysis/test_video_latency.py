"""Independent decode overlap and request-owned cancellation semantics."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_coach.visual_feedback.adapters.canonical_video_features import (
    CanonicalVideoFeatures, VideoFeatureConfiguration)


class VideoLatency(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, fail):
        entered_features, entered_pcm = asyncio.Event(), asyncio.Event()
        state = {}
        metadata = dict(streams=[
            dict(index=0,codec_type='video',codec_name='h264',width=320,height=240,time_base='1/30'),
            dict(index=1,codec_type='audio',codec_name='aac',sample_rate='16000',time_base='1/16000')],
            frames=[dict(stream_index=0,best_effort_timestamp=0),
                    dict(stream_index=1,best_effort_timestamp=0,nb_samples=1600)])

        class Port(CanonicalVideoFeatures):
            async def _capture(self, command, *, descriptor, limit):
                if str(command[0]).endswith('ffprobe'):
                    return json.dumps(metadata).encode()
                state['descriptor'] = descriptor
                entered_pcm.set()
                try:
                    await entered_features.wait()
                    if fail: await asyncio.Future()
                    return b'\x00'*3200
                finally:
                    os.fstat(descriptor)
                    state['pcmDrainedBeforeDescriptorClose'] = True

            async def _features(self, header, locator, descriptor):
                entered_features.set()
                await entered_pcm.wait()
                if fail: raise ValueError('FIXTURE_FEATURE_FAILURE')
                return dict(protocol='fixture',mappingRevision='fixture',frames=[])

        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'video';raw=b'verified-local-video';p.write_bytes(raw)
            port=Port(VideoFeatureConfiguration(Path('/python'),Path('/model'),'0'*64,{}))
            with patch('voice_coach.visual_feedback.adapters.canonical_video_features.validate_header',side_effect=lambda d:d):
                call=port.extract(video_path=p,video_sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw))
                if fail:
                    with self.assertRaisesRegex(ValueError,'FIXTURE_FEATURE_FAILURE'):
                        await asyncio.wait_for(call,1)
                else:
                    result=await asyncio.wait_for(call,1)
                    self.assertEqual(result['decodedPcmSha256'],hashlib.sha256(b'\x00'*3200).hexdigest())
                    self.assertEqual(result['decodedPcmFrames'],1600)
            self.assertTrue(state['pcmDrainedBeforeDescriptorClose'])
            with self.assertRaises(OSError):os.fstat(state['descriptor'])

    async def test_pcm_verification_and_features_overlap(self):
        await self.exercise(False)

    async def test_failed_features_drain_pcm_before_closing_input(self):
        await self.exercise(True)
