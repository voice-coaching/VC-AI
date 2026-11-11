"""Existing-assets promotion retains admission, scratch and rollback budgets."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS=Path(__file__).resolve().parents[2]/'deploy/dlpc'
sys.path.insert(0,str(TOOLS))
spec=importlib.util.spec_from_file_location('headroom_coordinated',TOOLS/'coordinated_release.py')
co=importlib.util.module_from_spec(spec);spec.loader.exec_module(co)
GIB=1024**3


class DeploymentHeadroom(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'asset').write_bytes(b'asset')
        self.manifest=dict(files={'test.py':{}},before={'test.py':None},
            coreReleases={'internal':{'asset':'pin'}},selection={'AI_CANONICAL_CORE_ROOT':str(self.root)})
        self.state=dict(capacityPlan=dict(basis='SHORT_AUDIO_PLANNING_ESTIMATE_NOT_PEAK_QA',
            inventory_complete=True,request_limit=12,compute_limit=3,
            disk_per_worker=256*1024**2,tmp_per_worker=64*1024**2))

    def budget(self,free=13*GIB,tmp_free=2*GIB,shared=True,rollback=24):
        original=co.os.stat
        def stat(path,*a,**kw):
            if str(path) in ('/var/lib','/tmp'):
                return SimpleNamespace(st_dev=1 if str(path)=='/var/lib' or shared else 2)
            return original(path,*a,**kw)
        with patch.object(co,'owned',return_value=b'x'*16), \
             patch.object(co,'source_bytes',return_value=b'x'*rollback), \
             patch.object(co.os,'stat',side_effect=stat), \
             patch.object(co.shutil,'disk_usage',side_effect=lambda p:SimpleNamespace(free=tmp_free if p=='/tmp' else free)):
            return co.disk_headroom(self.root,self.manifest,self.state)

    def test_installed_assets_keep_all_twelve_slots_and_both_reserves(self):
        result=self.budget()
        self.assertEqual(result['rootRequiredBytes'],int(12.75*GIB)+32+24)
        self.assertEqual(result['admittedSlots'],12)
        self.assertEqual(result['policy'],'INSTALLED_ASSETS_ADMISSION_BUDGET')

    def test_insufficient_actual_budget_still_fails(self):
        with self.assertRaisesRegex(ValueError,'COORDINATED_DISK_HEADROOM'):self.budget(free=12*GIB)

    def test_missing_asset_retains_legacy_reserve(self):
        (self.root/'asset').unlink()
        with self.assertRaisesRegex(ValueError,'COORDINATED_DISK_HEADROOM'):self.budget()
        self.assertEqual(self.budget(free=15*GIB)['policy'],'LEGACY_UNKNOWN_OR_NEW_ASSETS')

    def test_unknown_inventory_retains_legacy_reserve(self):
        self.state['capacityPlan']['inventory_complete']=False
        with self.assertRaisesRegex(ValueError,'COORDINATED_DISK_HEADROOM'):self.budget()

    def test_separate_tmp_filesystem_has_its_own_check(self):
        with self.assertRaisesRegex(ValueError,'COORDINATED_TMP_HEADROOM'):
            self.budget(shared=False,tmp_free=int(1.5*GIB))
        result=self.budget(shared=False)
        self.assertEqual(result['rootRequiredBytes'],11*GIB+32+24)
        self.assertEqual(result['tmpRequiredBytes'],int(1.75*GIB))

    def test_large_rollback_cannot_be_omitted(self):
        with self.assertRaisesRegex(ValueError,'COORDINATED_DISK_HEADROOM'):
            self.budget(rollback=512*1024**2)
