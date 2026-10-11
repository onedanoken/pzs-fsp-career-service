"""Package application sources from explicit public roots, without private state."""
import argparse,hashlib,tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PUBLIC_TREES=('backend','frontend/src','frontend/public','frontend/scripts','scripts','deploy','tests','benchmarks','docs')
ROOT_FILES=('Dockerfile','.dockerignore','compose.yaml','requirements.txt','requirements-dev.txt','.env.example','README.md','LICENSE','THIRD_PARTY_NOTICES.md','setup.ps1','start.ps1','frontend/package.json','frontend/package-lock.json','frontend/index.html','frontend/tsconfig.json','frontend/tsconfig.app.json','frontend/tsconfig.node.json','frontend/vite.config.ts','data/model_lock.json','data/knowledge_manifest.json','data/assessment_content_v2.json','data/screening_core_operations.json','data/generation_model_lock.json','data/generation_gguf_lock.json')
PRIVATE_PARTS={'.git','.local','.codex','.agents','node_modules','.venv','__pycache__','runtime','uploads','outbox','.terraform'}
PRIVATE_SUFFIXES={'.pyc','.db','.sqlite','.sqlite3','.key','.pem','.pfx','.p12','.tfstate','.tfplan','.apk'}
def allowed(relative):
 p=Path(relative)
 return not p.is_absolute() and '..' not in p.parts and not any(x in PRIVATE_PARTS for x in p.parts) and p.name not in {'AGENTS.md','FSP_IMPLEMENTATION_PLAN.md'} and not p.name.startswith('.env') and p.suffix.lower() not in PRIVATE_SUFFIXES and 'ChatExport' not in p.name
def package(target,root=None):
 root=Path(root or ROOT).resolve();included=[]
 for name in PUBLIC_TREES:
  for path in (root/name).rglob('*'):
   if path.is_file() and allowed(path.relative_to(root)):
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('Source package may not follow external links')
    included.append(path)
 included += [root/name for name in ROOT_FILES if (root/name).is_file()]
 target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
 with tarfile.open(target,'w:gz') as archive:
  for path in sorted(set(included)):archive.add(path,arcname=path.relative_to(root).as_posix(),recursive=False)
 return {'archive':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'files':len(set(included)),'excludes':'Private working memory, cloud operator state, credentials, database, uploads, models, Git history and unrelated binaries'}
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/fsp-submission.tar.gz');args=parser.parse_args();print(package(args.output))
