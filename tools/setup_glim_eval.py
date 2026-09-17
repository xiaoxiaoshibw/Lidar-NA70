"""Extract official GLIM CPU packages into a user prefix (WSL, no sudo)."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

root = Path.home() / 'na70-glim-eval'
root.mkdir(exist_ok=True)
index = (root / 'Packages').read_text()
packages = {}
wanted = {'libgtsam-notbb-dev', 'libgtsam-points-dev', 'libiridescence-dev',
          'ros-humble-glim', 'ros-humble-glim-ros'}
for block in index.split('\n\n'):
    fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
    if fields.get('Architecture') == 'amd64' and fields.get('Package') in wanted:
        packages[fields['Package']] = fields
for name, fields in packages.items():
    path = root / Path(fields['Filename']).name
    if not path.exists():
        print('download', path.name, flush=True)
        urllib.request.urlretrieve('https://koide3.github.io/ppa/ubuntu2204/' + fields['Filename'].removeprefix('./'), path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == fields['SHA256']
    subprocess.run(['dpkg-deb', '-x', str(path), str(root/'prefix')], check=True)
(root/'packages.json').write_text(json.dumps(packages,indent=2))
print(root/'prefix')
