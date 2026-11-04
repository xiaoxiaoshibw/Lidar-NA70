"""Local read-only review; annotated source-index selections download in the browser."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    p.add_argument('--port', type=int, default=8943)
    args = p.parse_args()
    out = Path(args.out).resolve()
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out), **kw)

        def do_GET(self):
            files = {'/':'review.html', '/review.html':'review.html', '/vendor.js':'vendor.js'}
            if self.path in files:
                source = HERE/'review.html' if files[self.path] == 'review.html' else Path('D:/Code/ldiar/pc_apps/human_replay/vendor/three.min.js')
                content = source.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8' if source.suffix == '.html' else 'text/javascript')
                self.send_header('Content-Length',str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                super().do_GET()
    print(f'http://127.0.0.1:{args.port}/', flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
