"""Retained real JS client against a mock HTTP server, NOT live 1Panel."""
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('ONEPANEL_NODE') or shutil.which('node')
READY = bool(NODE) and (ROOT/'dist/scripts/client.js').is_file()

class Handler(BaseHTTPRequestHandler):
    calls = []
    response = {'code':200,'data':{'sample':True}}
    def log_message(self, *args): pass
    def do_GET(self): self.handle_api()
    def do_POST(self): self.handle_api()
    def handle_api(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        type(self).calls.append({'path':self.path,'headers':dict(self.headers),'body':body.decode()})
        value = type(self).response if isinstance(type(self).response, bytes) else json.dumps(type(self).response).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(value))); self.end_headers(); self.wfile.write(value)

@unittest.skipUnless(READY, 'Complete upstream checkout and Node.js required')
class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1',0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def setUp(self): Handler.calls=[]; Handler.response={'code':200,'data':{'sample':True}}
    def invoke(self, request):
        env = {**os.environ,'ONEPANEL_BASE_URL':f'http://127.0.0.1:{self.server.server_port}',
               'ONEPANEL_API_KEY':'unit-test-not-a-real-key'}
        p = subprocess.run([NODE,str(ROOT/'scripts/ops-bridge.mjs')],input=json.dumps(request),capture_output=True,text=True,env=env,timeout=10)
        self.assertTrue(p.stdout, f"Bridge returned no JSON (exit {p.returncode}): {p.stderr[:2000]}")
        return p.returncode, json.loads(p.stdout)
    def test_retained_signing_and_unwrap(self):
        code,data = self.invoke({'mode':'request','method':'GET','path':'/api/v2/openresty'})
        self.assertEqual(code,0); self.assertEqual(data['data'],{'sample':True})
        headers={k.lower():v for k,v in Handler.calls[0]['headers'].items()}
        expected=hashlib.md5(('1panelunit-test-not-a-real-key'+headers['1panel-timestamp']).encode()).hexdigest()
        self.assertEqual(headers['1panel-token'],expected)
    def test_http_200_business_401_is_failure(self):
        Handler.response={'code':401,'message':'not logged in','data':None}
        code,data=self.invoke({'mode':'request','method':'GET','path':'/api/v2/openresty'})
        self.assertEqual(code,1); self.assertFalse(data['ok']); self.assertEqual(data['code'],'API_REJECTED')
    def test_upstream_query_reused_and_defaults_fixed(self):
        self.invoke({'mode':'query','module':'containers','action':'searchContainers','input':{}})
        body=json.loads(Handler.calls[0]['body']); self.assertEqual(body['state'],'all'); self.assertEqual(body['orderBy'],'createdAt')
    def test_destructive_request_never_sent(self):
        requests=[{'method':'POST','path':'/api/v2/containers/operate','body':{'names':['db'],'operation':'remove'}},
                  {'method':'POST','path':'/api/v2/containers/prune','body':{}},
                  {'method':'POST','path':'/api/v2/containers/compose/operate','body':{'name':'app','operation':'down'}},
                  {'method':'POST','path':'/api/v2/websites/proxies/update','body':{'operate':'delete'}},
                  {'method':'GET','path':'//evil.test/path'}]
        for request in requests:
            with self.subTest(request=request):
                code,data=self.invoke({'mode':'request',**request}); self.assertEqual(code,1); self.assertEqual(data['code'],'DENIED')
        self.assertEqual(Handler.calls,[])
    def test_log_tail_is_always_bounded(self):
        from urllib.parse import urlsplit, parse_qs
        for tail in (None, 'all', '-1', '50000', '5'):
            with self.subTest(tail=tail):
                values={'container':'demo','follow':True}
                if tail is not None: values['tail']=tail
                code,data=self.invoke({'mode':'query','module':'containers','action':'streamContainerLogs','input':values})
                if code:
                    self.assertFalse(data['ok'])
                    continue
                query=parse_qs(urlsplit(Handler.calls[-1]['path']).query)
                self.assertLessEqual(int(query['tail'][0]),1000)
                self.assertGreater(int(query['tail'][0]),0)
                self.assertEqual(query.get('follow'),['false'])
                if tail is None: self.assertEqual(query['tail'],['200'])
                if tail == '5': self.assertEqual(query['tail'],['5'])
    def test_oversized_response_fails_without_returning_body(self):
        Handler.response={'code':200,'data':'unique-private-marker'+'x'*(4*1024*1024)}
        code,data=self.invoke({'mode':'request','method':'GET','path':'/api/v2/openresty'})
        self.assertEqual(code,1); self.assertFalse(data['ok'])
        self.assertNotIn('unique-private-marker',json.dumps(data))
        self.assertLess(len(json.dumps(data)),4096)
    def test_malformed_json_never_prints_response(self):
        Handler.response=b'{"password":"private-malformed-value", BROKEN'
        code,data=self.invoke({'mode':'request','method':'GET','path':'/api/v2/openresty'})
        self.assertEqual(code,1); self.assertFalse(data['ok'])
        self.assertNotIn('private-malformed-value',json.dumps(data))
    def test_signed_headers_never_exposed(self):
        _,data=self.invoke({'mode':'sign'})
        self.assertNotIn('headers',data); self.assertNotIn('unit-test-not-a-real-key',json.dumps(data))

if __name__ == '__main__': unittest.main()
