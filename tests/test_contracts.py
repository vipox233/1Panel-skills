"""Contract fixtures for update/undo and cron/HTTPS; not production API tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_ops import FakePanel, GOOD, OLD, NEW, Assertions
from ops.common import Store
from ops.operations import Operations
from ops.validators import load_yaml

class StatefulPanel(FakePanel):
    def __init__(self):
        super().__init__()
        self.image = 'sha256:' + 'a'*64
        self.image_ref = 'nginx@sha256:' + 'a'*64
        self.https = {'enable':True,'SSL':{'id':2,'privateKey':'not-for-output'}, 'httpConfig':'HTTPAlso','SSLProtocol':['TLSv1.2'],'httpsPorts':[443]}
        self.crons = []
    def query(self, module, action, values=None):
        if action == 'inspectContainer':
            return json.dumps({'Image':self.image,'Config':{'Image':self.image_ref,'Labels':{'com.docker.compose.project':'demo','com.docker.compose.service':'web'}}})
        if action == 'loadCronjobInfo': return copy.deepcopy(next(c for c in self.crons if c['id']==values['id']))
        if action == 'getCertificate': return {'id':values['id'],'privateKey':'not-for-output'}
        return super().query(module,action,values)
    def call(self, method, path, body=None):
        if path == '/api/v2/cronjobs/search': return {'items':copy.deepcopy(self.crons),'total':len(self.crons)}
        if path == '/api/v2/cronjobs/next': return ['sample-next-run']
        if path == '/api/v2/websites/7/https' and method == 'GET': return copy.deepcopy(self.https)
        if path == '/api/v2/websites/7/https':
            self.writes.append((method,path,copy.deepcopy(body)))
            self.https = {**body,'SSL':{'id':body['websiteSSLId']}}
            return None
        if path == '/api/v2/containers/compose/update':
            self.writes.append((method,path,copy.deepcopy(body)))
            self.task_id = body['taskID']
            self.compose.update(content=body['content'], env=body['env'])
            image = load_yaml(body['content'])['services']['web']['image']
            self.image_ref = image
            self.image = image if image.startswith('sha256:') else 'sha256:'+'b'*64
            return None
        if path == '/api/v2/cronjobs/update':
            self.writes.append((method,path,copy.deepcopy(body)))
            self.crons[0].update(body); return None
        return super().call(method,path,body)

class ContractTests(Assertions):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.api=StatefulPanel(); self.store=Store(self.tmp.name+'/state'); self.ops=Operations(self.api,self.store)
    def test_update_undo_preserves_actual_image_and_env(self):
        self.api.compose={'name':'demo','configFile':'/tmp/demo.yml','content':GOOD,'env':'SETTING=old','createdBy':'1Panel',
                          'containers':[{'name':'demo-web-1','containerID':'c1'}],'containerCount':1,'runningCount':1}
        with patch('ops.operations.validate_compose',return_value={'ports':[],'warnings':[]}), patch('ops.operations.occupied_ports',return_value=[]):
            plan=self.ops.plan({'operation':'compose.update','target':{'name':'demo'},'change':{'content':GOOD.replace('a'*64,'b'*64),'env':'SETTING=new'}})
            receipt=self.store.load(plan['plan_id']); undo=receipt['rollback_payload']
            self.assertEqual(load_yaml(undo['content'])['services']['web']['image'], 'sha256:'+'a'*64)
            self.assertEqual(load_yaml(undo['content'])['services']['web']['pull_policy'],'never')
            self.assertEqual(undo['env'],'SETTING=old')
            self.assertEqual(self.ops.apply(plan)['status'],'verified')
            self.assertEqual(self.ops.rollback(plan)['status'],'rolled_back')
        self.assertEqual(self.api.image,'sha256:'+'a'*64)
        self.assertEqual(self.api.compose['env'],'SETTING=old')
    def test_https_uses_nested_certificate_and_undo(self):
        plan=self.ops.plan({'operation':'website.https','target':{'id':7,'domain':'example.test'},'change':{'certificate_id':3}})
        saved=self.store.load(plan['plan_id'])
        self.assertNotIn('privateKey', json.dumps(saved))
        self.assertEqual(saved['before']['websiteSSLId'],2)
        self.assertEqual(self.ops.apply(plan)['status'],'verified')
        self.assertEqual(self.ops.rollback(plan)['status'],'rolled_back')
        self.assertEqual(self.api.https['SSL']['id'],2)
    def test_existing_unicode_cron_preserves_user_and_script(self):
        self.api.crons=[{'id':4,'name':'用量统计','type':'shell','spec':'0 */1 * * *','user':'root','script':'existing-command','executor':'bash','scriptMode':'input','status':'Enable','alertCount':0,'groupID':0,'specCustom':True,'command':'','containerName':'','retainCopies':7,'retryTimes':0,'timeout':120,'ignoreErr':False,'scriptID':0,'args':'','secret':'','dbType':'mysql','isDir':True}]
        plan=self.ops.plan({'operation':'cron.schedule','target':{'name':self.api.crons[0]['name'],'id':4},'change':{'spec':'0 */2 * * *'}})
        body=self.store.load(plan['plan_id'])['payload']
        self.assertEqual(body['user'],'root'); self.assertEqual(body['script'],'existing-command')
        self.assertEqual(body['dbType'],'mysql'); self.assertIs(body['isDir'],True)
        self.assertEqual(self.ops.apply(plan)['status'],'verified')
        self.assertEqual(self.api.crons[0]['dbType'],'mysql'); self.assertIs(self.api.crons[0]['isDir'],True)
        self.assertEqual(self.ops.rollback(plan)['status'],'rolled_back')
        self.assertEqual(self.api.crons[0]['spec'],'0 */1 * * *')
        self.assertEqual(self.api.crons[0]['dbType'],'mysql'); self.assertIs(self.api.crons[0]['isDir'],True)
    def test_new_cron_uses_valid_executor_and_blocks_changed_script(self):
        script=Path(self.tmp.name)/'sample.py'; script.write_text('print(1)')
        plan=self.ops.plan({'operation':'cron.create','target':{'name':'sample'},'change':{'spec':'0 * * * *','script':str(script),'user':'agentdock'}})
        body=self.store.load(plan['plan_id'])['payload']
        self.assertEqual(body['executor'],'bash'); self.assertTrue(body['script'].startswith('/usr/bin/python3 '))
        script.write_text('print(2)'); self.denied('TARGET_CHANGED',self.ops.apply,plan)
        self.assertEqual(self.api.writes,[])
    def test_root_cron_creation_not_a_generic_root_shell(self):
        self.denied('DENIED',self.ops.plan,{'operation':'cron.create','target':{'name':'sample'},'change':{'spec':'0 * * * *','script':'/tmp/sample.py','user':'root'}})
    def test_failed_undo_remains_undo_when_verified_again(self):
        plan=self.ops.plan({'operation':'website.config','target':{'id':7,'domain':'example.test'},'change':{'content':NEW},'health':{'url':'http://example.test'}})
        with patch('ops.operations.probe_http',side_effect=[{'healthy':False},{'healthy':False},{'healthy':True}]):
            result=self.ops.apply(plan); self.assertEqual(result['status'],'rollback_uncertain')
            result=self.ops.verify(plan); self.assertEqual(result['status'],'rolled_back')
        self.assertEqual(len(self.api.writes),2)

if __name__=='__main__': unittest.main()
