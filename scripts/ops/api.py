"""Call the retained upstream JavaScript client; never reimplement API signing."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit
from .common import OpsError, require, obj

class Panel:
    def __init__(self):
        self.base_url = os.environ.get('ONEPANEL_BASE_URL', '').rstrip('/')
        p = urlsplit(self.base_url)
        require(p.scheme in ('http','https') and p.hostname in ('127.0.0.1', '::1', 'localhost')
                and not p.username and not p.password and p.path in ('','/') and not p.query and not p.fragment,
                'CONFIG', 'ONEPANEL_BASE_URL must be the local panel origin, e.g. http://127.0.0.1:PORT')
        require(bool(os.environ.get('ONEPANEL_API_KEY')), 'CONFIG', 'ONEPANEL_API_KEY is not configured')
        self.node = os.environ.get('ONEPANEL_NODE') or shutil.which('node')
        require(bool(self.node), 'DEPENDENCY', 'Node.js is required by the original client; set ONEPANEL_NODE or add node to PATH')
        self.bridge = Path(__file__).resolve().parents[1] / 'ops-bridge.mjs'
    def _invoke(self, data):
        try:
            result = subprocess.run([self.node, '--max-old-space-size=128', str(self.bridge)],
                                    input=json.dumps(data), text=True, capture_output=True, timeout=70)
        except subprocess.TimeoutExpired as e:
            raise OpsError('API_UNCERTAIN', 'Client timed out. A submitted write may still be running; verify before retrying.') from e
        except OSError as e:
            raise OpsError('DEPENDENCY', 'Unable to start the original Node.js client') from e
        try: output = obj(json.loads(result.stdout), 'bridge output')
        except (ValueError, OpsError) as e:
            raise OpsError('CLIENT_ERROR', 'API bridge returned no valid JSON; verify the bundled upstream dist files and Node.js') from e
        if not output.get('ok'):
            raise OpsError(output.get('code','API_ERROR'), output.get('message','1Panel rejected the request'))
        return output.get('data')
    def call(self, method, path, body=None):
        return self._invoke({'mode':'request', 'method':method, 'path':path, 'body':body})
    def query(self, module, action, values=None):
        return self._invoke({'mode':'query', 'module':module, 'action':action, 'input':values or {}})
