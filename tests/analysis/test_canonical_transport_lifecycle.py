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


if __name__=='__main__':unittest.main()
