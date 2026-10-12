"""Deterministic contract/regression tests. FakePanel is NOT a live 1Panel test."""
import copy
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from ops.common import OpsError, Store, redact
from ops.validators import load_yaml, lint_compose, validate_compose, lint_nginx, port_bindings, check_ports
from ops.operations import Operations
from ops.helpers import container_proxy, proxy_chain, static_release

GOOD = 'services:\n  web:\n    image: nginx:stable-alpine\n    ports: ["127.0.0.1:18081:80"]\n'
OLD = 'server { listen 80; server_name example.test; location / { proxy_pass http://127.0.0.1:8000; proxy_read_timeout 30s; } }'
NEW = OLD.replace('30s', '60s')

class Assertions(unittest.TestCase):
    def denied(self, code, call, *args, **kwargs):
        with self.assertRaises(OpsError) as caught: call(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

class ValidationTests(Assertions):
    def test_yaml_duplicate_and_unsafe_tag(self):
        for value in ['a: 1\na: 2', 'a: !!python/object/apply:os.system [echo x]', 'a: &a [*a]']:
            self.denied('BAD_YAML', load_yaml, value)
    def test_yaml_merge_normal(self):
        self.assertEqual(load_yaml('base: &b {image: nginx}\nservices: {web: {<<: *b}}')['services']['web']['image'], 'nginx')
    def test_compose_good(self):
        report = lint_compose(load_yaml(GOOD), 'demo')
        self.assertEqual(report['ports'][0]['port'], 18081)
    def test_compose_host_capability_and_hooks(self):
        fields = {'privileged': True, 'cap_add': ['SYS_ADMIN'], 'pid': 'host', 'ipc': 'host',
                  'security_opt': ['seccomp:unconfined'], 'devices': ['/dev/sda'],
                  'use_api_socket': True, 'post_start': [{'command': 'id'}], 'env_file': '/root/.env',
                  'build': '.', 'extends': {'file': '/tmp/other'}}
        for key, value in fields.items():
            with self.subTest(key=key):
                config = load_yaml(GOOD); config['services']['web'][key] = value
                with self.assertRaises(OpsError): lint_compose(config, 'demo')
    def test_mount_protection(self):
        for source in ('/', '/etc', '/var/run/docker.sock', '/proc', '../../etc', '/opt/1panel/db/core.db'):
            with self.subTest(source=source):
                config = load_yaml(GOOD); config['services']['web']['volumes'] = [source+':/data']
                with self.assertRaises(OpsError): lint_compose(config, 'demo', ['/'])
    def test_explicit_data_mount(self):
        config = load_yaml(GOOD); config['services']['web']['volumes'] = ['/srv/test-data:/data']
        self.denied('UNSAFE_MOUNT', lint_compose, config, 'demo')
        lint_compose(config, 'demo', ['/srv/test-data'])
    def test_external_volume_driver(self):
        for options in ({'external': True}, {'driver_opts': {'device': '/'}}, {'driver': 'nfs'}):
            config = load_yaml(GOOD); config['volumes'] = {'data': options}
            self.denied('UNSAFE_MOUNT', lint_compose, config, 'demo')
    def test_reserved_env_and_volume_identity(self):
        self.denied('RESERVED_ENV', validate_compose, GOOD, 'demo', 'COMPOSE_PROJECT_NAME=other')
        config = load_yaml(GOOD); config['volumes'] = {'data': {'name':'another-project-database'}}
        self.denied('UNSAFE_MOUNT', lint_compose, config, 'demo')
    def test_name_mismatch(self):
        config = load_yaml(GOOD); config['name'] = 'production'
        self.denied('TARGET_MISMATCH', lint_compose, config, 'demo')
    def test_port_conflict(self):
        ports = port_bindings(load_yaml(GOOD))
        self.denied('PORT_CONFLICT', check_ports, ports, [('tcp', '0.0.0.0', 18081)])
        check_ports(ports, [('tcp', '0.0.0.0', 18081)], ports)
    def test_compose_no_fake_validation(self):
        with patch('ops.validators.shutil.which', return_value=None):
            self.denied('DEPENDENCY', validate_compose, GOOD, 'demo')
    def test_nginx_correct_preview_not_real_nginx_test(self):
        result = lint_nginx(NEW, OLD)
        self.assertTrue(result['structural_check']); self.assertFalse(result['nginx_syntax_check'])
    def test_nginx_identity_and_code(self):
        self.denied('NEEDS_CONFIRMATION', lint_nginx, NEW.replace('listen 80', 'listen 81'), OLD)
        self.denied('UNSAFE_NGINX', lint_nginx, NEW.replace('location / {', 'location / { content_by_lua_block { return 1; }'), OLD)
        self.denied('NGINX_INVALID', lint_nginx, 'server { listen 80;')
    def test_redaction(self):
        value = redact({'env': 'PASSWORD=abc', 'token': 'abc', 'url': 'http://user:password@example.test'})
        self.assertNotIn('abc', json.dumps(value)); self.assertNotIn('password@', value['url'])

class FakePanel:
    base_url = 'http://127.0.0.1:8080'
    def __init__(self):
        self.config = OLD; self.writes = []; self.fail = False
        self.compose = None; self.task_status = 'Success'; self.task_id = None
    def query(self, module, action, values=None):
        mapping = {('containers','searchContainers'):'/containers/search',
                   ('websites','searchWebsites'):'/websites/search',
                   ('cronjobs','searchCronjobs'):'/cronjobs/search',
                   ('task-center','searchTasks'):'/logs/tasks/search'}
        return self.call('POST', '/api/v2'+mapping[(module,action)], values)
    def call(self, method, path, body=None):
        if path == '/api/v2/websites/7': return {'id': 7, 'primaryDomain': 'example.test'}
        if path == '/api/v2/websites/7/config/nginx': return {'content': self.config}
        if path == '/api/v2/openresty': return {'content': self.config}
        if path == '/api/v2/containers/search': return {'total': 0, 'items': []}
        if path == '/api/v2/containers/compose/search':
            rows = [] if self.compose is None else [dict(self.compose)]
            return {'total': len(rows), 'items': rows}
        if path == '/api/v2/files/content': return {'content': self.compose['content']}
        if path == '/api/v2/logs/tasks/search':
            rows = [{'id': self.task_id, 'status': self.task_status}] if self.task_id else []
            return {'total': len(rows), 'items': rows}
        self.writes.append((method, path, copy.deepcopy(body)))
        if self.fail: raise OpsError('API_UNCERTAIN', 'simulated interrupted response')
        if path == '/api/v2/websites/nginx/update': self.config = body['content']; return None
        if path == '/api/v2/openresty/file': self.config = body['content']; return None
        if path == '/api/v2/containers/compose':
            self.task_id = body['taskID']
            self.compose = {'name': body['name'], 'configFile': '/tmp/demo/docker-compose.yml', 'env': body['env'],
                            'createdBy': '1Panel', 'content': body['file'], 'containerCount': 1, 'runningCount': 1}
            return None
        raise AssertionError((method, path, body))

class WorkflowTests(Assertions):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.api = FakePanel(); self.store = Store(self.tmp.name+'/state'); self.ops = Operations(self.api, self.store)
    def config_plan(self, health=None):
        return self.ops.plan({'operation': 'website.config', 'target': {'id': 7, 'domain': 'example.test'},
                              'change': {'content': NEW}, **({'health': health} if health else {})})
    def test_preview_never_writes(self):
        plan = self.config_plan(); self.assertEqual(plan['status'], 'planned'); self.assertEqual(self.api.writes, [])
    def test_apply_readback_and_replay(self):
        plan = self.config_plan(); result = self.ops.apply(plan)
        self.assertEqual(result['status'], 'verified'); self.assertEqual(self.api.config, NEW)
        self.denied('ALREADY_SUBMITTED', self.ops.apply, plan)
        self.assertEqual(len(self.api.writes), 1)
    def test_exact_website_identity(self):
        self.denied('TARGET_MISMATCH', self.ops.plan, {'operation':'website.config', 'target':{'id':7,'domain':'other.test'}, 'change':{'content':NEW}})
        self.assertEqual(self.api.writes, [])
    def test_drift_fails_without_write(self):
        plan = self.config_plan(); self.api.config = OLD.replace('30s', '45s')
        self.denied('TARGET_CHANGED', self.ops.apply, plan); self.assertEqual(self.api.writes, [])
    def test_digest_tamper(self):
        plan = self.config_plan(); saved = self.store.load(plan['plan_id']); saved['payload']['content'] = OLD
        self.store.save(saved); self.denied('PLAN_CHANGED', self.ops.apply, plan)
    def test_cross_instance_and_bad_digest(self):
        plan = self.config_plan(); self.denied('PLAN_CHANGED', self.ops.apply, {**plan,'plan_digest':'bad'})
        self.ops.instance = 'other'; self.denied('TARGET_MISMATCH', self.ops.apply, plan)
    def test_expiry(self):
        plan = self.config_plan()
        with patch('ops.operations.time.time', return_value=9999999999): self.denied('PLAN_EXPIRED', self.ops.apply, plan)
    def test_uncertain_write_is_not_retried(self):
        plan = self.config_plan(); self.api.fail = True
        result = self.ops.apply(plan); self.assertEqual(result['status'], 'uncertain')
        self.denied('ALREADY_SUBMITTED', self.ops.apply, plan); self.assertEqual(len(self.api.writes), 1)
    def test_rollback_actual_saved_content(self):
        plan = self.config_plan(); self.ops.apply(plan)
        result = self.ops.rollback(plan)
        self.assertEqual(result['status'], 'rolled_back'); self.assertEqual(self.api.config, OLD)
        self.assertEqual(self.api.writes[-1][2]['content'], OLD)
    def test_rollback_refuses_other_edit(self):
        plan = self.config_plan(); self.ops.apply(plan); self.api.config = OLD.replace('30s', '90s')
        self.denied('TARGET_CHANGED', self.ops.rollback, plan)
    def test_failed_health_auto_restores_config(self):
        plan = self.config_plan({'url':'http://example.test'})
        with patch('ops.operations.probe_http', side_effect=[{'healthy':False}, {'healthy':True}]):
            result = self.ops.apply(plan)
        self.assertEqual(result['status'], 'rolled_back'); self.assertEqual(self.api.config, OLD)
        self.assertEqual(len(self.api.writes), 2)
    def test_new_resources_have_no_delete_undo(self):
        with patch('ops.operations.validate_compose', return_value={'ports':[], 'warnings':[]}), patch('ops.operations.occupied_ports', return_value=[]):
            plan = self.ops.plan({'operation':'compose.create','target':{'name':'demo'},'change':{'content':GOOD}})
            self.ops.apply(plan)
        self.denied('NOT_ROLLBACKABLE', self.ops.rollback, plan)
    def test_async_creation_stays_pending(self):
        self.api.task_status = 'Waiting'
        with patch('ops.operations.validate_compose', return_value={'ports':[], 'warnings':[]}), patch('ops.operations.occupied_ports', return_value=[]):
            plan = self.ops.plan({'operation':'compose.create','target':{'name':'demo'},'change':{'content':GOOD}})
            result = self.ops.apply(plan)
        self.assertEqual(result['status'], 'submitted')
        self.api.task_status = 'Success'; self.assertEqual(self.ops.verify(plan)['status'], 'verified')
    def test_missing_task_not_success(self):
        with patch('ops.operations.validate_compose', return_value={'ports':[], 'warnings':[]}), patch('ops.operations.occupied_ports', return_value=[]):
            plan = self.ops.plan({'operation':'compose.create','target':{'name':'demo'},'change':{'content':GOOD}})
            self.ops.apply(plan)
        saved = self.store.load(plan['plan_id']); saved['status'] = 'submitted'; self.store.save(saved)
        self.api.task_id = None; self.assertEqual(self.ops.verify(plan)['status'], 'submitted')
    def test_duplicate_project_not_overwritten(self):
        self.api.compose = {'name':'demo','content':GOOD,'configFile':'/tmp/demo.yml','env':'','createdBy':'1Panel'}
        self.denied('ALREADY_EXISTS', self.ops.plan, {'operation':'compose.create','target':{'name':'demo'},'change':{'content':GOOD}})
    def test_unknown_action(self):
        self.denied('UNKNOWN_ACTION', self.ops.plan, {'operation':'database.delete','target':{}})
    def test_state_permissions(self):
        plan = self.config_plan()
        self.assertEqual(stat.S_IMODE(self.store.path(plan['plan_id']).stat().st_mode), 0o600)

class HelperTests(Assertions):
    def test_container_proxy_does_not_add_host_network(self):
        self.denied('CONTAINER_LOOPBACK', container_proxy, {'content':GOOD,'service':'web','proxy':'http://127.0.0.1:7890'})
        result = container_proxy({'content':GOOD,'service':'web','proxy':'http://proxy:7890'})
        cfg = load_yaml(result['content']); self.assertNotIn('network_mode', cfg['services']['web'])
        self.assertEqual(cfg['services']['web']['environment']['HTTP_PROXY'], 'http://proxy:7890')
    def test_chain_and_cycle(self):
        source = 'proxies: [{name: exit, type: socks5}, {name: middle, type: socks5}]\nproxy-groups: []'
        result = proxy_chain({'content':source,'exit_proxy':'exit','via':'middle'})
        self.assertEqual(load_yaml(result['content'])['proxies'][0]['dialer-proxy'], 'middle')
        cycle = 'proxies: [{name: exit, type: socks5}]\nproxy-groups: [{name: middle, proxies: [exit]}]'
        self.denied('PROXY_CYCLE', proxy_chain, {'content':cycle,'exit_proxy':'exit','via':'middle'})
    def artifact(self, directory, entries):
        archive = Path(directory)/'build.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for n,v in entries.items(): z.writestr(n,v)
        return {'artifact':str(archive),'root':str(Path(directory)/'web'), 'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    def test_static_preview_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = self.artifact(tmp, {'index.html':'old'})
            static_release(request); self.assertFalse(Path(request['root']).exists())
    def test_static_two_releases_preserve_old_and_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = self.artifact(tmp, {'index.html':'old'}); first = static_release({**request,'apply':True})
            root = Path(request['root']); (root/'data').mkdir(); (root/'data/keep.json').write_text('safe')
            request = self.artifact(tmp, {'index.html':'new'}); second = static_release({**request,'apply':True})
            self.assertEqual((root/'current/index.html').read_text(), 'new')
            self.assertEqual((root/first['current']/'index.html').read_text(), 'old')
            self.assertEqual((root/'data/keep.json').read_text(), 'safe')
            self.assertEqual(second['previous'], first['current'])
            self.assertEqual((root/'current/data/keep.json').read_text(), 'safe')
    def test_static_rejects_traversal_and_protected_data(self):
        for bad in ('../escape','/tmp/escape','.env','data/tokscale.json','uploads/photo.jpg'):
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as tmp:
                request = self.artifact(tmp, {'index.html':'hello', bad:'bad'})
                with self.assertRaises(OpsError): static_release({**request,'apply':True})
                self.assertFalse(Path(request['root']).exists())
    def test_static_rollback(self):
        from ops.helpers import static_rollback
        with tempfile.TemporaryDirectory() as tmp:
            request = self.artifact(tmp, {'index.html':'old'}); first = static_release({**request,'apply':True})
            request = self.artifact(tmp, {'index.html':'new'}); static_release({**request,'apply':True})
            result = static_rollback({'root':request['root'],'apply':True})
            self.assertEqual(result['current'], first['current'])
            self.assertEqual((Path(request['root'])/'current/index.html').read_text(),'old')
    def test_static_bad_hash_and_real_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = self.artifact(tmp, {'index.html':'hello'})
            self.denied('CHECKSUM_MISMATCH', static_release, {**request,'sha256':'0'*64})
            (Path(request['root'])/'current').mkdir(parents=True)
            self.denied('ALREADY_EXISTS', static_release, {**request,'apply':True})
    def test_static_zip_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)/'bad.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('index.html','hello')
                entry = zipfile.ZipInfo('link'); entry.external_attr = (stat.S_IFLNK|0o777)<<16
                z.writestr(entry,'/etc/passwd')
            self.denied('UNSAFE_PATH', static_release, {'artifact':str(archive),'root':str(Path(tmp)/'web'),
                         'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'apply':True})

class CliTests(unittest.TestCase):
    def invoke(self, value):
        proc = subprocess.run([sys.executable,str(ROOT/'run.py')],input=json.dumps(value),capture_output=True,text=True)
        return proc.returncode, json.loads(proc.stdout)
    def test_status_offline(self):
        code, data = self.invoke({'skill_action':'status'})
        self.assertEqual(code,0); self.assertEqual(data['version'],'1.1.0'); self.assertFalse(data['live_checked'])
    def test_no_unknown_fallback(self):
        code,data = self.invoke({'skill_action':'shell','command':'echo should-not-run'})
        self.assertEqual(code,1); self.assertEqual(data['code'],'UNKNOWN_ACTION')
    def test_invalid_input_json_shape(self):
        code,data = self.invoke([]); self.assertEqual(code,1); self.assertEqual(data['code'],'BAD_INPUT')

if __name__ == '__main__': unittest.main()
