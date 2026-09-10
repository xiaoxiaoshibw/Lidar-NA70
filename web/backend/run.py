"""Start the local server and open the browser only after health is ready."""
import argparse
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')

    def open_when_ready():
        url = f'http://127.0.0.1:{args.port}'
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + '/api/health', timeout=.5) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                time.sleep(.2)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run('backend.app:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__': main()
