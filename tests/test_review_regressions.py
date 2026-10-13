"""Regression scenarios from PR review; local mocks only, never live infrastructure."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
from test_ops import Assertions, GOOD, OLD, NEW, ROOT
import test_ops
import test_contracts
from ops.common import OpsError, redact
from ops.validators import lint_compose, lint_nginx, load_yaml

class ReviewValidationTests(Assertions):
    def test_sensitive_mount_ancestors(self):
        for source in ('/opt', '/opt/1panel', '/var', '/var/run'):
            with self.subTest(source=source):
                cfg=load_yaml(GOOD); cfg['services']['web']['volumes']=[source+':/data:ro']
                with self.assertRaises(OpsError): lint_compose(cfg,'demo', ['/'])
    def test_project_data_mount_remains_available(self):
        cfg=load_yaml(GOOD); cfg['services']['web']['volumes']=['/srv/demo/data:/data']
        lint_compose(cfg,'demo',['/srv/demo/data'])
    def test_mutable_images_require_explicit_choice(self):
        for image in ('nginx', 'nginx:latest', 'nginx:1.27.1'):
            with self.subTest(image=image):
                cfg=load_yaml(GOOD); cfg['services']['web']['image']=image
                with self.assertRaises(OpsError): lint_compose(cfg,'demo')
                self.assertTrue(lint_compose(cfg,'demo',allow_mutable_images=True)['warnings'])
    def test_digest_and_local_image_id_are_allowed(self):
        for image in ('nginx@sha256:'+'b'*64, 'sha256:'+'c'*64):
            cfg=load_yaml(GOOD); cfg['services']['web']['image']=image
            lint_compose(cfg,'demo')
    def test_external_network_requires_exact_opt_in(self):
        cfg=load_yaml(GOOD); cfg['services']['web']['networks']=['shared']
        cfg['networks']={'shared':{'external':True,'name':'1panel-network'}}
        with self.assertRaises(OpsError): lint_compose(cfg,'demo')
        with self.assertRaises(OpsError): lint_compose(cfg,'demo',allowed_networks=['other'])
        lint_compose(cfg,'demo',allowed_networks=['1panel-network'])
    def test_custom_network_name_without_external_rejected(self):
        cfg=load_yaml(GOOD); cfg['networks']={'default':{'name':'1panel-network'}}
        with self.assertRaises(OpsError): lint_compose(cfg,'demo',allowed_networks=['1panel-network'])
    def test_project_scoped_network_allowed(self):
        cfg=load_yaml(GOOD); cfg['services']['web']['networks']=['internal']; cfg['networks']={'internal':{}}
        lint_compose(cfg,'demo')
    def test_sensitive_nginx_directives_cannot_move_between_servers(self):
        for directive,a,b in [('root','/srv/a','/srv/b'),('ssl_certificate','/cert/a.pem','/cert/b.pem'),
                              ('ssl_certificate_key','/cert/a.key','/cert/b.key')]:
            before=f'server {{ listen 80; server_name a.test; {directive} {a}; }} server {{ listen 81; server_name b.test; {directive} {b}; }}'
            after=before.replace(a,'PLACEHOLDER').replace(b,a).replace('PLACEHOLDER',b)
            with self.subTest(directive=directive), self.assertRaises(OpsError): lint_nginx(after,before)
    def test_sensitive_nginx_directive_cannot_move_locations(self):
        before='server { listen 80; location /a { root /srv/a; } location /b { root /srv/b; } }'
        after=before.replace('/srv/a','PLACEHOLDER').replace('/srv/b','/srv/a').replace('PLACEHOLDER','/srv/b')
        with self.assertRaises(OpsError): lint_nginx(after,before)
    def test_multiline_and_spaced_credentials_are_not_partially_printed(self):
        for value in ('password: "first secret second secret"', 'password: |\n  first secret\n  second secret',
                      'PASSWORD=first secret second secret', 'authorization: Bearer first secret second secret'):
            result=json.dumps(redact({'message':value}))
            with self.subTest(value=value):
                self.assertNotIn('first secret',result); self.assertNotIn('second secret',result)
    def test_safe_status_string_survives_redaction(self):
        self.assertEqual(redact('Operation verified successfully'),'Operation verified successfully')

class ReviewWorkflowTests(Assertions):
    setUp = test_contracts.ContractTests.setUp
    def compose_fixture(self):
        self.api.compose={'name':'demo','configFile':'/tmp/demo.yml','content':GOOD,'env':'SETTING=old','createdBy':'1Panel',
                          'containers':[{'name':'demo-web-1','containerID':'c1'}],'containerCount':1,'runningCount':1}
    def test_service_addition_rejected_before_write(self):
        self.compose_fixture(); cfg=load_yaml(GOOD); cfg['services']['extra']={'image':'sha256:'+'d'*64}
        import yaml
        with patch('ops.operations.validate_compose',return_value={'ports':[],'warnings':[],'mounts':{}}), patch('ops.operations.occupied_ports',return_value=[]):
            self.denied('SERVICE_ADDITION',self.ops.plan,{'operation':'compose.update','target':{'name':'demo'},'change':{'content':yaml.safe_dump(cfg)}})
        self.assertEqual(self.api.writes,[])
    def test_cron_special_fields_never_silently_dropped(self):
        base={'id':4,'name':'job','type':'shell','spec':'0 * * * *','specCustom':True,'user':'root','script':'existing-command',
              'executor':'bash','scriptMode':'input','status':'Enable','alertCount':0,'groupID':0,'command':'','containerName':'',
              'retainCopies':7,'retryTimes':0,'timeout':120,'ignoreErr':False,'scriptID':0,'args':'','secret':''}
        for extra in ({'scriptMode':'library','scriptID':42},{'args':'--important'}, {'secret':'do-not-lose'},
                      {'alertCount':1}, {'scriptID':12}):
            self.api.crons=[{**base,**extra}]
            with self.subTest(extra=extra), self.assertRaises(OpsError):
                self.ops.plan({'operation':'cron.schedule','target':{'id':4,'name':'job'},'change':{'spec':'0 */2 * * *'}})
        self.assertEqual(self.api.writes,[])

class ReviewReleaseTests(Assertions):
    artifact = test_ops.HelperTests.artifact
    def test_existing_release_modified_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            req=self.artifact(tmp,{'index.html':'trusted'}); first=__import__('ops.helpers',fromlist=['static_release']).static_release({**req,'apply':True})
            root=Path(req['root']); (root/first['current']/'index.html').write_text('modified')
            with self.assertRaises(OpsError): __import__('ops.helpers',fromlist=['static_release']).static_release({**req,'apply':True})
    def test_existing_release_extra_file_is_rejected(self):
        from ops.helpers import static_release
        with tempfile.TemporaryDirectory() as tmp:
            req=self.artifact(tmp,{'index.html':'trusted'}); first=static_release({**req,'apply':True})
            (Path(req['root'])/first['current']/'unexpected.php').write_text('extra')
            with self.assertRaises(OpsError): static_release({**req,'apply':True})
    def test_existing_release_symlink_is_rejected(self):
        from ops.helpers import static_release
        with tempfile.TemporaryDirectory() as tmp:
            req=self.artifact(tmp,{'index.html':'trusted'}); first=static_release({**req,'apply':True})
            page=Path(req['root'])/first['current']/'index.html'; page.unlink(); page.symlink_to('/etc/hosts')
            with self.assertRaises(OpsError): static_release({**req,'apply':True})
    def test_existing_identical_release_can_be_reused(self):
        from ops.helpers import static_release
        with tempfile.TemporaryDirectory() as tmp:
            req=self.artifact(tmp,{'index.html':'trusted'}); first=static_release({**req,'apply':True})
            self.assertEqual(static_release({**req,'apply':True})['current'],first['current'])

class ReviewProxyOutputTests(Assertions):
    def test_proxy_cli_saves_private_candidate_without_echoing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env={**os.environ,'ONEPANEL_OPS_STATE_DIR':tmp}
            request={'skill_action':'proxy.compose','content':GOOD+'    environment:\n      PASSWORD: \"first spaced secret\"\n','service':'web','proxy':'http://proxy:7890'}
            result=subprocess.run([sys.executable,str(ROOT/'run.py')],input=json.dumps(request),capture_output=True,text=True,env=env)
            data=json.loads(result.stdout); self.assertEqual(result.returncode,0,result.stdout)
            self.assertIn('candidate_path',data); self.assertNotIn('content_preview',data); self.assertNotIn('content',data)
            self.assertNotIn('spaced',result.stdout); self.assertNotIn('HTTP_PROXY',result.stdout)
            self.assertTrue(Path(data['candidate_path']).is_file())

class ReviewRecoveryTests(Assertions):
    setUp = test_contracts.ContractTests.setUp
    compose_fixture = ReviewWorkflowTests.compose_fixture
    def pending_plan(self):
        self.compose_fixture()
        with patch('ops.operations.validate_compose',return_value={'ports':[],'warnings':[],'mounts':{}}), patch('ops.operations.occupied_ports',return_value=[]):
            plan=self.ops.plan({'operation':'compose.update','target':{'name':'demo'},'change':{'content':GOOD.replace('a'*64,'b'*64),'env':'SETTING=new'}})
        saved=self.store.load(plan['plan_id']); saved.update(status='uncertain',submitted_at=1000,task_id=saved['payload']['taskID']); self.store.save(saved)
        return plan
    def test_reconcile_requires_confirmation_and_two_stable_observations(self):
        plan=self.pending_plan(); req={**plan,'acknowledge_no_inflight':True}
        with patch.object(self.api,'query',wraps=self.api.query) as query, patch('ops.operations.time.time',return_value=1400):
            query.side_effect=lambda m,a,v=None: 0 if a=='countExecutingTasks' else test_contracts.StatefulPanel.query(self.api,m,a,v)
            self.denied('NEEDS_CONFIRMATION',self.ops.reconcile,plan)
            self.assertEqual(self.ops.reconcile(req)['status'],'uncertain')
            self.denied('PENDING_OPERATION',self.ops.reconcile,req)
            with patch('ops.operations.time.time',return_value=1460):
                self.assertEqual(self.ops.reconcile(req)['status'],'reconciled_unchanged')
        self.assertEqual(self.ops.verify(plan)['status'],'reconciled_unchanged')
        self.denied('ALREADY_SUBMITTED',self.ops.apply,plan)
        self.assertEqual(self.api.writes,[])
    def test_reconcile_refuses_young_running_known_or_changed_tasks(self):
        plan=self.pending_plan(); req={**plan,'acknowledge_no_inflight':True}
        with patch('ops.operations.time.time',return_value=1100):
            self.denied('PENDING_OPERATION',self.ops.reconcile,req)
        with patch('ops.operations.time.time',return_value=1400), patch.object(self.api,'query',side_effect=lambda m,a,v=None: 1 if a=='countExecutingTasks' else test_contracts.StatefulPanel.query(self.api,m,a,v)):
            self.denied('PENDING_OPERATION',self.ops.reconcile,req)
        self.api.task_id=self.store.load(plan['plan_id'])['task_id']; self.api.task_status='Waiting'
        with patch('ops.operations.time.time',return_value=1400):
            self.denied('PENDING_OPERATION',self.ops.reconcile,req)
        self.api.task_id=None; self.api.compose['env']='EXTERNAL=change'
        with patch('ops.operations.time.time',return_value=1400), patch.object(self.api,'query',side_effect=lambda m,a,v=None: 0 if a=='countExecutingTasks' else test_contracts.StatefulPanel.query(self.api,m,a,v)):
            self.denied('TARGET_CHANGED',self.ops.reconcile,req)
        self.assertEqual(self.api.writes,[])
    def test_partial_failure_can_restore_old_content_and_new_env(self):
        plan=self.pending_plan(); saved=self.store.load(plan['plan_id']); saved['status']='failed'; self.store.save(saved)
        self.api.task_id=saved['task_id']; self.api.task_status='Failed'; self.api.compose['env']='SETTING=new'
        original=self.api.call
        def completed_restore(method,path,body=None):
            result=original(method,path,body)
            if path=='/api/v2/containers/compose/update': self.api.task_status='Success'
            return result
        with patch('ops.operations.validate_compose',return_value={'ports':[],'warnings':[],'mounts':{}}), patch('ops.operations.occupied_ports',return_value=[]), patch.object(self.api,'call',side_effect=completed_restore):
            self.assertEqual(self.ops.rollback(plan)['status'],'rolled_back')
        self.assertEqual(self.api.compose['env'],'SETTING=old'); self.assertEqual(len(self.api.writes),1)
    def test_partial_failure_does_not_overwrite_external_drift(self):
        plan=self.pending_plan(); saved=self.store.load(plan['plan_id']); saved['status']='failed'; self.store.save(saved)
        self.api.task_id=saved['task_id']; self.api.task_status='Failed'; self.api.compose['env']='SETTING=external'
        self.denied('TARGET_CHANGED',self.ops.rollback,plan); self.assertEqual(self.api.writes,[])
    def test_partial_failure_requires_terminal_failure_and_exact_identity(self):
        plan=self.pending_plan(); saved=self.store.load(plan['plan_id']); actual=copy.deepcopy(saved['before']); actual['env']='SETTING=new'
        self.assertTrue(self.ops.partial_compose_failure(saved,actual,'Failed'))
        for state in ('Waiting','Success',None): self.assertFalse(self.ops.partial_compose_failure(saved,actual,state))
        for field in ('name','path','createdBy'):
            self.assertFalse(self.ops.partial_compose_failure(saved,{**actual,field:'external'},'Failed'))
    def test_allowed_network_must_exist(self):
        def network_call(items):
            return lambda method,path,body=None: {'items':items,'total':len(items)} if path=='/api/v2/containers/network/search' else test_contracts.StatefulPanel.call(self.api,method,path,body)
        with patch('ops.operations.validate_compose',return_value={'ports':[],'warnings':[],'mounts':{}}), patch('ops.operations.occupied_ports',return_value=[]), patch.object(self.api,'call',side_effect=network_call([])):
            self.denied('TARGET_MISSING',self.ops._compose_checks,GOOD,'demo','',{'allow_networks':['1panel-network']})
        with patch('ops.operations.validate_compose',return_value={'ports':[],'warnings':[],'mounts':{}}), patch('ops.operations.occupied_ports',return_value=[]), patch.object(self.api,'call',side_effect=network_call([{'name':'1panel-network'}])):
            self.ops._compose_checks(GOOD,'demo','',{'allow_networks':['1panel-network']})
    def test_digest_image_runtime_mismatch_is_not_verified(self):
        plan=self.pending_plan(); saved=self.store.load(plan['plan_id']); saved['options']['expected_images']={'web':'nginx@sha256:'+'b'*64}
        # options form part of the plan digest; save a consistent fixture.
        from ops.operations import plan_hash
        saved['plan_digest']=plan_hash(saved); self.store.save(saved)
        self.api.task_id=saved['task_id']; self.api.task_status='Success'
        self.api.compose.update(content=saved['payload']['content'],env=saved['payload']['env'])
        self.assertEqual(self.ops.verify({'plan_id':plan['plan_id']})['status'],'failed')

    def test_digest_reference_verifies_even_when_image_id_differs(self):
        plan=self.pending_plan(); saved=self.store.load(plan['plan_id']); saved['options']['expected_images']={'web':'nginx@sha256:'+'b'*64}
        from ops.operations import plan_hash
        saved['plan_digest']=plan_hash(saved); self.store.save(saved)
        self.api.task_id=saved['task_id']; self.api.task_status='Success'
        self.api.compose.update(content=saved['payload']['content'],env=saved['payload']['env'])
        self.api.image_ref='nginx@sha256:'+'b'*64
        self.api.image='sha256:'+'c'*64
        self.assertEqual(self.ops.verify({'plan_id':plan['plan_id']})['status'],'verified')

    def test_reconcile_intervening_execution_resets_stability_window(self):
        plan=self.pending_plan(); req={**plan,'acknowledge_no_inflight':True}
        count=[0]
        def query(m,a,v=None):
            return count[0] if a=='countExecutingTasks' else test_contracts.StatefulPanel.query(self.api,m,a,v)
        with patch.object(self.api,'query',side_effect=query):
            with patch('ops.operations.time.time',return_value=1400): self.ops.reconcile(req)
            count[0]=1
            with patch('ops.operations.time.time',return_value=1430): self.denied('PENDING_OPERATION',self.ops.reconcile,req)
            self.assertNotIn('reconcile_observation',self.store.load(plan['plan_id']))
            count[0]=0
            with patch('ops.operations.time.time',return_value=1460): self.assertEqual(self.ops.reconcile(req)['status'],'uncertain')
            with patch('ops.operations.time.time',return_value=1520): self.assertEqual(self.ops.reconcile(req)['status'],'reconciled_unchanged')
    def test_reconcile_receipt_reappearance_clears_observation(self):
        plan=self.pending_plan(); req={**plan,'acknowledge_no_inflight':True}
        with patch.object(self.api,'query',side_effect=lambda m,a,v=None: 0 if a=='countExecutingTasks' else test_contracts.StatefulPanel.query(self.api,m,a,v)), patch('ops.operations.time.time',return_value=1400):
            self.ops.reconcile(req)
            self.api.task_id=self.store.load(plan['plan_id'])['task_id']; self.api.task_status='Waiting'
            self.denied('PENDING_OPERATION',self.ops.reconcile,req)
        self.assertNotIn('reconcile_observation',self.store.load(plan['plan_id']))
