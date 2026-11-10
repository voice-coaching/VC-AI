"""One request owns the GOP future and must join it even when MFA fails."""
import importlib.util
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
import unittest

path = Path(__file__).resolve().parents[2]/'deploy/dlpc/performance/gop_task.py'
spec = importlib.util.spec_from_file_location('gop_overlap_under_test',path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GopOverlap(unittest.TestCase):
    def state(self):
        row=SimpleNamespace(model_dump=lambda **_: {'phone':'ㅏ'})
        return dict(native={'arr':[]},phone_contract=SimpleNamespace(full_metadata=[row]))

    def test_mfa_can_run_before_gop_finishes_and_result_is_unchanged(self):
        entered,release=Event(),Event()
        expected={'samples':[{'gop':-1.25}]}
        def extract(*_):
            entered.set()
            self.assertTrue(release.wait(2))
            return expected
        task=module.GopTask({},extract,{})
        try:
            task.start(self.state())
            self.assertTrue(entered.wait(2))
            self.assertFalse(task.future.done())
            release.set()
            self.assertIs(task.result(self.state()),expected)
        finally:
            release.set();task.close()

    def test_cleanup_joins_work_and_preserves_existing_error_classification(self):
        entered,release,closed=Event(),Event(),Event()
        def extract(*_):
            entered.set();release.wait(2);raise ValueError('private error')
        task=module.GopTask({},extract,{})
        task.start(self.state());self.assertTrue(entered.wait(2))
        waiter=Thread(target=lambda:(task.close(),closed.set()))
        waiter.start()
        self.assertFalse(closed.wait(.05))
        release.set();waiter.join(2)
        self.assertTrue(closed.is_set())
        self.assertEqual(task.future.result(),dict(revision='gop-ctc-af-sd-native-v1',
            status='UNAVAILABLE',errorCode='ValueError'))
