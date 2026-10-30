# -*- coding: utf-8 -*-
"""动画素材补拉工具：误删 assets/anims 时重跑即可（需网络，从上游开源仓库拉取）。"""
import os, re, sys, urllib.parse, urllib.request, concurrent.futures

BASE = 'https://raw.githubusercontent.com/shenliuyu18/dafeiyu-pet/main/assets/anims/'
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, 'assets', 'anims')
os.makedirs(OUTDIR, exist_ok=True)

manifest = os.path.join(HERE, 'anims_index.js')
if not os.path.exists(manifest):
    print('缺少 anims_index.js（动作清单）')
    sys.exit(1)

text = open(manifest, encoding='utf-8').read()
names = re.findall(r'"([^"]+)"', text)
print(f'{len(names)} animations in manifest')

def fetch(name):
    url = BASE + urllib.parse.quote(name) + '.webm'
    dest = os.path.join(OUTDIR, name + '.webm')
    if os.path.exists(dest) and os.path.getsize(dest) > 10000:
        return name, 'skip'
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = urllib.request.urlopen(req, timeout=90).read()
            if len(data) < 1000:
                raise IOError(f'too small: {len(data)}B')
            open(dest, 'wb').write(data)
            return name, f'{len(data)//1024}KB'
        except Exception as e:
            if attempt == 3:
                return name, f'FAIL {e}'
    return name, 'FAIL'

ok, fail = 0, []
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
    for name, status in ex.map(fetch, names):
        if 'FAIL' in status:
            fail.append((name, status))
        else:
            ok += 1
        print(f'[{ok}] {name}: {status}', flush=True)

print(f'\nDONE ok={ok} fail={len(fail)}')
if fail:
    for n, s in fail:
        print('FAILED:', n, s)
    sys.exit(1)
