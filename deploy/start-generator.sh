#!/usr/bin/env bash
set -euo pipefail
cd /opt/fsp-lct2026
test -f .fsp-vm-marker
python3 -c 'import ipaddress,sys; value=sys.argv[1]; ip=ipaddress.ip_address(value); assert ip.version==4 and ip.is_global and value not in {"192.144.15.182","111.88.146.220"}' "${1:?Set the dedicated VM IPv4}"
python3 scripts/download_generator_gguf.py --cloud
sudo python3 - <<'PY'
import json,os,secrets
from pathlib import Path
root=Path('/opt/fsp-lct2026');p=root/'.env';original=p.stat()
lines=p.read_text().splitlines()
values=dict(line.split('=',1) for line in lines if '=' in line and not line.startswith('#'))
token=values.get('FSP_GENERATOR_TOKEN') or secrets.token_urlsafe(32)
lines=[line for line in lines if not line.startswith('FSP_GENERATOR_TOKEN=')]+['FSP_GENERATOR_TOKEN='+token]
temp=p.with_name('.env.generator-update');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream:stream.write(chr(10).join(lines)+chr(10))
os.chown(temp,original.st_uid,original.st_gid);os.replace(temp,p)
secret=root/'deploy/runtime/generator-token';secret.write_text(token+chr(10));os.chown(secret,10001,10001);secret.chmod(0o400)
print('Private generator credential prepared; API provider URL not activated')
PY
sudo docker compose --env-file .env -p fsp-lct2026 -f compose.yaml -f deploy/compose.https.yaml -f deploy/compose.generator.yaml --profile generation up -d --no-deps generator
echo 'Generator is private and optional. Activate the API only after a quality check.'
