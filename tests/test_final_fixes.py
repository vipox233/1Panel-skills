"""Small regressions for health failure recovery, private state and proxy switching."""
import json
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch
import test_ops
from test_ops import Assertions, GOOD
from ops.common import OpsError, secure_dir
from ops.helpers import container_proxy
from ops.validators import load_yaml

class HealthRecoveryTests(Assertions):
    setUp = test_ops.WorkflowTests.setUp
    config_plan = test_ops.WorkflowTests.config_plan
    def test_invalid_health_is_rejected_before_any_write(self):
        for change in ({'timeout':0},{'timeout':61},{'timeout':True},{'timeout':'10'},
                       {'resolve_ip':''},{'resolve_ip':'not-an-ip'},{'resolve_ip':123},
                       {'codes':[]},{'codes':[99]},{'codes':[600]},{'codes':[True]},
                       {'codes':['200']},{'url':'not-an-http-url'}):
            with self.subTest(change=change), self.assertRaises(OpsError):
                self.config_plan({'url':'http://example.test',**change})
        self.assertEqual(self.api.writes,[])
    def test_valid_health_accepts_ipv4_ipv6_and_status_codes(self):
        for address in ('127.0.0.1','::1'):
            self.assertEqual(self.config_plan({'url':'https://example.test','resolve_ip':address,'timeout':60,'codes':[200,204,301]})['status'],'planned')
        self.assertEqual(self.api.writes,[])
    def test_apply_validates_legacy_plan_health_before_write(self):
        plan=self.config_plan({'url':'http://example.test'})
        saved=self.store.load(plan['plan_id']); saved['health']['timeout']=0
        from ops.operations import plan_hash
        saved['plan_digest']=plan_hash(saved); self.store.save(saved)
        with self.assertRaises(OpsError): self.ops.apply({'plan_id':plan['plan_id'],'plan_digest':saved['plan_digest']})
        self.assertEqual(self.api.writes,[])
    def test_probe_error_is_recorded_and_manual_undo_remains_available(self):
        for error in (OpsError('DEPENDENCY','curl unavailable'), OSError('probe unavailable')):
            with self.subTest(error=type(error).__name__):
                self.api.config=test_ops.OLD
                plan=self.config_plan({'url':'http://example.test'})
                before=len(self.api.writes)
                with patch('ops.operations.probe_http',side_effect=error):
                    result=self.ops.apply(plan)
                self.assertEqual(result['status'],'failed')
                saved=self.store.load(plan['plan_id'])
                self.assertEqual(saved['status'],'failed')
                self.assertEqual(len(self.api.writes),before+1) # No automatic undo on probe failure.
                if isinstance(error,OpsError): self.assertIn('DEPENDENCY',json.dumps(saved['result']))
                with patch('ops.operations.probe_http',return_value={'healthy':True}):
                    self.assertEqual(self.ops.rollback(plan)['status'],'rolled_back')
    def test_probe_error_during_undo_remains_rollback_uncertain(self):
        plan=self.config_plan({'url':'http://example.test'})
        with patch('ops.operations.probe_http',return_value={'healthy':True}): self.ops.apply(plan)
        with patch('ops.operations.probe_http',side_effect=OpsError('DEPENDENCY','curl unavailable')):
            result=self.ops.rollback(plan)
        self.assertEqual(result['status'],'rollback_uncertain')
        saved=self.store.load(plan['plan_id'])
        self.assertEqual(saved['status'],'rollback_uncertain')
        self.assertIn('DEPENDENCY',json.dumps(saved['result']))
        self.assertEqual(len(self.api.writes),2)

class StateDirectoryTests(Assertions):
    def test_public_existing_directory_and_contents_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'shared'; directory.mkdir(mode=0o755)
            file=directory/'keep.txt'; file.write_text('untouched'); file.chmod(0o644)
            with self.assertRaises(OpsError): secure_dir(directory)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o755)
            self.assertEqual(file.read_text(),'untouched')
            self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o644)
    def test_new_private_directory_and_existing_private_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private'
            self.assertEqual(secure_dir(path),path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700)
            with patch.object(Path,'chmod',side_effect=AssertionError('existing directory must not be chmodded')):
                self.assertEqual(secure_dir(path),path)
    def test_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'private'; target.mkdir(mode=0o700)
            alias=Path(tmp)/'alias'; alias.symlink_to(target)
            with self.assertRaises(OpsError): secure_dir(alias)
            self.assertTrue(alias.is_symlink()); self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o700)

class ProxySwitchTests(Assertions):
    def test_switch_proxy_family_removes_stale_upper_and_lower_variables(self):
        import yaml
        keys=('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')
        for proxy,expected in [('socks5h://proxy:7890',{'ALL_PROXY','all_proxy'}),('http://proxy:7890',{'HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'})]:
            with self.subTest(proxy=proxy):
                cfg=load_yaml(GOOD); cfg['services']['web']['environment']={**{k:'http://stale:8080' for k in keys},'KEEP':'original'}
                result=container_proxy({'content':yaml.safe_dump(cfg),'service':'web','proxy':proxy})
                env=load_yaml(result['content'])['services']['web']['environment']
                self.assertEqual(set(env).intersection(keys),expected)
                for key in expected: self.assertEqual(env[key],proxy)
                self.assertEqual(env['KEEP'],'original')
