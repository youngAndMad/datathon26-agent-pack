"""Read-only local service check; never prints keys, video contents or model output."""
import json
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

root = Path(__file__).resolve().parents[1]
key = (root / 'secrets/jury_api_key').read_text().strip()
client = build_opener(ProxyHandler({}))
checks_ok = True
for path, authenticated in [('/', False), ('/api/v1/jobs/00000000-0000-4000-8000-000000000000', False), ('/readyz', True)]:
    request = Request('http://127.0.0.1:8000' + path, headers={'X-API-Key': key} if authenticated else {})
    try:
        with client.open(request, timeout=5) as response:
            code, body = response.status, response.read()
    except HTTPError as exc:
        code, body = exc.code, exc.read()
    print(path, 'HTTP', code)
    checks_ok = checks_ok and code == (401 if path.startswith('/api/v1/jobs/') else 200)
    if path == '/readyz':
        print(json.dumps(json.loads(body), ensure_ascii=True))
raise SystemExit(0 if checks_ok else 1)
