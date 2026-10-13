"""Shared validation and small private change records, not an authorization system."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid

VERSION = '1.2.1'
MAX_INPUT = 1024 * 1024

class OpsError(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details
    def result(self):
        return {'ok': False, 'code': self.code, 'message': self.message,
                **({'details': self.details} if self.details is not None else {})}

def require(condition, code, message):
    if not condition:
        raise OpsError(code, message)

def obj(value, label='input'):
    require(isinstance(value, dict), 'BAD_INPUT', f'{label} must be a JSON object')
    return value

def text(value, label, max_length=200_000, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= max_length,
            'BAD_INPUT', f'{label} must be a nonempty string up to {max_length} characters')
    require('\x00' not in value, 'BAD_INPUT', f'{label} contains a null character')
    return value

def boolean(value, label):
    require(type(value) is bool, 'BAD_INPUT', f'{label} must be a boolean')
    return value

def integer(value, label, low=1, high=2**31-1):
    require(type(value) is int and low <= value <= high, 'BAD_INPUT', f'{label} must be an integer in {low}..{high}')
    return value

def name(value, label='name'):
    text(value, label, 100)
    require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value) is not None and '..' not in value,
            'BAD_NAME', f'{label} must be an exact resource name, not a path or wildcard')
    return value

def digest(value):
    data = value.encode() if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()

_SECRET = re.compile(r'password|passwd|secret|token|api.?key|authorization|private.?key|credential|cookie', re.I)
def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if _SECRET.search(str(k)) or k.lower() in ('env', 'environment') else redact(v) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        secret = os.environ.get('ONEPANEL_API_KEY', '')
        if secret:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[PRIVATE KEY REDACTED]', value, flags=re.S)
        # Unstructured configuration/log strings cannot be safely partially redacted.
        if re.search(r'(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie)[\w-]*[\s\"\']*[=:]', value):
            return '[REDACTED SENSITIVE TEXT]'
        return re.sub(r'(https?|socks5h?)://[^\s/:@]+:[^\s/@]+@', r'\1://[REDACTED]@', value)
    return value

def records(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('items', 'records', 'data'):
            if isinstance(data.get(key), list):
                return data[key]
    raise OpsError('API_SHAPE', 'Expected a list response; no mutation was inferred from an unfamiliar response')

def content(data):
    if isinstance(data, str):
        return data
    if isinstance(data, dict) and isinstance(data.get('content'), str):
        return data['content']
    raise OpsError('API_SHAPE', 'The API did not return readable configuration content')

def secure_dir(path: Path):
    # Never repair permissions on an existing caller-selected directory.
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            'UNSAFE_PATH', 'Use a dedicated state directory owned by the current user with mode 0700; existing permissions are not changed')
    return path

def atomic_write(path: Path, data: bytes, mode=0o600):
    require(not path.is_symlink(), 'UNSAFE_PATH', 'Refusing to overwrite a symlink')
    fd, tmp = tempfile.mkstemp(prefix='.ops-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

class Store:
    def __init__(self, root=None):
        default = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))) / '1panel-ops'
        self.root = Path(root or os.environ.get('ONEPANEL_OPS_STATE_DIR', default)).expanduser()
    def path(self, operation_id):
        require(isinstance(operation_id, str) and re.fullmatch(r'[0-9a-f]{32}', operation_id), 'BAD_PLAN', 'Invalid plan ID')
        return self.root / (operation_id + '.json')
    def save(self, plan):
        secure_dir(self.root)
        atomic_write(self.path(plan['plan_id']), json.dumps(plan, ensure_ascii=False, indent=2).encode())
    def load(self, operation_id):
        p = self.path(operation_id)
        require(p.is_file() and not p.is_symlink(), 'PLAN_MISSING', 'Change record was not found')
        require(p.stat().st_size <= 8*MAX_INPUT, 'BAD_PLAN', 'Change record is too large')
        return obj(json.loads(p.read_text()), 'change record')
    def create(self, action, target, before, payload, instance, **extra):
        plan = dict(version=1, plan_id=uuid.uuid4().hex, action=action, target=target,
                    before=before, payload=payload, instance=instance, created_at=time.time(),
                    status='planned', **extra)
        plan['plan_digest'] = digest({k:v for k,v in plan.items() if k not in ('status', 'plan_digest')})
        self.save(plan)
        return self.summary(plan)
    def summary(self, plan):
        return {'ok': True, **{k:plan[k] for k in ('plan_id','plan_digest','action','target','status')},
                'before_digest': digest(plan['before']), 'candidate_digest': digest(plan['payload']),
                'warnings': plan.get('warnings', []), 'approval': 'task authorization; plan digest is NOT human authentication',
                **({'result': plan['result']} if 'result' in plan else {})}
    @contextlib.contextmanager
    def lock(self):
        import fcntl
        secure_dir(self.root)
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise OpsError('BUSY', 'Another operation is in progress; do not run concurrent writes')
            yield
        finally: os.close(fd)
