"""Offline proxy helpers and checked static releases; no production changes by default."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile
from urllib.parse import urlsplit
from .common import OpsError, require, obj, text, atomic_write
from .validators import load_yaml, safe_relative


def dump_yaml(config):
    import yaml
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def container_proxy(request):
    config = load_yaml(text(request.get('content'), 'Compose content'))
    service = text(request.get('service'), 'service', 100)
    require(service in obj(config.get('services'), 'services'), 'TARGET_MISSING', 'No such service in Compose')
    target = obj(config['services'][service], 'service')
    url = text(request.get('proxy'), 'proxy URL', 2000)
    p = urlsplit(url)
    require(p.scheme in ('http', 'https', 'socks5', 'socks5h') and p.hostname and not p.fragment
            and not p.username and not p.password, 'BAD_URL', 'Use a proxy URL without embedded credentials')
    try: require(p.port is not None, 'BAD_URL', 'Specify the proxy port explicitly')
    except ValueError as e: raise OpsError('BAD_URL', 'Invalid proxy port') from e
    require(target.get('network_mode') == 'host' or p.hostname not in ('localhost', '127.0.0.1', '::1'),
            'CONTAINER_LOOPBACK', 'Container loopback is not the host. Use a reachable proxy service address; host networking is never added automatically.')
    env = target.get('environment', {})
    if isinstance(env, list):
        require(all(isinstance(x, str) and '=' in x for x in env), 'BAD_INPUT', 'Resolve inherited environment entries before editing')
        env = dict(x.split('=', 1) for x in env)
    obj(env, 'environment')
    exclusions = text(request.get('no_proxy', 'localhost,127.0.0.1,::1'), 'no_proxy', 4000)
    if p.scheme.startswith('socks'):
        env.update(ALL_PROXY=url, all_proxy=url)
    else:
        env.update(HTTP_PROXY=url, HTTPS_PROXY=url, http_proxy=url, https_proxy=url)
    env.update(NO_PROXY=exclusions, no_proxy=exclusions)
    target['environment'] = env
    return {'ok': True, 'content': dump_yaml(config), 'applied': False,
            'warnings': ['The application must support these proxy variables.',
                         'A host-gateway address cannot reach a proxy that listens only on host loopback.',
                         'Submit the candidate through compose.update; no file, container or listener was changed.']}


def proxy_chain(request):
    config = load_yaml(text(request.get('content'), 'Mihomo configuration'))
    exit_name = text(request.get('exit_proxy'), 'exit proxy', 200)
    via = text(request.get('via'), 'intermediate proxy/group', 200)
    proxies = config.get('proxies', [])
    groups = config.get('proxy-groups', [])
    require(isinstance(proxies, list) and isinstance(groups, list), 'BAD_INPUT', 'Expected proxies and proxy-groups lists')
    known = {}
    for entry in proxies + groups:
        obj(entry); label = text(entry.get('name'), 'proxy name', 200)
        require(label not in known, 'AMBIGUOUS_TARGET', 'Duplicate proxy/group name')
        known[label] = entry
    exits = [entry for entry in proxies if entry.get('name') == exit_name]
    require(len(exits) == 1 and via in known, 'TARGET_MISSING', 'Exit must be a concrete local proxy and via must already exist')
    require(exit_name != via, 'PROXY_CYCLE', 'A proxy cannot dial through itself')
    graph = {}
    for label, entry in known.items():
        edges = list(entry.get('proxies', []))
        if entry.get('dialer-proxy'): edges.append(entry['dialer-proxy'])
        graph[label] = edges
    graph[exit_name] = [via]
    visiting, visited = set(), set()
    def visit(node):
        require(node not in visiting, 'PROXY_CYCLE', 'Proxy/group references would form a cycle')
        if node in visited or node not in graph: return
        visiting.add(node)
        for child in graph[node]: visit(child)
        visiting.remove(node); visited.add(node)
    for node in graph: visit(node)
    exits[0]['dialer-proxy'] = via
    return {'ok': True, 'content': dump_yaml(config), 'applied': False, 'mihomo_runtime_checked': False,
            'warnings': ['Only structure and local-reference cycles were checked.',
                         'Provider-supplied references and protocol compatibility still require the installed Mihomo parser and a live connectivity test.',
                         'No provider URL, password, listener or running service was changed.']}


def static_release(request):
    """Stage a checksum-verified ZIP and switch a managed current symlink.

    Needs a dedicated writable directory. First migration of an existing site
    is separate; this function does not chmod/chown an existing 1Panel website.
    """
    artifact = Path(text(request.get('artifact'), 'artifact path', 2048)).expanduser()
    root = Path(text(request.get('root'), 'dedicated release directory', 2048)).expanduser()
    require(type(request.get('apply', False)) is bool, 'BAD_INPUT', 'apply must be a boolean')
    require(not any(p.is_symlink() for p in (root, *root.parents)), 'UNSAFE_PATH', 'Release path must not traverse a symlink')
    require('..' not in root.parts, 'UNSAFE_PATH', 'Release path must not contain parent traversal')
    checksum = text(request.get('sha256'), 'sha256', 64)
    require(len(checksum) == 64 and all(c in '0123456789abcdef' for c in checksum), 'BAD_INPUT', 'Expected a lowercase SHA-256 digest')
    require(artifact.is_file() and not artifact.is_symlink(), 'UNSAFE_PATH', 'Artifact must be a regular ZIP file')
    require(artifact.stat().st_size <= 50*1024*1024, 'ARTIFACT_LIMIT', 'ZIP exceeds the 50 MiB compressed limit')
    require(root.is_absolute() and root not in (Path('/'), Path.home()) and not root.is_symlink(),
            'UNSAFE_PATH', 'Use a dedicated absolute releases directory, not a system or home root')
    for critical in ('/etc', '/proc', '/sys', '/dev', '/run', '/var/lib/docker', '/opt/1panel/db'):
        p = Path(critical)
        require(root != p and p not in root.parents, 'UNSAFE_PATH', 'Do not publish into a system directory')
    data = artifact.read_bytes()
    require(hashlib.sha256(data).hexdigest() == checksum, 'CHECKSUM_MISMATCH', 'Artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= 10_000 and sum(x.file_size for x in entries) <= 200*1024*1024,
                'ARTIFACT_LIMIT', 'ZIP contains too many files or too much decompressed data')
        names = set()
        for entry in entries:
            path = safe_relative(entry.filename)
            require(entry.filename not in names, 'BAD_ARCHIVE', 'Duplicate archive entry')
            names.add(entry.filename)
            mode = entry.external_attr >> 16
            require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                    'UNSAFE_PATH', 'Archive links/devices are not allowed')
            require(not any(part.startswith('.') or part in ('data', 'uploads') for part in path.parts),
                    'PROTECTED_DATA', 'Archive must not contain hidden files, data or uploads directories')
        require('index.html' in names, 'BAD_ARCHIVE', 'Archive must contain index.html at its root')
        current = root / 'current'
        require(not (current.exists() and not current.is_symlink()), 'ALREADY_EXISTS', 'Refusing to replace a real website directory with a symlink')
        previous = os.readlink(current) if current.is_symlink() else None
        if previous:
            require(previous.startswith('releases/') and '..' not in Path(previous).parts and not Path(previous).is_absolute(),
                    'UNSAFE_PATH', 'Existing current link is not managed by this release helper')
        if not request.get('apply', False):
            return {'ok': True, 'applied': False, 'sha256': checksum, 'files': len(entries), 'previous': previous,
                    'note': 'Preview only. root/current must already be used as the static web root for switching to affect a site.'}
        root.mkdir(parents=True, exist_ok=True)
        require(not root.is_symlink(), 'UNSAFE_PATH', 'Release root changed into a symlink')
        releases = root / 'releases'
        require(not releases.is_symlink(), 'UNSAFE_PATH', 'Release directory must not be a symlink')
        releases.mkdir(exist_ok=True)
        release = releases / checksum
        if not release.exists():
            temporary = Path(tempfile.mkdtemp(prefix='.stage-', dir=releases))
            try:
                for entry in entries:
                    dest = temporary / safe_relative(entry.filename)
                    if entry.is_dir(): dest.mkdir(parents=True, exist_ok=True); continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, open(dest, 'xb') as out:
                        shutil.copyfileobj(source, out)
                    dest.chmod(0o644)
                for directory, _, _ in os.walk(temporary): Path(directory).chmod(0o755)
                temporary.rename(release)
            finally:
                if temporary.exists(): shutil.rmtree(temporary)  # Only this invocation's own temporary directory.
        require(release.is_dir() and not release.is_symlink() and (release/'index.html').is_file(),
                'BAD_RELEASE', 'Existing release is not a valid managed directory')
        shared = root / 'data'
        if shared.is_dir():
            require(not shared.is_symlink(), 'UNSAFE_PATH', 'Shared data directory must not be a symlink')
            link = release / 'data'
            if link.is_symlink():
                require(os.readlink(link) == '../../data', 'PROTECTED_DATA', 'Unexpected data link inside release')
            else:
                require(not link.exists(), 'PROTECTED_DATA', 'Unexpected data path inside release')
                os.symlink('../../data', link)
        require((os.readlink(current) if current.is_symlink() else None) == previous,
                'TARGET_CHANGED', 'Current release changed while staging; refusing to switch')
        temp_link = root / ('.current-' + os.urandom(8).hex())
        os.symlink('releases/' + checksum, temp_link)
        os.replace(temp_link, current)
        receipt = {'current': 'releases/' + checksum, 'previous': previous, 'sha256': checksum}
        atomic_write(root/'last-release.json', json.dumps(receipt).encode())
        return {'ok': True, 'applied': True, **receipt,
                'note': 'Old releases and data were not deleted. Verify HTTP after switching; this does not update 1Panel site settings.'}


def static_rollback(request):
    import re
    root = Path(text(request.get('root'), 'dedicated release directory', 2048)).expanduser()
    require(root.is_absolute() and '..' not in root.parts and not any(p.is_symlink() for p in (root, *root.parents)),
            'UNSAFE_PATH', 'Release root must be an absolute non-symlink path')
    record = root/'last-release.json'
    require(record.is_file() and not record.is_symlink() and record.stat().st_size < 4096,
            'NOT_ROLLBACKABLE', 'No valid release receipt exists')
    receipt = obj(json.loads(record.read_text()), 'release receipt')
    previous = receipt.get('previous')
    require(isinstance(previous, str) and re.fullmatch(r'releases/[0-9a-f]{64}', previous) is not None,
            'NOT_ROLLBACKABLE', 'No preceding managed release exists')
    current = root/'current'
    require(current.is_symlink() and os.readlink(current) == receipt.get('current'),
            'TARGET_CHANGED', 'The current release no longer matches the last receipt')
    destination = root/previous
    require(destination.is_dir() and not destination.is_symlink() and (destination/'index.html').is_file(),
            'BAD_RELEASE', 'Previous release is missing')
    require(type(request.get('apply', False)) is bool, 'BAD_INPUT', 'apply must be a boolean')
    if not request.get('apply', False): return {'ok':True, 'applied':False, 'restore':previous}
    temp_link = root / ('.current-' + os.urandom(8).hex())
    os.symlink(previous, temp_link); os.replace(temp_link, current)
    atomic_write(record, json.dumps({'current':previous, 'previous':receipt['current'], 'sha256':previous.split('/')[1]}).encode())
    return {'ok':True, 'applied':True, 'current':previous, 'note':'Static files switched back; shared data was not changed. Verify HTTP.'}


def static_sync(request):
    """Pull a public GitHub release ZIP; no incoming webhook or GitHub token."""
    import re
    import subprocess
    repository = text(request.get('repository'), 'public GitHub repository', 200)
    tag = text(request.get('tag', 'site-deploy'), 'release tag', 100)
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) is not None
            and re.fullmatch(r'[A-Za-z0-9_.-]+', tag) is not None and '..' not in repository and '..' not in tag,
            'BAD_INPUT', 'Invalid repository or release tag')
    curl = shutil.which('curl'); require(curl is not None, 'DEPENDENCY', 'curl is required')
    base = f'https://github.com/{repository}/releases/download/{tag}/site.zip'
    with tempfile.TemporaryDirectory(prefix='1panel-static-sync-') as tmp:
        archive, manifest = Path(tmp)/'site.zip', Path(tmp)/'site.zip.sha256'
        for url, target, limit in ((base+'.sha256',manifest,4096),(base,archive,50*1024*1024)):
            result = subprocess.run([curl,'--fail','--silent','--show-error','--location','--proto','=https',
                                     '--proto-redir','=https','--max-time','60','--max-filesize',str(limit),
                                     '--output',str(target),'--url',url], capture_output=True, text=True, timeout=65)
            require(result.returncode == 0, 'DOWNLOAD_FAILED', 'Public release download failed; existing site was not changed')
        match = re.fullmatch(r'([0-9a-f]{64})\s+\*?site.zip\s*', manifest.read_text())
        require(match is not None, 'BAD_MANIFEST', 'Unexpected release checksum manifest')
        return static_release({'artifact':str(archive),'sha256':match.group(1),
                               'root':request.get('root'),'apply':request.get('apply',False)})
