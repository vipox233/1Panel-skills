"""Package complete upstream checkout plus ops; exclude local state/secrets."""
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]
include_files=['SKILL.md','README.md','README.zh-CN.md','AGENTS.md','LICENSE','package.json',
               'package-lock.json','tsconfig.json','tsconfig.build.json','run.py','requirements-ops.txt']
include_dirs=['agents','scripts','dist','references','docs','examples','tests']
paths=[ROOT/p for p in include_files if (ROOT/p).is_file()]
for d in include_dirs:
    paths.extend(p for p in (ROOT/d).rglob('*') if p.is_file())
paths=[p for p in paths if not any(x.startswith('.') or x in ('node_modules','__pycache__') for x in p.relative_to(ROOT).parts)
       and p.suffix not in ('.pyc','.log') and not p.is_symlink()]
assert (ROOT/'dist/scripts/client.js') in paths, 'Missing original built client'
assert (ROOT/'dist/scripts/index.js') in paths, 'Missing original module registry'
assert len(list((ROOT/'scripts/modules').glob('*.ts')))>=8, 'Original modules missing'
assert 'version: 1.2.1' in (ROOT/'SKILL.md').read_text(), 'Missing AgentDock package version'
with zipfile.ZipFile(ROOT/'skill.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for p in sorted(set(paths)):
        archive.write(p, '1panel-skills/'+str(p.relative_to(ROOT)))
assert (ROOT/'skill.zip').stat().st_size < 25*1024*1024
print('skill.zip: complete original Skill plus operations extension; no local environment files')
