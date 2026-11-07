"""Idle connections between audio analyses must not cause ambiguous POST retries."""
import http.client
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from voice_coach.backend_analysis.canonical_transport import FrozenOpenAITransport


class Socket:
    def settimeout(self, value):
        pass


class Response:
    status=200
    def __init__(self):self.done=False
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read1(self, size):
        if self.done:return b''
        self.done=True
        return b'{}'


class Connection:
    def __init__(self):self.sock=None;self.posts=0;self.closed=False
    def connect(self):self.sock=Socket()
    def request(self,*args,**kw):self.posts+=1
    def getresponse(self):return Response()
    def close(self):self.closed=True;self.sock=None


class CanonicalTransportLifecycle(unittest.TestCase):
    def setUp(self):
        self.transport=FrozenOpenAITransport('test-only-key',model='gpt-6.1-terra')
        self.payload=json.dumps(self.transport._expected | {'messages':[{'role':'user','content':'{}'}]}).encode()

    def test_consecutive_analyses_use_fresh_connections_and_one_post_each(self):
        connections=[]
        def create(*args,**kwargs):
            conn=Connection();connections.append(conn);return conn
        with patch('voice_coach.backend_analysis.canonical_transport.http.client.HTTPSConnection',side_effect=create):
            self.transport(self.payload,timeout=120)
            self.transport(self.payload,timeout=120)
        self.assertEqual(len(connections),2)
        self.assertTrue(connections[0].closed)
        self.assertEqual([conn.posts for conn in connections],[1,1])
        self.assertFalse(self.transport.diagnostics['connectionReused'])

    def test_uncertain_post_is_never_retried(self):
        conn=Connection()
        def fail():raise http.client.RemoteDisconnected()
        conn.getresponse=fail
        with patch('voice_coach.backend_analysis.canonical_transport.http.client.HTTPSConnection',return_value=conn) as create:
            with self.assertRaisesRegex(ValueError,'CANONICAL_PROVIDER_UNAVAILABLE'):
                self.transport(self.payload,timeout=120)
        self.assertEqual(create.call_count,1)
        self.assertEqual(conn.posts,1)
        self.assertEqual(self.transport.diagnostics['failureExceptionType'],'RemoteDisconnected')


class LunaStandardCompatibility(unittest.TestCase):
    def test_both_new_lanes_and_previous_lanes_remain_serializable(self):
        from voice_coach.backend_analysis.canonical_harness import (
            LIP, LIP_INTERNAL, LUNA_STANDARD, LUNA_STANDARD_INTERNAL,
            check_result_classification, check_schema_registration,
        )
        from voice_coach.backend_analysis.native_execution import APPROVED_NATIVE_EXECUTIONS
        for current, previous in ((LUNA_STANDARD, LIP), (LUNA_STANDARD_INTERNAL, LIP_INTERNAL)):
            with self.subTest(lane=current.revision):
                self.assertEqual(current.core_sha256, previous.core_sha256)
                self.assertEqual(current.prompt_sha256, previous.prompt_sha256)
                self.assertIn((current.core_sha256, current.manifest_sha256), APPROVED_NATIVE_EXECUTIONS)
                for identity in (current, previous):
                    check_schema_registration(identity)
                    check_result_classification(identity)
                transport = FrozenOpenAITransport.for_harness('test-only-key', current)
                self.assertEqual(transport._expected['model'], 'gpt-6-luna')
                self.assertEqual(transport._expected['reasoning_effort'], 'none')
                self.assertEqual(transport._expected['service_tier'], 'default')

    def test_luna_policy_accepts_none_without_relaxing_astra(self):
        from pathlib import Path
        from voice_coach.six_dimension.policy import load_policy, validate_policy
        policy, _ = load_policy(Path(__file__).resolve().parents[2] /
                                'configs/pronunciation_feedback/six_dimension_v1.json')
        self.assertEqual(policy['request']['model'], 'gpt-6-luna')
        self.assertEqual(policy['request']['reasoning_effort'], 'none')
        self.assertEqual(policy['request']['service_tier'], 'default')
        policy['request']['model'] = 'gpt-6-astra'
        with self.assertRaisesRegex(ValueError, 'POLICY_REQUEST'):
            validate_policy(policy)


if __name__=='__main__':unittest.main()
