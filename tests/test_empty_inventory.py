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


if __name__ == '__main__':
    unittest.main()
