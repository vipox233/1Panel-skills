"""Real Docker Compose parser test: no container creation and no daemon access."""
from pathlib import Path
import shutil
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ops.common import OpsError
from ops.validators import validate_compose

@unittest.skipUnless(shutil.which('docker'), 'Docker Compose CLI not installed')
class ComposeCliTests(unittest.TestCase):
    def test_actual_parser(self):
        result=validate_compose('services:\n  app:\n    image: nginx:alpine\n    volumes: ["./data:/data"]\n    ports: ["127.0.0.1:18991:80"]\n','parser-test')
        self.assertEqual(result['services'],['app']); self.assertEqual(result['ports'][0]['port'],18991)
    def test_resolved_dangerous_env_path_rejected(self):
        with self.assertRaises(OpsError):
            validate_compose('services:\n  app:\n    image: nginx:alpine\n    volumes: ["${DATA}:/data"]\n','parser-test','DATA=/etc')
    def test_unset_variable_rejected(self):
        with self.assertRaises(OpsError):
            validate_compose('services:\n  app:\n    image: nginx:${MISSING_VALUE}\n','parser-test')

if __name__ == '__main__': unittest.main()
