"""Regression for cache-heavy idle hosts and genuine per-request RAM shortage."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice_coach.backend_analysis.cgroup_resources import memory_limits
from voice_coach.backend_analysis.capacity_planning import GIB, capacity_plan
from voice_coach.backend_analysis.resource_budget import ResourceBudget, available_memory_bytes


class MemoryAdmission(unittest.TestCase):
    def cgroup(self, version, *, limit=40*GIB, used=36*GIB, stats=''):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        (root/('memory.limit_in_bytes' if version==1 else 'memory.max')).write_text(str(limit))
        (root/('memory.usage_in_bytes' if version==1 else 'memory.current')).write_text(str(used))
        (root/'memory.stat').write_text(stats)
        return version,root

    def test_v1_hierarchical_clean_cache_only_and_standby_accounting_unchanged(self):
        cg=self.cgroup(1,stats=f'total_inactive_file {8*GIB}\ninactive_file {GIB}\ntotal_dirty {2*GIB}\ntotal_writeback {GIB}\ntotal_active_file {12*GIB}\ncache {24*GIB}\n')
        with patch('voice_coach.backend_analysis.cgroup_resources.hierarchies',return_value=[cg]):
            self.assertEqual(memory_limits()[0]['available'],4*GIB)
            self.assertEqual(memory_limits(reclaim_file_cache=True)[0]['available'],9*GIB)

    def test_v2_cache_and_tighter_ancestor_are_both_considered(self):
        leaf=self.cgroup(2,stats=f'inactive_file {8*GIB}\nfile_dirty {GIB}\nfile_writeback {GIB}\n')
        parent=self.cgroup(2,limit=100*GIB,used=98*GIB,stats=f'inactive_file {GIB}\n')
        with patch('voice_coach.backend_analysis.cgroup_resources.hierarchies',return_value=[leaf,parent]):
            limits=memory_limits(reclaim_file_cache=True)
        self.assertEqual([v['available'] for v in limits],[10*GIB,3*GIB])
        with patch('voice_coach.backend_analysis.resource_budget.memory_limits',return_value=limits),patch('pathlib.Path.read_text',return_value=f'MemAvailable: {20*GIB//1024} kB\n'):
            self.assertEqual(available_memory_bytes(),3*GIB)

    def test_invalid_cache_does_not_invent_headroom(self):
        for stats in ['inactive_file -1\n','inactive_file invalid\n',f'inactive_file {50*GIB}\n','garbage','inactive_file 1\nfile_dirty -1\n','']:
            with self.subTest(stats=stats):
                cg=self.cgroup(2,stats=stats)
                with patch('voice_coach.backend_analysis.cgroup_resources.hierarchies',return_value=[cg]):
                    self.assertEqual(memory_limits(reclaim_file_cache=True)[0]['available'],4*GIB)

    def test_unknown_memory_remains_unknown_and_denies_admission(self):
        with patch('voice_coach.backend_analysis.resource_budget.memory_limits',return_value=[]),patch('pathlib.Path.read_text',side_effect=OSError()):
            self.assertIsNone(available_memory_bytes())
        with patch('voice_coach.backend_analysis.resource_budget.available_memory_bytes',return_value=None):
            self.assertFalse(ResourceBudget(minimum_available_bytes=3*GIB).can_admit())

    def test_incident_numbers_pass_but_real_shortage_and_exact_boundary_work(self):
        budget=ResourceBudget(minimum_available_bytes=3*GIB)
        for free,allowed in [(25*GIB,True),(3*GIB,True),(3*GIB-1,False)]:
            with patch('voice_coach.backend_analysis.resource_budget.available_memory_bytes',return_value=free):
                self.assertEqual(budget.can_admit(),allowed)
                self.assertEqual(budget.can_admit(large_input=True),allowed)

    def test_profile_and_slot_plan_no_longer_require_twenty_four_extra_gib(self):
        inventory={0:dict(total=80*GIB,free=70*GIB)}
        plan=capacity_plan(profile='dlpc-4cpu',cpu_count=16,memory_available=3*GIB,disk_free=20*GIB,tmp_free=4*GIB,gpus=inventory)
        self.assertEqual((plan.request_limit,plan.compute_limit,plan.ram_limit),(1,1,1))
        with patch('voice_coach.backend_analysis.resource_budget.available_memory_bytes',return_value=3*GIB),patch('voice_coach.backend_analysis.resource_budget.effective_cpu_count',return_value=16),patch('voice_coach.backend_analysis.resource_budget.free_bytes',return_value=20*GIB),patch('voice_coach.backend_analysis.resource_budget.gpu_memory',return_value=inventory):
            budget=ResourceBudget.from_environment({'AI_ANALYSIS_RESOURCE_PROFILE':'dlpc-4cpu'})
        self.assertEqual(budget.minimum_available_bytes,3*GIB)
        with patch('voice_coach.backend_analysis.resource_budget.available_memory_bytes',return_value=GIB),patch('voice_coach.backend_analysis.resource_budget.effective_cpu_count',return_value=16),patch('voice_coach.backend_analysis.resource_budget.free_bytes',return_value=20*GIB),patch('voice_coach.backend_analysis.resource_budget.gpu_memory',return_value=inventory):
            self.assertEqual(ResourceBudget.from_environment({'AI_ANALYSIS_RESOURCE_PROFILE':'dlpc-4cpu'}).max_active,0)

    def test_other_resource_checks_are_not_removed(self):
        with patch('voice_coach.backend_analysis.resource_budget.available_memory_bytes',return_value=5*GIB),patch('voice_coach.backend_analysis.resource_budget.gpu_memory',return_value={0:dict(free=GIB)}):
            self.assertFalse(ResourceBudget(minimum_available_bytes=3*GIB,gpu_indices=(0,),minimum_gpu_free_bytes=10*GIB).can_admit())
