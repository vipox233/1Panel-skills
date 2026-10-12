#!/usr/bin/env python3
"""Portable stdin-JSON entry point; no credentials on the command line."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'scripts'))
from ops.common import OpsError, VERSION, MAX_INPUT, obj, redact


def run(request):
    obj(request)
    action = request.get('skill_action')
    holders = [request]
    if isinstance(request.get('change'), dict): holders.append(request['change'])
    for values in holders:
        if 'content_file' in values:
            path = Path(values.pop('content_file')).expanduser()
            if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_INPUT:
                raise OpsError('BAD_INPUT', 'content_file must be a regular UTF-8 file up to 1 MiB')
            if 'content' in values: raise OpsError('BAD_INPUT', 'Use content or content_file, not both')
            values['content'] = path.read_text()
    if action in ('status', 'actions'):
        import importlib.util
        root = Path(__file__).resolve().parent
        dependencies = {'node': bool(os.environ.get('ONEPANEL_NODE') or shutil.which('node')),
                        'pyyaml': importlib.util.find_spec('yaml') is not None,
                        'docker_cli': shutil.which('docker') is not None,
                        'curl': shutil.which('curl') is not None,
                        'upstream_client': (root/'dist/scripts/client.js').is_file()}
        return {'ok': True, 'version': VERSION, 'dependencies': dependencies,
                'configured': bool(os.environ.get('ONEPANEL_BASE_URL') and os.environ.get('ONEPANEL_API_KEY')),
                'actions': ['status', 'query', 'plan', 'apply', 'verify', 'rollback', 'record', 'compose.validate',
                            'nginx.validate', 'http.check', 'proxy.compose', 'proxy.chain', 'static.release', 'static.rollback', 'static.sync'],
                'operations': ['compose.create', 'compose.update', 'compose.rebuild', 'container.operate',
                               'website.create', 'website.config', 'website.https', 'openresty.config',
                               'proxy.create', 'proxy.config', 'cron.create', 'cron.schedule', 'cron.status'],
                'permission_model': 'accident-prevention scripts; no new daemon, sudo rules or hard authorization boundary',
                'live_checked': False}
    if action == 'compose.validate':
        from ops.validators import validate_compose
        return {'ok': True, **validate_compose(request.get('content'), request.get('project'), request.get('env', ''),
                                              request.get('bind_roots', []), request.get('allow_host_network', False))}
    if action == 'nginx.validate':
        from ops.validators import lint_nginx
        return {'ok': True, **lint_nginx(request.get('content'), request.get('previous'))}
    if action == 'http.check':
        from ops.operations import probe_http
        return {'ok': True, **probe_http(request)}
    if action in ('proxy.compose', 'proxy.chain', 'static.release', 'static.rollback', 'static.sync'):
        from ops.helpers import container_proxy, proxy_chain, static_release, static_rollback, static_sync
        return {'proxy.compose': container_proxy, 'proxy.chain': proxy_chain, 'static.release': static_release,
                'static.rollback': static_rollback, 'static.sync': static_sync}[action](request)
    if action == 'record':
        from ops.common import Store
        store = Store(); return store.summary(store.load(request.get('plan_id')))
    if action not in ('query', 'plan', 'apply', 'verify', 'rollback'):
        raise OpsError('UNKNOWN_ACTION', 'Unknown skill_action; run actions to see the supported entrypoints')
    from ops.api import Panel
    api = Panel()
    if action == 'query':
        return {'ok': True, 'data': api.query(request.get('module'), request.get('action'), request.get('input', {}))}
    from ops.operations import Operations
    return getattr(Operations(api), action)(request)


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw) > MAX_INPUT: raise OpsError('BAD_INPUT', 'JSON input exceeds 1 MiB')
        request = json.loads(raw)
        result = run(request)
        if request.get('skill_action') in ('proxy.compose', 'proxy.chain') and isinstance(result.get('content'), str):
            from ops.common import Store, secure_dir, atomic_write
            import uuid
            directory = secure_dir(Store().root / 'drafts')
            candidate = directory / (uuid.uuid4().hex + '.yaml')
            atomic_write(candidate, result['content'].encode())
            result['candidate_path'] = str(candidate)
            result['content_preview'] = result.pop('content')
    except OpsError as error:
        result = error.result()
    except (ValueError, TypeError, KeyError):
        result = {'ok': False, 'code': 'BAD_INPUT', 'message': 'Input or response has an invalid shape; no guessed fallback was attempted'}
    except PermissionError:
        result = {'ok': False, 'code': 'PERMISSION_DENIED', 'message': 'The current OS user cannot access the required file. No sudo or permission changes were attempted.'}
    except Exception as error:
        result = {'ok': False, 'code': 'UNEXPECTED_ERROR', 'message': f'{type(error).__name__}; operation state may need verification, not blind retry'}
    print(json.dumps(redact(result), ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
