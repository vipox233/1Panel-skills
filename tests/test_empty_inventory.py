"""Regression for 1Panel zero-row inventories. All panel responses are mocked."""
import tempfile
import unittest
from unittest.mock import patch

from test_ops import Assertions, FakePanel, GOOD
from ops.common import OpsError, Store, records
from ops.operations import Operations


class NullEmptyPanel(FakePanel):
    """1Panel emits null for an empty page (including the Compose inventory)."""
    def call(self, method, path, body=None):
        result = super().call(method, path, body)
        if path in (
            '/api/v2/containers/compose/search',
            '/api/v2/containers/search',
            '/api/v2/logs/tasks/search',
        ) and isinstance(result, dict) and result.get('total') == 0 and result.get('items') == []:
            return {'total': 0, 'items': None}
        return result


class EmptyInventoryTests(Assertions):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = NullEmptyPanel()
        self.ops = Operations(self.api, Store(self.tmp.name + '/state'))

    def test_1panel_empty_inventory_and_missing_task(self):
        self.assertEqual(self.ops.compose_rows(), [])
        self.assertEqual(self.ops.container_rows(), [])
        self.assertIsNone(self.ops.task_state('missing-task'))
        self.assertEqual(self.api.writes, [])

    def test_compose_creation_on_empty_inventory(self):
        report = {'ports': [], 'warnings': [], 'mounts': {}, 'images': {}}
        with patch('ops.operations.validate_compose', return_value=report), \
             patch('ops.operations.occupied_ports', return_value=[]):
            plan = self.ops.plan({
                'operation': 'compose.create',
                'target': {'name': 'demo'},
                'change': {'content': GOOD},
            })
            self.assertEqual(plan['status'], 'planned')
            self.assertEqual(self.api.writes, [])
            applied = self.ops.apply(plan)
        self.assertEqual(applied['status'], 'verified')
        self.assertEqual(self.api.compose['name'], 'demo')

    def test_zero_null_requires_explicit_integer_count(self):
        for key in ('items', 'records', 'data'):
            with self.subTest(key=key):
                self.assertEqual(records({'total': 0, key: None}), [])
        for value in (
            {'total': 1, 'items': None},
            {'total': False, 'items': None},
            {'total': -1, 'items': None},
            {'items': None},
            {'total': 0},
            {'total': 0, 'items': 'invalid'},
        ):
            with self.subTest(value=value):
                self.denied('API_SHAPE', records, value)
        self.assertEqual(records({'total': 1, 'items': [{'name': 'existing'}]}),
                         [{'name': 'existing'}])

    def test_nonempty_inventory_with_empty_page_is_rejected(self):
        with patch.object(self.api, 'call', return_value={'total': 1, 'items': []}):
            self.denied('API_SHAPE', self.ops.compose_rows)
        with patch.object(self.api, 'call', return_value={'total': 1, 'items': None}):
            self.denied('API_SHAPE', self.ops.compose_rows)

    def test_pagination_missing_second_page_is_rejected(self):
        first = {'total': 101, 'items': [{'name': f'project-{i}'} for i in range(100)]}
        second = {'total': 101, 'items': []}
        with patch.object(self.api, 'call', side_effect=[first, second]):
            self.denied('API_SHAPE', self.ops.compose_rows)

    def test_nonempty_short_page_must_not_hide_existing_compose(self):
        # The missing final row could contain the target project.
        short_page = {'total': 101, 'items': [{'name': f'project-{i}'} for i in range(99)]}
        with patch.object(self.api, 'call', return_value=short_page):
            self.denied('API_SHAPE', self.ops.compose_rows)
            self.denied('API_SHAPE', self.ops.plan, {
                'operation': 'compose.create',
                'target': {'name': 'demo'},
                'change': {'content': GOOD},
            })
        self.assertEqual(self.api.writes, [])

    def test_second_page_missing_one_row_must_not_be_accepted(self):
        first = {'total': 102, 'items': [{'name': f'project-{i}'} for i in range(100)]}
        second = {'total': 102, 'items': [{'name': 'project-100'}]}
        with patch.object(self.api, 'call', side_effect=[first, second]):
            self.denied('API_SHAPE', self.ops.compose_rows)

    def test_exact_short_final_page_still_succeeds(self):
        first = {'total': 101, 'items': [{'name': f'project-{i}'} for i in range(100)]}
        second = {'total': 101, 'items': [{'name': 'project-100'}]}
        with patch.object(self.api, 'call', side_effect=[first, second]):
            rows = self.ops.compose_rows()
        self.assertEqual(len(rows), 101)


    def test_failed_compose_with_null_containers_is_readable(self):
        self.api.compose = {
            'name': 'demo', 'configFile': '/tmp/demo.yml',
            'env': '', 'createdBy': '1Panel', 'content': GOOD,
            'containers': None, 'containerCount': 0, 'runningCount': 0,
        }
        details = self.ops.compose('demo')
        self.assertEqual(details['images'], {})
        self.assertEqual(details['image_refs'], {})
        self.assertEqual(self.api.writes, [])

    def test_inconsistent_compose_container_counts_are_rejected(self):
        self.api.compose = {
            'name': 'demo', 'configFile': '/tmp/demo.yml',
            'env': '', 'createdBy': '1Panel', 'content': GOOD,
            'containers': None, 'containerCount': 0, 'runningCount': 0,
        }
        for count, containers in ((1, None), (1, []), (0, 'wrong')):
            with self.subTest(count=count, containers=containers):
                self.api.compose.update(containerCount=count, containers=containers)
                self.denied('API_SHAPE', self.ops.compose, 'demo')
        self.assertEqual(self.api.writes, [])

    def test_failed_empty_compose_task_can_release_pending_record(self):
        report = {'ports': [], 'warnings': [], 'mounts': {}, 'images': {}}
        with patch('ops.operations.validate_compose', return_value=report), \
             patch('ops.operations.occupied_ports', return_value=[]):
            old = self.ops.plan({
                'operation': 'compose.create',
                'target': {'name': 'demo'},
                'change': {'content': GOOD},
            })
            self.api.task_status = 'Waiting'
            self.assertEqual(self.ops.apply(old)['status'], 'submitted')
            self.api.task_status = 'Failed'
            self.api.compose.update(containers=None, containerCount=0, runningCount=0)
            self.assertEqual(self.ops.verify(old)['status'], 'failed')
            new = self.ops.plan({
                'operation': 'compose.create',
                'target': {'name': 'new-project'},
                'change': {'content': GOOD},
            })
            self.assertEqual(new['status'], 'planned')

    def test_empty_failed_compose_update_checks_use_normalized_containers(self):
        self.api.compose = {
            'name': 'demo', 'configFile': '/tmp/demo.yml',
            'env': '', 'createdBy': '1Panel', 'content': GOOD,
            'containers': None, 'containerCount': 0, 'runningCount': 0,
        }
        before = self.ops.compose('demo')
        report = {'ports': [], 'warnings': [], 'mounts': {'web': []}, 'images': {}}
        with patch('ops.operations.validate_compose', return_value=report), \
             patch('ops.operations.occupied_ports', return_value=[]):
            self.ops._compose_checks(GOOD, 'demo', '', {}, before)
            self.api.compose['containerCount'] = 1
            self.denied('API_SHAPE', self.ops._compose_checks, GOOD, 'demo', '', {}, before)
        self.assertEqual(self.api.writes, [])


if __name__ == '__main__':
    unittest.main()
