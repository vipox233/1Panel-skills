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
        result=validate_compose('services:\n  app:\n    image: nginx@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n    volumes: ["./data:/data"]\n    ports: ["127.0.0.1:18991:80"]\n','parser-test')
        self.assertEqual(result['services'],['app']); self.assertEqual(result['ports'][0]['port'],18991)
    def test_resolved_dangerous_env_path_rejected(self):
        with self.assertRaises(OpsError):
            validate_compose('services:\n  app:\n    image: nginx@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n    volumes: ["${DATA}:/data"]\n','parser-test','DATA=/etc')
    def test_env_resolved_mount_change_is_blocked_but_unrelated_env_is_allowed(self):
        import tempfile
        from unittest.mock import patch
        from test_contracts import StatefulPanel
        from ops.common import Store
        from ops.operations import Operations
        with tempfile.TemporaryDirectory() as tmp:
            old_dir=Path(tmp)/'srv-old'; new_dir=Path(tmp)/'srv-new'
            old_dir.mkdir(); new_dir.mkdir()
            source='services:\n  web:\n    image: nginx@sha256:'+'a'*64+'\n    volumes: ["${DATA_DIR}:/data"]\n    ports: ["127.0.0.1:${PORT}:80"]\n'
            old_env=f'DATA_DIR={old_dir}\nPORT=18991\nUNRELATED=old\n'
            api=StatefulPanel()
            api.compose={'name':'demo','configFile':'/tmp/demo.yml','content':source,'env':old_env,'createdBy':'1Panel',
                         'containers':[{'name':'demo-web-1','containerID':'c1'}],'containerCount':1,'runningCount':1}
            ops=Operations(api,Store(str(Path(tmp)/'state')))
            request={'operation':'compose.update','target':{'name':'demo'},'change':{'content':source},
                     'options':{'bind_roots':[str(old_dir),str(new_dir)]}}
            # Exercise the real Compose renderer. Both mount roots are explicitly
            # allowed, so only comparing old/new resolved mounts catches this.
            with patch('ops.operations.occupied_ports',return_value=[('tcp','127.0.0.1',18991)]):
                with self.assertRaises(OpsError) as caught:
                    ops.plan({**request,'change':{'content':source,'env':old_env.replace(str(old_dir),str(new_dir))}})
                self.assertEqual(caught.exception.code,'MOUNT_CHANGE')
                self.assertEqual(api.writes,[])
                result=ops.plan({**request,'change':{'content':source,'env':old_env.replace('UNRELATED=old','UNRELATED=new')}})
            self.assertEqual(result['status'],'planned')
            self.assertEqual(api.writes,[])
    def test_unset_variable_rejected(self):
        with self.assertRaises(OpsError):
            validate_compose('services:\n  app:\n    image: nginx:${MISSING_VALUE}\n','parser-test')

if __name__ == '__main__': unittest.main()
