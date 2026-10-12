"""Checks for common operator mistakes, not a sandbox or an IAM layer."""
from __future__ import annotations
import collections
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from .common import OpsError, obj, text, name, integer, require, boolean

try:
    import yaml
except ImportError:
    yaml = None

def load_yaml(source):
    text(source, 'configuration')
    require(yaml is not None, 'DEPENDENCY', 'PyYAML is required: install the documented dependencies explicitly')
    class Loader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            keys = set()
            for key, value in node.value:
                if key.tag == 'tag:yaml.org,2002:merge': continue
                k = self.construct_object(key, deep=deep)
                require(isinstance(k, (str,int,float,bool)), 'BAD_YAML', 'Complex YAML keys are not supported')
                require(k not in keys, 'BAD_YAML', 'Duplicate YAML mapping key')
                keys.add(k)
            self.flatten_mapping(node)
            return super().construct_mapping(node, deep=deep)
    try:
        aliases = sum(isinstance(e, yaml.events.AliasEvent) for e in yaml.parse(source))
        require(aliases <= 32, 'BAD_YAML', 'Too many YAML aliases')
        value = yaml.load(source, Loader=Loader)
    except OpsError:
        raise
    except Exception as e:
        raise OpsError('BAD_YAML', 'Configuration is not valid safe YAML') from e
    stack, count = [(value, frozenset(), 0)], 0
    while stack:
        v, parents, depth = stack.pop(); count += 1
        require(count <= 20_000 and depth <= 50, 'BAD_YAML', 'Configuration is too deeply nested or expands too far')
        if isinstance(v, (dict,list)):
            require(id(v) not in parents, 'BAD_YAML', 'Cyclic YAML aliases are not supported')
            next_parents = parents | {id(v)}
            stack.extend((x,next_parents,depth+1) for x in (v.values() if isinstance(v,dict) else v))
        else:
            require(v is None or isinstance(v, (str,int,float,bool)), 'BAD_YAML', 'Only JSON-compatible YAML values are supported')
    return obj(value, 'configuration')

def safe_relative(value):
    text(value, 'relative path', 500)
    p = Path(value)
    require(not p.is_absolute() and '..' not in p.parts and '\\' not in value and not value.startswith('~'),
            'UNSAFE_PATH', 'Expected a relative path inside the project')
    return p

def check_bind(source, allowed_roots):
    text(source, 'bind source', 1000)
    require('$' not in source, 'UNRESOLVED_ENV', 'Bind paths must be resolved before use')
    if not source.startswith('/'):
        safe_relative(source)
        return
    p = Path(source).resolve()
    critical = [Path(x) for x in ('/etc','/root','/proc','/sys','/dev','/run','/var/run','/var/lib/docker','/opt/1panel/db')]
    require(p != Path('/') and not any(p == x or x in p.parents for x in critical),
            'UNSAFE_MOUNT', 'This bind mount exposes a critical host path')
    roots = [Path(x).resolve() for x in allowed_roots]
    require(any(p == root or root in p.parents for root in roots), 'UNSAFE_MOUNT',
            'Absolute bind mounts require a project-specific bind_roots entry; prefer ./data or a named volume')

def port_bindings(config):
    bindings=[]
    for service, values in obj(config.get('services'), 'services').items():
        for port in values.get('ports', []):
            host_ip, protocol, published = '0.0.0.0', 'tcp', None
            if isinstance(port, dict):
                protocol = port.get('protocol','tcp'); host_ip = port.get('host_ip','0.0.0.0')
                published=port.get('published')
            elif isinstance(port, (int,str)):
                spec=str(port)
                if '/' in spec: spec,protocol=spec.rsplit('/',1)
                parts=spec.rsplit(':',2)
                if len(parts)==2: published=parts[0]
                elif len(parts)==3: host_ip,published,_=parts
            else: raise OpsError('BAD_PORT','Unsupported port mapping')
            if published is None: continue
            require(str(published).isdigit(), 'BAD_PORT','Use an explicit single published port, not a range')
            published=integer(int(published),'published port',1,65535)
            host_ip=str(host_ip).strip('[]')
            try: ipaddress.ip_address(host_ip)
            except ValueError as e: raise OpsError('BAD_PORT','host_ip must be a literal IPv4 or IPv6 address') from e
            require(protocol in ('tcp','udp'), 'BAD_PORT','Only TCP and UDP port checks are supported')
            bindings.append({'service':service,'ip':host_ip,'port':published,'protocol':protocol})
    for i,a in enumerate(bindings):
        require(not any(a['port']==b['port'] and a['protocol']==b['protocol'] and
                        (a['ip']==b['ip'] or a['ip'] in ('0.0.0.0','::') or b['ip'] in ('0.0.0.0','::'))
                        for b in bindings[:i]), 'PORT_CONFLICT','Two services publish the same host port')
    return bindings

def lint_compose(config, project, bind_roots=(), allow_host_network=False):
    name(project, 'project name')
    require(project.lower()==project, 'BAD_NAME', 'Compose project names must be lowercase')
    obj(config, 'compose')
    require(config.get('name',project)==project, 'TARGET_MISMATCH','Top-level Compose name differs from the selected project')
    require('include' not in config, 'UNSUPPORTED','Compose include is not allowed in a self-contained deployment')
    services=obj(config.get('services'), 'services')
    require(0 < len(services) <= 32, 'BAD_COMPOSE','Expected 1..32 services')
    warnings=[]
    for key, values in services.items():
        name(key,'service name'); obj(values, key)
        require(bool(values.get('image')), 'BAD_COMPOSE','Every service needs an explicit image; host builds are not part of this script')
        for forbidden in ('build','extends','env_file','devices','device_cgroup_rules','use_api_socket','post_start','pre_stop','develop','volumes_from'):
            require(forbidden not in values, 'UNSUPPORTED', f'{key}: {forbidden} is not allowed in this deployment path')
        require(not values.get('privileged'), 'UNSAFE_COMPOSE', f'{key}: privileged mode is not supported')
        require(not values.get('cap_add'), 'UNSAFE_COMPOSE', f'{key}: added capabilities need a separate explicitly reviewed deployment')
        require(values.get('pid') != 'host' and values.get('ipc') != 'host', 'UNSAFE_COMPOSE','Host PID/IPC sharing is not supported')
        require(not any('unconfined' in str(s) for s in values.get('security_opt', [])), 'UNSAFE_COMPOSE','Unconfined security settings are not supported')
        mode=values.get('network_mode','')
        if mode=='host':
            require(allow_host_network, 'NEEDS_CONFIRMATION','Host networking must be explicitly requested for this project')
            warnings.append(f'{key}: host network was explicitly selected; published-port checks do not cover ports opened by the process')
        elif mode:
            require(mode.startswith('service:') and mode[8:] in services, 'UNSAFE_COMPOSE','Only an in-project service namespace can be shared')
        if 'container_name' in values: name(values['container_name'],'container_name')
        mounts=values.get('volumes',[])
        require(isinstance(mounts,list), 'BAD_COMPOSE','Service volumes must be a list')
        for mount in mounts:
            source=None
            if isinstance(mount,str):
                parts=mount.split(':'); source=parts[0] if len(parts)>1 else None
                if source and (source.startswith(('/','.','~')) or '/' in source): check_bind(source,bind_roots)
            elif isinstance(mount,dict):
                if mount.get('type')=='bind': check_bind(mount.get('source'),bind_roots)
                elif mount.get('type','volume') not in ('volume','tmpfs'): raise OpsError('UNSAFE_MOUNT','Unsupported mount type')
            else: raise OpsError('BAD_COMPOSE','Invalid volume mapping')
    for key,value in obj(config.get('volumes',{}),'volumes').items():
        if value is None: continue
        obj(value,'volume')
        require(value.get('name', f'{project}_{key}') == f'{project}_{key}', 'UNSAFE_MOUNT', 'Custom volume names can accidentally reuse another project; use project-scoped names')
        require(not value.get('external') and not value.get('driver_opts') and value.get('driver','local')=='local',
                'UNSAFE_MOUNT','External volumes and custom volume drivers/options are not enabled')
    for kind in ('configs','secrets'):
        for value in obj(config.get(kind,{}),kind).values():
            require(isinstance(value,dict) and 'file' not in value and 'environment' not in value and not value.get('external'),
                    'UNSAFE_MOUNT',f'{kind} must not load host files or pre-existing resources')
    return {'services':list(services),'ports':port_bindings(config),'warnings':warnings}

def validate_compose(source, project, env='', bind_roots=(), allow_host_network=False):
    raw=load_yaml(source)
    text(env,'env',200_000,empty=True)
    require(re.search(r'(?m)^\s*(?:export\s+)?(?:COMPOSE_|DOCKER_)[A-Za-z0-9_]*\s*=', env) is None,
            'RESERVED_ENV', 'Deployment env must not override Compose/Docker control variables')
    provisional=json.loads(json.dumps(raw))
    for values in provisional.get('services',{}).values():
        if isinstance(values,dict):
            if isinstance(values.get('image'),str): values['image']=values['image'].replace('$','_')
            values['volumes']=[v for v in values.get('volumes',[]) if '$' not in str(v)]
            values['ports']=[v for v in values.get('ports',[]) if '$' not in str(v)]
    lint_compose(provisional,project,bind_roots,allow_host_network)
    docker=shutil.which('docker')
    require(docker is not None, 'DEPENDENCY','Docker Compose CLI is required for a real configuration check; no daemon write permission is needed')
    with tempfile.TemporaryDirectory(prefix='1panel-compose-check-') as tmp:
        root=Path(tmp); (root/'compose.yaml').write_text(source); (root/'candidate.env').write_text(env)
        result=subprocess.run([docker,'compose','--project-name',project,'--env-file',str(root/'candidate.env'),
                               '-f',str(root/'compose.yaml'),'config','--format','json','--no-path-resolution'],
                              capture_output=True,text=True,timeout=30,cwd=root,
                              env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':tmp,'DOCKER_CONFIG':tmp,'COMPOSE_ANSI':'never'})
    require(result.returncode==0, 'COMPOSE_INVALID','Docker Compose rejected the candidate; inspect it locally (stderr is withheld because it may contain secrets)')
    try: normalized=json.loads(result.stdout)
    except ValueError as e: raise OpsError('COMPOSE_INVALID','Docker Compose did not return JSON') from e
    report=lint_compose(normalized,project,bind_roots,allow_host_network)
    require('not set' not in result.stderr.lower(), 'UNRESOLVED_ENV','Compose reported an unset variable; supply its value explicitly')
    return report

def occupied_ports():
    if not shutil.which('ss'): raise OpsError('DEPENDENCY','ss is required to check host listening ports')
    proc=subprocess.run(['ss','-H','-lntu'],capture_output=True,text=True,timeout=10)
    require(proc.returncode==0, 'PORT_CHECK','Unable to inspect host listening ports')
    result=[]
    for row in proc.stdout.splitlines():
        fields=row.split()
        if len(fields)<5: continue
        address=fields[4]
        try: host,port=address.rsplit(':',1); port=int(port)
        except ValueError: continue
        result.append((fields[0],host.strip('[]'),port))
    return result

def check_ports(bindings, occupied, previous=()):
    old={(p['protocol'],p['ip'],p['port']) for p in previous}
    for p in bindings:
        if (p['protocol'],p['ip'],p['port']) in old: continue
        for protocol,host,port in occupied:
            if protocol==p['protocol'] and port==p['port'] and (host==p['ip'] or host in ('*','0.0.0.0','::') or p['ip'] in ('0.0.0.0','::')):
                raise OpsError('PORT_CONFLICT',f'Host port {port}/{protocol} is already listening')

def nginx_directives(source):
    text(source,'Nginx content')
    lexer=shlex.shlex(source,posix=True,punctuation_chars='{};')
    lexer.whitespace_split=True; lexer.commenters='#'
    depth=0; current=[]; directives=[]
    try: tokens=list(lexer)
    except ValueError as e: raise OpsError('NGINX_INVALID','Unclosed quoted string') from e
    for token in tokens:
        if token and all(c in '{};' for c in token):
            for c in token:
                if c in ';{':
                    if current: directives.append(tuple(current)); current=[]
                    if c=='{': depth+=1
                elif c=='}':
                    require(not current,'NGINX_INVALID','Missing semicolon before closing block')
                    depth-=1; require(depth>=0,'NGINX_INVALID','Unexpected closing brace')
        else: current.append(token)
    require(depth==0 and not current, 'NGINX_INVALID','Unbalanced blocks or missing trailing semicolon')
    return directives

def lint_nginx(candidate, previous=None):
    new=nginx_directives(candidate)
    require(bool(new),'NGINX_INVALID','Empty Nginx configuration')
    old=nginx_directives(previous) if previous is not None else []
    old_counter=collections.Counter(old)
    for item in new:
        key=item[0].lower()
        if ('lua' in key or key.startswith('perl') or key in ('load_module','env')) and old_counter[item]==0:
            raise OpsError('UNSAFE_NGINX','Adding executable modules, Lua/Perl or environment directives requires separate manual review')
    if previous is not None:
        critical={'user','pid','load_module','include','root','alias','ssl_certificate','ssl_certificate_key','server_name','listen',
                  'access_log','error_log','client_body_temp_path','proxy_temp_path','proxy_cache_path'}
        require(collections.Counter(x for x in new if x[0] in critical)==collections.Counter(x for x in old if x[0] in critical),
                'NEEDS_CONFIRMATION','This edit changes site identity, listeners, file mappings, includes or certificate paths; use the dedicated site/HTTPS action instead')
    return {'structural_check':True,'nginx_syntax_check':False,
            'note':'1Panel performs nginx -t and reload when saving. This preview is not a full nginx -t.'}

def http_url(value):
    text(value,'URL',2000)
    require(re.search(r'[\s{};]', value) is None, 'BAD_URL', 'URL contains whitespace or unescaped directive delimiters')
    p=urlsplit(value)
    require(p.scheme in ('http','https') and p.hostname and not p.username and not p.password and not p.fragment,
            'BAD_URL','Expected an HTTP(S) URL without embedded credentials')
    try: p.port
    except ValueError as e: raise OpsError('BAD_URL','Invalid URL port') from e
    return p

def cron_spec(value):
    text(value,'schedule',100)
    parts=value.split()
    require(len(parts) in (5,6) and all(re.fullmatch(r'[0-9*/,?\-]+',x) for x in parts),
            'BAD_CRON','Use a numeric 5- or 6-field cron expression')
    return value
