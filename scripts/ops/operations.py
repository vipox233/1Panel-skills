"""Discover -> plan -> apply -> verify -> optional undo through 1Panel.

No daemon, privileged shell, destructive resource deletion, or home-made IAM.
API schemas are pinned to 1Panel v2.3.2; unfamiliar replies fail closed.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid
from .common import (OpsError, Store, obj, text, name, integer, boolean,
                     require, digest, records, content)
from .validators import (load_yaml, validate_compose, lint_nginx, port_bindings,
                         check_ports, occupied_ports, http_url, cron_spec)

PREFIX = '/api/v2'
MUTABLE_STATUS = {'planned', 'submitted', 'uncertain', 'verified', 'failed',
                  'rollback_submitted', 'rolled_back', 'rollback_uncertain', 'reconciled_unchanged'}
RECEIPT_FIELDS = {'status', 'plan_digest', 'result', 'submitted_at', 'task_id',
                  'applied_digest', 'rollback_task_id', 'last_verified_at', 'reconcile_observation'}

CRON_REQUIRED = ('id','name','type','groupID','spec','specCustom','executor','scriptMode',
                 'script','command','containerName','user','retainCopies','retryTimes','timeout',
                 'ignoreErr','scriptID','args','secret','alertCount')
CRON_EXTRA = ('appID','website','exclusionRules','dbType','dbName','url','isDir','sourceDir',
              'snapshotRule','sourceAccountIDs','downloadAccountID','alertTitle','alertMethod','scopes')

def nonempty(value):
    if isinstance(value, dict): return any(nonempty(v) for v in value.values())
    return bool(value)


def plan_hash(plan):
    return digest({k: v for k, v in plan.items() if k not in RECEIPT_FIELDS})


def exact(rows, field, value, required=True):
    selected = [r for r in rows if isinstance(r, dict) and r.get(field) == value]
    require(len(selected) <= 1, 'AMBIGUOUS_TARGET', 'More than one exact target matched')
    if required:
        require(len(selected) == 1, 'TARGET_MISSING', 'The exact target does not exist')
    return selected[0] if selected else None


def normalized_config(value):
    from .validators import nginx_directives
    return nginx_directives(value)


def same_text(a, b):
    return a.replace('\r\n', '\n').strip() == b.replace('\r\n', '\n').strip()


class Operations:
    def __init__(self, api, store=None):
        self.api, self.store = api, store or Store()
        self.instance = digest(api.base_url.rstrip('/'))

    def call(self, method, path, body=None):
        return self.api.call(method, PREFIX + path, body)

    def pages(self, path, body=None):
        rows = []
        for page in range(1, 51):
            params = {**(body or {}), 'page': page, 'pageSize': 100}
            existing = {'/containers/search': ('containers', 'searchContainers'),
                        '/websites/search': ('websites', 'searchWebsites'),
                        '/cronjobs/search': ('cronjobs', 'searchCronjobs'),
                        '/logs/tasks/search': ('task-center', 'searchTasks')}
            if path in existing:
                module, action = existing[path]
                result = self.api.query(module, action, params)
            else:
                result = self.call('POST', path, params)
            batch = records(result)
            rows.extend(batch)
            if (isinstance(result, dict) and isinstance(result.get('total'), (int, float))
                    and len(rows) >= result['total']) or len(batch) < 100:
                return rows
        raise OpsError('INVENTORY_LIMIT', 'Inventory exceeds the bounded scan; no target was assumed absent')

    def compose_rows(self):
        return self.pages('/containers/compose/search', {'info': ''})

    def container_rows(self):
        return self.pages('/containers/search', {'name': '', 'state': 'all', 'orderBy': 'createdAt', 'order': 'descending'})

    def website_rows(self):
        return self.pages('/websites/search', {'name': '', 'orderBy': 'createdAt', 'order': 'descending'})

    def cron_rows(self):
        return self.pages('/cronjobs/search', {'info': '', 'orderBy': 'createdAt', 'order': 'descending'})

    def website(self, target):
        row = obj(self.call('GET', f"/websites/{integer(target.get('id'), 'website id')}"), 'website')
        require(row.get('primaryDomain') == target.get('domain'), 'TARGET_MISMATCH',
                'Website ID no longer belongs to the selected domain')
        return row

    def compose(self, project, required=True):
        row = exact(self.compose_rows(), 'name', project, required)
        if row is None:
            return None
        require(row.get('createdBy') != 'Apps', 'APP_STORE_RESOURCE',
                'Use the application-specific upgrade path for an app-store installation')
        path = text(row.get('configFile') or row.get('path'), 'actual Compose file path', 2048)
        require(path.startswith('/') and ',' not in path and '..' not in Path(path).parts,
                'UNSUPPORTED_LAYOUT', 'Only a single absolute Compose file is supported')
        require(row.get('composeFileExists', True), 'TARGET_MISSING', 'The Compose file is missing')
        source = content(self.call('POST', '/files/content', {'path': path, 'expand': False}))
        env = row.get('env')
        require(isinstance(env, str), 'API_SHAPE', 'Compose inventory did not include its environment text')
        images = {}; image_refs = {}
        for item in row.get('containers', []):
            info = self.api.query('containers', 'inspectContainer', {'id': item['containerID'], 'type': 'container', 'detail': ''})
            if isinstance(info, str): info = json.loads(info)
            obj(info, 'container inspection')
            labels = obj(obj(info.get('Config'), 'container config').get('Labels'), 'container labels')
            service = labels.get('com.docker.compose.service')
            require(labels.get('com.docker.compose.project') == project and service, 'TARGET_MISMATCH', 'Container no longer belongs to this Compose project')
            image = info.get('Image', '')
            require(isinstance(image, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'API_SHAPE', 'Inspection did not expose the actual image ID')
            require(service not in images or images[service] == image, 'MIXED_IMAGES', 'Service replicas use different images; no single rollback version can be assumed')
            images[service] = image
            image_refs[service] = info['Config'].get('Image', '')
        return {'name': project, 'path': path, 'content': source, 'env': env,
                'createdBy': row.get('createdBy', ''), 'images': images, 'image_refs': image_refs}

    def proxy(self, target, required=True):
        self.website(target)
        return exact(records(self.call('POST', '/websites/proxies', {'id': target['id']})),
                     'name', target['proxy_name'], required)

    def snapshot(self, action, target):
        if action.startswith('compose.'):
            return self.compose(target['name'], action != 'compose.create')
        if action == 'container.operate':
            row = exact(self.container_rows(), 'name', target['name'])
            require(row.get('containerID') == target['container_id'], 'TARGET_MISMATCH', 'Container identity changed')
            return {'containerID': row['containerID'], 'name': row['name'], 'state': row['state']}
        if action == 'website.create':
            rows = self.website_rows()
            for row in rows:
                if row.get('alias') == target['alias'] or row.get('primaryDomain') == target['domain']:
                    return {'exists': True}
                for domain in records(self.call('GET', f"/websites/domains/{integer(row.get('id'), 'website id')}")):
                    if domain.get('domain') == target['domain']:
                        return {'exists': True}
            return None
        if action == 'website.config':
            self.website(target)
            return {'content': content(self.call('GET', f"/websites/{target['id']}/config/openresty"))}
        if action == 'openresty.config':
            return {'content': content(self.call('GET', '/openresty'))}
        if action in ('proxy.create', 'proxy.config'):
            row = self.proxy(target, action != 'proxy.create')
            return None if row is None else {'content': content(row)}
        if action == 'website.https':
            self.website(target)
            value = obj(self.call('GET', f"/websites/{target['id']}/https"), 'HTTPS settings')
            keys = ('enable', 'websiteSSLId', 'type', 'httpConfig', 'SSLProtocol', 'algorithm',
                    'hsts', 'hstsIncludeSubDomains', 'httpsPorts', 'http3')
            require(type(value.get('enable')) is bool and isinstance(value.get('SSL'), dict), 'API_SHAPE', 'Unfamiliar HTTPS response; no certificate binding inferred')
            result = {k: value[k] for k in keys if k in value}
            result['websiteSSLId'] = value['SSL'].get('id', 0)
            result['type'] = 'existed'
            return result
        if action.startswith('cron.'):
            row = exact(self.cron_rows(), 'name', target['name'], action != 'cron.create')
            if row is None:
                return None
            if 'id' in target:
                require(row.get('id') == target['id'], 'TARGET_MISMATCH', 'Cron task identity changed')
            if action == 'cron.schedule':
                detail = obj(self.api.query('cronjobs', 'loadCronjobInfo', {'id': row['id']}), 'cron detail')
                require(detail.get('id') == row['id'] and detail.get('name') == row['name'], 'TARGET_MISMATCH', 'Cron detail identity changed')
                require(all(k in detail for k in CRON_REQUIRED), 'API_SHAPE', 'Cron detail is incomplete; no schedule update is safe')
                require(detail['type'] == 'shell' and detail['scriptMode'] == 'input' and not detail['alertCount']
                        and not any(nonempty(detail.get(k)) for k in ('scriptID','args','secret', *(k for k in CRON_EXTRA if k not in ('dbType','isDir')))),
                        'UNSUPPORTED', 'Schedule-only changes support complete inline shell jobs without library, argument, secret or special settings')
                return {k: copy.deepcopy(detail[k]) for k in (*CRON_REQUIRED, *CRON_EXTRA, 'status') if k in detail}
            keys = ('id', 'name', 'type', 'spec', 'specCustom', 'groupID', 'executor', 'scriptMode',
                    'script', 'command', 'containerName', 'user', 'retainCopies', 'retryTimes',
                    'timeout', 'ignoreErr', 'status', 'alertCount')
            return {k: row[k] for k in keys if k in row}
        raise OpsError('UNKNOWN_ACTION', 'Unsupported operation')

    def _compose_checks(self, source, project, env, options, before=None):
        roots = options.get('bind_roots', [])
        require(isinstance(roots, list) and all(isinstance(p, str) and p.startswith('/') for p in roots),
                'BAD_INPUT', 'bind_roots must explicitly list absolute data directories')
        host = boolean(options.get('allow_host_network', False), 'allow_host_network')
        networks = options.get('allow_networks', [])
        mutable = boolean(options.get('allow_mutable_images', False), 'allow_mutable_images')
        report = validate_compose(source, project, env, roots, host, networks, mutable)
        if networks:
            available = self.pages('/containers/network/search', {'info': ''})
            require(all(any(row.get('name', row.get('Name')) == n for row in available) for n in networks),
                    'TARGET_MISSING', 'Every allowed external network must exist in the bounded inventory')
        prior = port_bindings(load_yaml(before['content'])) if before else []
        check_ports(report['ports'], occupied_ports(), prior)
        candidates = load_yaml(source)['services']
        own_names = set()
        if before:
            row = exact(self.compose_rows(), 'name', project)
            own_names = {c['name'] for c in row.get('containers', [])}
            prior_services = load_yaml(before['content']).get('services', {})
            require(set(prior_services) <= set(candidates), 'SERVICE_REMOVAL',
                    'Removing existing services is not part of this update workflow')
            require(set(candidates) <= set(prior_services), 'SERVICE_ADDITION', 'Adding services is a separate deployment; ordinary updates preserve the service set')
            for key, old in prior_services.items():
                require(old.get('volumes', []) == candidates[key].get('volumes', []), 'MOUNT_CHANGE',
                        'Changing existing persistent mounts needs a separate migration, not an ordinary update')
        for container in self.container_rows():
            for value in candidates.values():
                if value.get('container_name') == container.get('name') and container['name'] not in own_names:
                    raise OpsError('NAME_CONFLICT', 'A container with this explicit name already exists outside the project')
        return report

    def plan(self, request):
        action = text(request.get('operation'), 'operation', 60)
        target = copy.deepcopy(obj(request.get('target'), 'target'))
        change = copy.deepcopy(obj(request.get('change', {}), 'change'))
        options = copy.deepcopy(obj(request.get('options', {}), 'options'))
        warnings = []
        health = request.get('health')
        if health:
            obj(health, 'health'); http_url(health.get('url'))
            codes = health.get('codes', [200])
            require(isinstance(codes, list) and all(type(c) is int and 100 <= c <= 599 for c in codes),
                    'BAD_INPUT', 'health.codes must be HTTP status numbers')
        if action.startswith('compose.'):
            name(target.get('name'), 'project')
        elif action == 'container.operate':
            name(target.get('name')); text(target.get('container_id'), 'container ID', 100)
        elif action.startswith('website.') or action.startswith('proxy.'):
            text(target.get('domain'), 'domain', 253)
            # Existing 1Panel identities may include a listener port. Keep the
            # exact inventory identity for the subsequent ID/domain match.
            pattern = r'[A-Za-z0-9.-]+' if action == 'website.create' else r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?'
            require(re.fullmatch(pattern, target['domain']) is not None,
                    'BAD_INPUT', 'Use the exact ASCII domain from inventory; existing sites may include a port')
            if ':' in target['domain']:
                integer(int(target['domain'].rsplit(':', 1)[1]), 'domain port', 1, 65535)
            if action == 'website.create': name(target.get('alias'), 'site alias')
            else: integer(target.get('id'), 'website id')
            if action.startswith('proxy.'): name(target.get('proxy_name'), 'proxy name')
        elif action.startswith('cron.'):
            text(target.get('name'), 'task name', 100)
            require(not any(c in target['name'] for c in ('/', '\\', '\n', '\r', '..')), 'BAD_NAME', 'Task name must not be a path')
        elif action != 'openresty.config':
            raise OpsError('UNKNOWN_ACTION', 'Unsupported operation')
        before = self.snapshot(action, target)
        if action in ('compose.create', 'website.create', 'proxy.create', 'cron.create'):
            require(before is None, 'ALREADY_EXISTS', 'Refusing to create over an existing resource')
        rollback_payload = None
        task_id = uuid.uuid4().hex
        if action in ('compose.create', 'compose.update'):
            source = text(change.get('content'), 'Compose content')
            env = text(change.get('env', before['env'] if before else ''), 'env', empty=True)
            report = self._compose_checks(source, target['name'], env, options, before)
            warnings.extend(report['warnings'])
            options['expected_images'] = report.get('images', {})
            if action == 'compose.create':
                payload = {'name': target['name'], 'dirName': target['name'], 'from': 'edit',
                           'file': source, 'env': env, 'forcePull': boolean(change.get('force_pull', False), 'force_pull'), 'taskID': task_id}
            else:
                payload = {'name': target['name'], 'path': before['path'], 'detailPath': before['path'],
                           'content': source, 'env': env, 'forcePull': boolean(change.get('force_pull', False), 'force_pull'), 'taskID': task_id}
            if before:
                rollback_config = load_yaml(before['content'])
                for service, values in rollback_config['services'].items():
                    if service in before['images']:
                        values['image'] = before['images'][service]
                        values['pull_policy'] = 'never'
                    else:
                        require(values.get('profiles'), 'ROLLBACK_UNAVAILABLE', 'No prior runtime image for a required service; inspect this partial deployment first')
                from .helpers import dump_yaml
                rollback_payload = {**payload, 'content': dump_yaml(rollback_config), 'env': before['env'], 'forcePull': False}
            warnings.append('Undo preserves the previous local image IDs where available and does not roll back application/database data. Old images must remain present.')
        elif action == 'compose.rebuild':
            report = self._compose_checks(before['content'], target['name'], before['env'], options, before)
            warnings.extend(report['warnings']); options['expected_images'] = report.get('images', {})
            payload = {'name': target['name'], 'path': before['path'], 'operation': 'rebuild', 'withFile': False, 'force': False}
            warnings.append('Rebuild stops and recreates this project; it does not delete its persistent volumes.')
        elif action == 'container.operate':
            operation = change.get('operation')
            require(operation in ('start', 'stop', 'restart'), 'DENIED', 'Only start, stop, and restart are implemented')
            payload = {'names': [target['name']], 'operation': operation, 'taskID': task_id}
        elif action in ('website.config', 'openresty.config', 'proxy.config'):
            source = text(change.get('content'), 'Nginx content')
            lint_nginx(source, before['content'])
            payload = {'content': source}
            if action == 'website.config': payload['id'] = target['id']
            elif action == 'proxy.config': payload.update(websiteID=target['id'], name=target['proxy_name'])
            else: payload['backup'] = True
            warnings.append('Preview checks structure and dangerous changes. Actual nginx -t/reload is performed by 1Panel during save, not during preview.')
        elif action == 'proxy.create':
            upstream = http_url(change.get('upstream'))
            match = text(change.get('match', '/'), 'location', 200)
            host = text(change.get('host', '$host'), 'Host header', 253)
            require(re.fullmatch(r'/[A-Za-z0-9_./~-]*', match) is not None and '..' not in match,
                    'BAD_INPUT', 'Use a simple location path')
            require(re.fullmatch(r'(\$host|\$http_host|[A-Za-z0-9_.:-]+)', host) is not None,
                    'BAD_INPUT', 'Invalid proxy Host header')
            payload = {'id': target['id'], 'operate': 'create', 'enable': True,
                       'name': target['proxy_name'], 'match': match, 'modifier': '',
                       'proxyPass': change['upstream'], 'proxyHost': host, 'cache': False,
                       'cacheTime': 0, 'replaces': {}, 'sni': upstream.scheme == 'https',
                       'sslVerify': upstream.scheme == 'https', 'cors': False}
        elif action == 'website.create':
            kind = change.get('type', 'proxy')
            require(kind in ('proxy', 'static'), 'UNSUPPORTED', 'Only static and reverse-proxy sites are supported')
            group = integer(change.get('group_id'), 'existing website group ID')
            payload = {'type': kind, 'alias': target['alias'], 'webSiteGroupID': group,
                       'domains': [{'domain': target['domain'], 'port': integer(change.get('port', 80), 'port', 1, 65535), 'ssl': False}],
                       'appType': 'installed', 'createDb': False, 'enableSSL': False,
                       'IPV6': boolean(change.get('ipv6', False), 'ipv6'), 'taskID': task_id}
            if kind == 'proxy': http_url(change.get('upstream')); payload['proxy'] = change['upstream']
        elif action == 'website.https':
            require(before.get('type') in (None, '', 'existed'), 'UNSUPPORTED',
                    'This helper only changes bindings to certificates already managed by 1Panel')
            cert_id = integer(change.get('certificate_id'), 'existing certificate ID')
            certificate = obj(self.api.query('websites', 'getCertificate', {'id': cert_id}), 'certificate')
            require(certificate.get('id') == cert_id, 'TARGET_MISMATCH', 'Certificate lookup did not match its ID')
            payload = {**before, 'websiteId': target['id'], 'type': 'existed', 'enable': True,
                       'websiteSSLId': cert_id, 'httpConfig': change.get('http_config', 'HTTPToHTTPS')}
            require(payload['httpConfig'] in ('HTTPSOnly', 'HTTPAlso', 'HTTPToHTTPS'), 'BAD_INPUT', 'Invalid HTTPS mode')
        elif action in ('cron.create', 'cron.schedule'):
            spec = cron_spec(change.get('spec'))
            self.call('POST', '/cronjobs/next', {'spec': spec})
            if action == 'cron.schedule':
                require(before.get('type') == 'shell' and not before.get('alertCount'), 'UNSUPPORTED', 'This schedule helper supports existing shell jobs without alert configuration; it preserves the existing user and script')
                payload = {k: v for k, v in before.items() if k != 'status'}
                payload.update(spec=spec, specCustom=True)
            else:
                script = text(change.get('script'), 'existing script path', 2048)
                require(script.startswith('/') and '..' not in Path(script).parts and script.endswith(('.py', '.sh')),
                        'UNSAFE_PATH', 'Use an existing absolute .py or .sh script path')
                user = name(change.get('user'), 'non-root execution user')
                require(user != 'root', 'DENIED', 'New script jobs use a named non-root user')
                args = change.get('args', [])
                require(isinstance(args, list) and len(args) <= 32 and all(isinstance(x, str) and '\x00' not in x for x in args),
                        'BAD_INPUT', 'args must be an array of strings')
                interpreter = '/usr/bin/python3' if script.endswith('.py') else '/bin/sh'
                require(Path(script).is_file() and not Path(script).is_symlink(), 'TARGET_MISSING', 'Script must exist locally and must not be a symlink')
                command = shlex.join([interpreter, script, *args])
                options['script_path'] = script
                options['script_digest'] = digest(Path(script).read_bytes().hex())
                payload = {'name': target['name'], 'type': 'shell', 'groupID': 0,
                           'spec': spec, 'specCustom': True, 'executor': 'bash',
                           'scriptMode': 'input', 'script': command, 'command': command,
                           'user': user, 'containerName': '', 'retainCopies': 7,
                           'retryTimes': 0, 'timeout': integer(change.get('timeout', 120), 'timeout', 1, 3600), 'ignoreErr': False}
                warnings.append('The existing script runs with the specified user. Script contents must be reviewed separately; this is not a shell sandbox.')
        elif action == 'cron.status':
            status = change.get('status')
            require(status in ('Enable', 'Disable'), 'BAD_INPUT', 'status must be Enable or Disable')
            payload = {'id': before['id'], 'status': status}
        else:
            raise OpsError('UNKNOWN_ACTION', 'Unsupported operation')
        with self.store.lock():
            summary = self.store.create(action, target, before, payload, self.instance,
                                        options=options, health=health, warnings=warnings, rollback_payload=rollback_payload)
        return summary

    def load_plan(self, request):
        plan = self.store.load(request.get('plan_id'))
        require(plan.get('instance') == self.instance, 'TARGET_MISMATCH', 'Plan was made for another panel origin')
        require(plan.get('plan_digest') == plan_hash(plan), 'PLAN_CHANGED', 'Change record contents do not match the saved digest')
        require(plan.get('status') in MUTABLE_STATUS, 'BAD_PLAN', 'Unknown record state')
        return plan

    def _other_pending(self, plan):
        for path in self.store.root.glob('*.json'):
            other = self.store.load(path.stem)
            if other['plan_id'] == plan['plan_id'] or other.get('instance') != self.instance:
                continue
            if other.get('status') in ('submitted', 'uncertain', 'rollback_submitted', 'rollback_uncertain'):
                raise OpsError('PENDING_OPERATION', 'Verify the previous pending operation before another write')

    def endpoint(self, action, target):
        paths = {'compose.create': '/containers/compose', 'compose.update': '/containers/compose/update',
                 'compose.rebuild': '/containers/compose/operate', 'container.operate': '/containers/operate',
                 'website.create': '/websites', 'website.config': '/websites/nginx/update',
                 'openresty.config': '/openresty/file', 'proxy.create': '/websites/proxies/update',
                 'proxy.config': '/websites/proxies/file', 'cron.create': '/cronjobs',
                 'cron.schedule': '/cronjobs/update', 'cron.status': '/cronjobs/status'}
        return f"/websites/{target['id']}/https" if action == 'website.https' else paths[action]

    def apply(self, request):
        with self.store.lock():
            plan = self.load_plan(request)
            require(request.get('plan_digest') == plan['plan_digest'], 'PLAN_CHANGED', 'Supply the exact digest returned by plan')
            require(plan['status'] == 'planned', 'ALREADY_SUBMITTED', 'This plan was already used; verify it instead of retrying')
            require(time.time() - plan['created_at'] <= 1800, 'PLAN_EXPIRED', 'Plan expired; inspect the current target and plan again')
            self._other_pending(plan)
            if plan['action'] == 'cron.create':
                script = Path(plan['options']['script_path'])
                require(script.is_file() and not script.is_symlink() and digest(script.read_bytes().hex()) == plan['options']['script_digest'], 'TARGET_CHANGED', 'Scheduled script changed after planning')
            current = self.snapshot(plan['action'], plan['target'])
            require(digest(current) == digest(plan['before']), 'TARGET_CHANGED', 'Target changed after planning; no write was sent')
            if plan['action'] == 'compose.rebuild':
                self._compose_checks(current['content'], plan['target']['name'], current['env'], plan['options'], current)
            if plan['action'] in ('compose.create', 'compose.update'):
                body = plan['payload']
                self._compose_checks(body.get('content', body.get('file')), plan['target']['name'],
                                     body['env'], plan['options'], plan['before'])
            plan['status'] = 'submitted'; plan['submitted_at'] = time.time()
            plan['task_id'] = plan['payload'].get('taskID')
            self.store.save(plan)
            try:
                self.call('POST', self.endpoint(plan['action'], plan['target']), plan['payload'])
            except OpsError as e:
                plan['status'] = 'failed' if e.code in ('DENIED', 'CONFIG', 'DEPENDENCY', 'API_REJECTED') else 'uncertain'
                plan['result'] = {'code': e.code, 'message': e.message}
                self.store.save(plan)
                return self.store.summary(plan)
        return self.verify({'plan_id': plan['plan_id']})

    def task_state(self, task_id):
        if not task_id:
            return None
        for row in self.pages('/logs/tasks/search', {'type': '', 'status': ''}):
            if row.get('id') == task_id or row.get('taskID') == task_id:
                return row.get('status')
        return None

    def expected_matches(self, plan, actual, rollback=False):
        if rollback:
            if not isinstance(actual, dict) or not isinstance(plan['before'], dict): return False
            if plan['action'] == 'compose.update' and plan.get('rollback_payload'):
                saved = plan['rollback_payload']
                return same_text(actual.get('content', ''), saved['content']) and actual.get('env') == saved['env'] and actual.get('images') == plan['before'].get('images')
            if 'content' in plan['before']:
                return same_text(actual.get('content', ''), plan['before']['content']) and actual.get('env') == plan['before'].get('env')
            return all(actual.get(k) == v for k, v in plan['before'].items())
        action, body = plan['action'], plan['payload']
        if action.startswith('compose.') and action != 'compose.rebuild':
            return isinstance(actual, dict) and same_text(actual['content'], body.get('content', body.get('file'))) and actual['env'] == body['env']
        if action in ('website.config', 'openresty.config', 'proxy.config'):
            return isinstance(actual, dict) and normalized_config(actual['content']) == normalized_config(body['content'])
        if action == 'container.operate':
            return actual['state'] == ('exited' if body['operation'] == 'stop' else 'running')
        if action == 'compose.rebuild': return True
        if action in ('website.create', 'proxy.create'): return actual is not None
        if action == 'website.https':
            return actual.get('enable') is True and actual.get('websiteSSLId') == body['websiteSSLId'] and actual.get('httpConfig') == body['httpConfig']
        if action == 'cron.status': return actual.get('status') == body['status']
        if action in ('cron.create', 'cron.schedule'):
            if action == 'cron.schedule':
                return actual is not None and all(actual.get(k) == v for k, v in body.items())
            return actual is not None and all(actual.get(k) == v for k, v in body.items() if k in ('name', 'spec', 'script', 'user'))
        return False

    def verify(self, request):
        with self.store.lock():
            plan = self.load_plan(request)
            if plan['status'] == 'reconciled_unchanged': return self.store.summary(plan)
            require(plan['status'] != 'planned', 'NOT_SUBMITTED', 'Plan has not been applied')
            rollback = plan['status'].startswith('rollback') or plan['status'] == 'rolled_back'
            state = self.task_state(plan.get('rollback_task_id') if rollback else plan.get('task_id'))
            if state is not None and str(state).lower() not in ('success', 'failed', 'error', 'completed'):
                plan['result'] = {'task_status': state, 'message': 'Task is still running or its status is unfamiliar'}
                self.store.save(plan); return self.store.summary(plan)
            if (plan.get('task_id') or plan.get('rollback_task_id')) and state is None:
                actual = self.snapshot(plan['action'], plan['target'])
                plan['result'] = {'unchanged': digest(actual) == digest(plan['before']),
                                  'message': 'Task receipt missing. Do not retry apply. Use reconcile only after the user confirms no delayed operation can still execute.'}
                self.store.save(plan); return self.store.summary(plan)
            actual = self.snapshot(plan['action'], plan['target'])
            matched = self.expected_matches(plan, actual, rollback)
            running = True
            if plan['action'].startswith('compose.') and matched:
                row = exact(self.compose_rows(), 'name', plan['target']['name'])
                running = row.get('containerCount', 0) > 0 and row.get('runningCount') == row.get('containerCount')
            task_ok = state is None or str(state).lower() in ('success', 'completed')
            health = None
            if matched and running and task_ok and plan.get('health'):
                health = probe_http(plan['health'])
            healthy = health is None or health['healthy']
            if plan['action'].startswith('compose.') and matched and not rollback:
                refs = actual.get('image_refs', {})
                for service, image in plan.get('options', {}).get('expected_images', {}).items():
                    if '@sha256:' in image or image.startswith('sha256:'):
                        expected = image.rsplit('@', 1)[-1]
                        running = running and (refs.get(service, '').rsplit('@', 1)[-1] == expected or actual.get('images', {}).get(service) == expected)
            if matched and running and task_ok and healthy:
                plan['status'] = 'rolled_back' if rollback else 'verified'
                plan['applied_digest'] = digest(actual)
                plan['result'] = {'configuration_matches': True, 'runtime_running': running,
                                  'task_status': state, 'health': health,
                                  'deployed_images': actual.get('images', {}) if isinstance(actual, dict) else {},
                                  'message': 'Readback verified; application health is only checked when a health URL is supplied'}
            else:
                plan['status'] = 'rollback_uncertain' if rollback else 'failed'
                plan['result'] = {'configuration_matches': matched, 'runtime_running': running,
                                  'task_status': state, 'health': health,
                                  'message': 'Verification failed. Preserve this change record; do not repeat apply.'}
            plan['last_verified_at'] = time.time()
            self.store.save(plan)
            summary = self.store.summary(plan)
        if (not rollback and matched and task_ok and health is not None and not healthy
                and plan['action'] in ('website.config', 'openresty.config', 'proxy.config')):
            return self.rollback({'plan_id': plan['plan_id'], 'automatic': True})
        return summary

    def partial_compose_failure(self, plan, actual, state):
        if plan['action'] != 'compose.update' or str(state).lower() not in ('failed', 'error') or not isinstance(actual, dict):
            return False
        before, body = plan['before'], plan['payload']
        return (all(actual.get(k) == before.get(k) for k in ('name', 'path', 'createdBy'))
                and same_text(actual.get('content', ''), before['content'])
                and actual.get('env') == body['env'])

    def reconcile(self, request):
        """Close an unchanged abandoned write only after operator confirmation and stable reads.

        This records an operator-assisted recovery, never proof the write did not execute.
        The caller must not manufacture the acknowledgement from elapsed time alone.
        """
        with self.store.lock():
            plan = self.load_plan(request)
            require(request.get('plan_digest') == plan['plan_digest'], 'PLAN_CHANGED', 'Use the exact plan digest')
            require(plan['status'] in ('submitted', 'uncertain') and plan.get('task_id'), 'BAD_PLAN', 'Only missing-receipt forward writes can be reconciled')
            require(request.get('acknowledge_no_inflight') is True, 'NEEDS_CONFIRMATION', 'The user must confirm the old request/task cannot still execute; do not infer this from time or a snapshot')
            now = time.time()
            require(now - plan.get('submitted_at', plan['created_at']) >= 300, 'PENDING_OPERATION', 'Wait at least five minutes before recovery')
            try:
                require(self.task_state(plan['task_id']) is None, 'PENDING_OPERATION', 'A task receipt exists; use verify')
                count = self.api.query('task-center', 'countExecutingTasks', {})
                require(type(count) is int and count == 0, 'PENDING_OPERATION', 'Executing count is nonzero or unfamiliar')
            except Exception:
                plan.pop('reconcile_observation', None); self.store.save(plan)
                raise
            actual = self.snapshot(plan['action'], plan['target'])
            current = digest(actual)
            if current != digest(plan['before']):
                plan.pop('reconcile_observation', None); self.store.save(plan)
                raise OpsError('TARGET_CHANGED', 'Target differs from before; preserve evidence and investigate')
            observation = plan.get('reconcile_observation')
            if not observation or observation.get('digest') != current:
                plan['reconcile_observation'] = {'at': now, 'digest': current}
                plan['result'] = {'message': 'First unchanged observation recorded; repeat reconciliation after at least 60 seconds with user confirmation still valid'}
            else:
                require(now - observation['at'] >= 60, 'PENDING_OPERATION', 'Need two unchanged observations at least 60 seconds apart')
                plan['status'] = 'reconciled_unchanged'
                plan['result'] = {'unchanged': True, 'operator_confirmed_no_inflight': True,
                                  'message': 'Recovery closed this record without writes. This is not deployment success or proof the original request never executed; create a fresh plan for future work.'}
            self.store.save(plan)
            return self.store.summary(plan)

    def rollback(self, request):
        with self.store.lock():
            plan = self.load_plan(request)
            require(plan['status'] in ('verified', 'failed', 'uncertain'), 'NOT_ROLLBACKABLE', 'Verify pending tasks before undo')
            require(plan['action'] in ('compose.update', 'website.config', 'openresty.config', 'proxy.config',
                                       'website.https', 'cron.schedule', 'cron.status'),
                    'NOT_ROLLBACKABLE', 'No destructive undo for newly created resources or lifecycle operations')
            self._other_pending(plan)
            state = self.task_state(plan.get('task_id'))
            require(not plan.get('task_id') or str(state).lower() in ('success', 'failed', 'error', 'completed'),
                    'PENDING_OPERATION', 'Cannot undo while the previous task is unconfirmed or running')
            actual = self.snapshot(plan['action'], plan['target'])
            require(self.expected_matches(plan, actual) or self.expected_matches(plan, actual, True) or digest(actual) == digest(plan['before']) or self.partial_compose_failure(plan, actual, state),
                    'TARGET_CHANGED', 'Configuration differs from both saved and proposed versions; do not overwrite another edit')
            body = copy.deepcopy(plan['payload'])
            if plan['action'] == 'compose.update':
                require(plan.get('rollback_payload') is not None, 'ROLLBACK_UNAVAILABLE', 'No saved image-pinned rollback candidate')
                body = {**plan['rollback_payload'], 'taskID': uuid.uuid4().hex}
                self._compose_checks(body['content'], plan['target']['name'], body['env'], plan['options'], actual)
                plan['rollback_task_id'] = body['taskID']
            elif 'content' in plan['before']: body['content'] = plan['before']['content']
            elif plan['action'] == 'website.https': body = {**plan['before'], 'websiteId': plan['target']['id']}
            elif plan['action'] == 'cron.status': body = {'id': plan['before']['id'], 'status': plan['before']['status']}
            else: body = {k: v for k, v in plan['before'].items() if k != 'status'}
            plan['status'] = 'rollback_submitted'; self.store.save(plan)
            try: self.call('POST', self.endpoint(plan['action'], plan['target']), body)
            except OpsError as e:
                plan['status'] = 'rollback_uncertain'; plan['result'] = {'code': e.code, 'message': e.message}
                self.store.save(plan); return self.store.summary(plan)
        return self.verify({'plan_id': plan['plan_id']})


def probe_http(request):
    """Use curl for correct TLS/SNI and optional local-origin --resolve checks."""
    import shutil
    obj(request); parsed = http_url(request.get('url'))
    curl = shutil.which('curl'); require(curl is not None, 'DEPENDENCY', 'curl is required for HTTP health checks')
    timeout = integer(request.get('timeout', 10), 'timeout', 1, 60)
    command = [curl, '--silent', '--show-error', '--noproxy', '*', '--max-time', str(timeout),
               '--output', os.devnull, '--write-out', '%{http_code} %{time_connect} %{time_starttransfer} %{time_total}']
    address = request.get('resolve_ip')
    if address:
        import ipaddress
        try: ipaddress.ip_address(address)
        except ValueError as e: raise OpsError('BAD_INPUT', 'resolve_ip must be an IP address') from e
        address = f'[{address}]' if ':' in address else address
        command += ['--resolve', f"{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}:{address}"]
    command += ['--url', request['url']]
    try: result = subprocess.run(command, capture_output=True, text=True, timeout=timeout+3)
    except subprocess.TimeoutExpired: return {'healthy': False, 'error': 'timeout'}
    values = result.stdout.split()
    code = int(values[0]) if values and values[0].isdigit() else 0
    return {'healthy': result.returncode == 0 and code in request.get('codes', [200]), 'status': code,
            'connect_seconds': values[1] if len(values)>1 else None,
            'first_byte_seconds': values[2] if len(values)>2 else None,
            'total_seconds': values[3] if len(values)>3 else None,
            'curl_exit': result.returncode, 'origin_override': bool(address)}
