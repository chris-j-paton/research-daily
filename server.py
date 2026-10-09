"""
Research Daily - local server.

Serves the dashboard at http://127.0.0.1:8765 and runs fetch.py when the
page's Refresh button is clicked.  Standard library only.

    python server.py          start the server (or, if it is already running,
                              just open the dashboard in the browser)
"""
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import datetime as dt
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", 8765
URL = "http://%s:%d/" % (HOST, PORT)

state = {"running": False, "log": [], "exit": None, "started": None, "finished": None}
lock = threading.Lock()


def run_fetch():
    with lock:
        if state["running"]:
            return False
        state.update(running=True, log=[], exit=None, started=time.time(), finished=None)
    proc = subprocess.Popen(
        [sys.executable, "-u", os.path.join(HERE, "fetch.py")],
        cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    for line in proc.stdout:
        with lock:
            state["log"].append(line.rstrip())
            if len(state["log"]) > 400:
                del state["log"][:-400]
    proc.wait()
    with lock:
        state.update(running=False, exit=proc.returncode, finished=time.time())
    return True


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        return  # keep the console quiet; fetch progress is shown in the page

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            with lock:
                self.send_json({
                    "server": True,
                    "running": state["running"],
                    "log": state["log"][-60:],
                    "exit": state["exit"],
                    "started": state["started"],
                    "finished": state["finished"],
                })
            return
        # never let the browser cache the data file
        if path.startswith("/data/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript" if path.endswith(".js") else "application/json")
            self.send_header("Cache-Control", "no-store")
            try:
                with open(os.path.join(HERE, path.lstrip("/")), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/refresh":
            with lock:
                already = state["running"]
            if not already:
                threading.Thread(target=run_fetch, daemon=True).start()
            self.send_json({"started": not already, "running": True})
            return
        self.send_error(404)


def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, PORT)) == 0


def main():
    if port_in_use():
        print("Research Daily is already running at %s - opening it." % URL)
        webbrowser.open(URL)
        return
    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print("Research Daily running at %s  (close this window to stop)" % URL)
    threading.Timer(0.6, lambda: webbrowser.open(URL)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
