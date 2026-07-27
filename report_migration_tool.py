"""
report_migration_tool.py  —  Unified Report Migration Server (port 8500)
Routes between Tableau (.twb/.twbx) and Crystal Reports (.rpt) engines.

Usage:
    python report_migration_tool.py [--port 8500]
    python report_migration_tool.py [--port 8500] [--crystal-assembly-path "C:\\path\\to\\dlls"]

Both tableau_pbi_server.py and crystal_pbi_generator.py must be in the same directory.
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ─── Locate sibling scripts ───────────────────────────────────────────────────
_HERE = Path(__file__).parent.resolve()
LOGIN_FILE = _HERE / "Landing.html"
APP_FILE = _HERE / "Index.html"
sys.path.insert(0, str(_HERE))

# ─── Session management for login auth ────────────────────────────────────────
_VALID_CREDENTIALS = {"admin": "admin123"}  # username: password
_sessions = {}  # session_id -> {'authenticated': True, 'timestamp': ...}
import uuid

# ─── Parse CLI args early so we can set env vars before importing Crystal ────
_pre_parser = argparse.ArgumentParser(add_help=False)
_pre_parser.add_argument("--crystal-assembly-path", default="")
_pre_args, _ = _pre_parser.parse_known_args()
if _pre_args.crystal_assembly_path:
    os.environ["CRYSTAL_ASSEMBLY_PATH"] = _pre_args.crystal_assembly_path
    print(f"[tool] CRYSTAL_ASSEMBLY_PATH set to: {_pre_args.crystal_assembly_path}")

# ─── Load Tableau engine ──────────────────────────────────────────────────────
try:
    import tableau_pbi_server as _tbl
    _TABLEAU_OK = True
    print("[tool] ✓ Tableau engine loaded")
except Exception as _e:
    _TABLEAU_OK = False
    print(f"[tool] ✗ Tableau engine failed to load: {_e}")

# ─── Load Crystal engine ──────────────────────────────────────────────────────
try:
    import crystal_pbi_generator as _cry
    try:
        from werkzeug.test import Client as _WerkzeugClient
        _crystal_client = _WerkzeugClient(_cry.app)
        _CRYSTAL_OK = True
        print("[tool] ✓ Crystal engine loaded")
    except Exception as _we:
        _CRYSTAL_OK = False
        print(f"[tool] ✗ Crystal werkzeug bridge failed: {_we}")
except Exception as _e:
    _CRYSTAL_OK = False
    print(f"[tool] ✗ Crystal engine failed to load: {_e}")

# ─── Unified SSE broadcast ────────────────────────────────────────────────────
_clients: list = []
_clients_lock = threading.Lock()


def _broadcast(event: str, data: dict):
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)


# Monkey-patch Tableau's broadcast so Tableau events reach our unified clients
if _TABLEAU_OK:
    _orig_tableau_broadcast = _tbl.broadcast

    def _unified_tableau_broadcast(event: str, data: dict):
        _orig_tableau_broadcast(event, data)
        _broadcast(event, data)

    _tbl.broadcast = _unified_tableau_broadcast


# ─── Crystal SSE relay ────────────────────────────────────────────────────────
def _crystal_sse_relay():
    if not _CRYSTAL_OK:
        return
    seen = 0
    while True:
        try:
            with _cry._sse_lock:
                new_msgs = _cry._sse_messages[seen:]
                seen = len(_cry._sse_messages)
            for msg in new_msgs:
                level = ("error" if "ERROR" in msg.upper()
                         else "warn" if "WARN" in msg.upper() else "info")
                _broadcast("log", {
                    "ts": time.strftime("%H:%M:%S"),
                    "level": level,
                    "file": "[Crystal]",
                    "message": msg,
                })
        except Exception:
            pass
        time.sleep(0.3)


if _CRYSTAL_OK:
    threading.Thread(target=_crystal_sse_relay, daemon=True).start()


# ─── Crystal WSGI proxy ───────────────────────────────────────────────────────
def _crystal_proxy(method: str, path: str, body: bytes, content_type: str):
    if not _CRYSTAL_OK:
        return 503, {}, b'{"status":"error","message":"Crystal engine not loaded"}'
    try:
        if method == "GET":
            resp = _crystal_client.get(path)
        elif method == "POST":
            resp = _crystal_client.post(path, data=body,
                                        content_type=content_type or "application/json")
        else:
            return 405, {}, b'{"error":"Method not allowed"}'
        return resp.status_code, dict(resp.headers), resp.data
    except Exception as exc:
        return 500, {}, json.dumps({"status": "error", "message": str(exc)}).encode()


# ─── HTTP Handler ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_login(self):
        """Serve the login page (Landing.html)"""
        if LOGIN_FILE.exists():
            body = LOGIN_FILE.read_bytes()
        else:
            body = b'<h1>Login page not found</h1><p>Place Landing.html next to the server.</p>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _serve_app(self):
        """Serve the main app (Index.html) - auth required"""
        cookies = self.headers.get('Cookie', '')
        session_id = None
        for cookie in cookies.split(';'):
            cookie = cookie.strip()
            if cookie.startswith('session_id='):
                session_id = cookie.split('=', 1)[1]
                break
        
        if not session_id or session_id not in _sessions:
            self.send_response(302)
            self.send_header('Location', '/')
            self._cors()
            self.end_headers()
            return
        
        if APP_FILE.exists():
            body = APP_FILE.read_bytes()
        else:
            body = b'<h1>App file not found</h1><p>Place Index.html next to the server.</p>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _check_session(self):
        """Check if user has a valid session"""
        cookies = self.headers.get('Cookie', '')
        session_id = None
        for cookie in cookies.split(';'):
            cookie = cookie.strip()
            if cookie.startswith('session_id='):
                session_id = cookie.split('=', 1)[1]
                break
        
        if session_id and session_id in _sessions:
            self._json({'authenticated': True})
        else:
            self._json({'authenticated': False})

    def _handle_login(self, payload):
        """Handle login POST request"""
        username = payload.get('username', '').strip()
        password = payload.get('password', '').strip()
        
        if username not in _VALID_CREDENTIALS or _VALID_CREDENTIALS[username] != password:
            self._json({'ok': False, 'error': 'Invalid username or password'})
            return
        
        session_id = str(uuid.uuid4())
        _sessions[session_id] = {'authenticated': True, 'username': username, 'timestamp': time.time()}
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Set-Cookie', f'session_id={session_id}; Path=/; HttpOnly')
        self._cors()
        response = json.dumps({'ok': True, 'redirect': '/app'}).encode('utf-8')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _serve_static(self, path):
        """Serve static files (images, SVGs, etc.)"""
        clean_path = path.lstrip('/')
        if '..' in clean_path:
            self.send_error(403)
            return
        
        file_path = _HERE / clean_path
        
        if not file_path.exists() or file_path.is_dir():
            self.send_error(404)
            return
        
        try:
            body = file_path.read_bytes()
            ext = file_path.suffix.lower()
            content_types = {
                '.svg': 'image/svg+xml',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.css': 'text/css',
                '.js': 'application/javascript',
                '.json': 'application/json',
            }
            content_type = content_types.get(ext, 'application/octet-stream')
            
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/"):
            self._serve_login()
        elif path == "/app":
            self._serve_app()
        elif path == "/api/session":
            self._check_session()
        elif path == "/events":
            self._serve_sse()
        elif path == "/state":
            self._json(_tbl.conversion_state if _TABLEAU_OK else {})
        elif path == "/engines":
            # Let the UI know which engines loaded successfully
            self._json({
                "tableau": _TABLEAU_OK,
                "crystal": _CRYSTAL_OK,
                "crystal_sdk": _cry.CRYSTAL_SDK_AVAILABLE if _CRYSTAL_OK else False,
                "crystal_sdk_mode": _cry.CRYSTAL_SDK_MODE if _CRYSTAL_OK else None,
                "crystal_assembly_path": os.environ.get("CRYSTAL_ASSEMBLY_PATH", ""),
            })
        elif path in ("/crystal/status", "/crystal/seed_status", "/crystal/diagnose"):
            crystal_path = path[len("/crystal"):]
            sc, hdrs, body = _crystal_proxy("GET", crystal_path, b"", "")
            self._raw(sc, hdrs, body)
        else:
            # Try to serve static files
            self._serve_static(path)

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b"{}"
        content_type = self.headers.get("Content-Type", "application/json")
        try:
            payload = json.loads(raw_body)
        except Exception:
            payload = {}

        # ── Auth routes ──────────────────────────────────────────────────────
        if path == "/api/login":
            self._handle_login(payload)
        # ── Tableau routes ────────────────────────────────────────────────────
        elif path == "/start":
            self._tableau_start(payload)
        elif path == "/stop":
            self._tableau_stop()
        elif path == "/convert_one":
            self._tableau_convert_one(payload)
        elif path == "/clear":
            if _TABLEAU_OK:
                _tbl.conversion_state["jobs"].clear()
                _tbl.broadcast("state", _tbl.conversion_state)
            self._json({"ok": True})
        elif path == "/convert_to_pbix":
            self._handle_convert_to_pbix(payload)

        # ── Crystal routes ────────────────────────────────────────────────────
        elif path == "/crystal/start":
            self._crystal_start(payload)
        elif path == "/crystal/set_assembly_path":
            self._set_crystal_assembly_path(payload)
        elif path.startswith("/crystal/"):
            crystal_path = path[len("/crystal"):]
            sc, hdrs, body = _crystal_proxy("POST", crystal_path, raw_body, content_type)
            self._raw(sc, hdrs, body)
        else:
            self.send_error(404)

    # ── Tableau delegates ─────────────────────────────────────────────────────
    def _tableau_start(self, payload):
        if not _TABLEAU_OK:
            self._json({"ok": False, "error": "Tableau engine not loaded"}); return
        if _tbl.conversion_state["running"]:
            self._json({"ok": False, "error": "Already running"}); return
        src = payload.get("source", "").strip()
        tgt = payload.get("target", "").strip()
        if not src or not tgt:
            self._json({"ok": False, "error": "source and target are required"}); return
        if not Path(src).is_dir():
            self._json({"ok": False, "error": f"Source folder not found: {src}"}); return
        sample = bool(payload.get("embed_sample_data", False))
        mode = payload.get("mode", "migrate")
        pbix_params = _tbl._extract_pbix_params(payload)
        _tbl.conversion_state.update(running=True, source=src, target=tgt, jobs={})
        threading.Thread(target=_tbl.conversion_worker,
                         args=(src, tgt, sample, mode, pbix_params), daemon=True).start()
        self._json({"ok": True})

    def _tableau_stop(self):
        if _TABLEAU_OK:
            _tbl.conversion_state["running"] = False
        self._json({"ok": True})

    def _tableau_convert_one(self, payload):
        if not _TABLEAU_OK:
            self._json({"ok": False, "error": "Tableau engine not loaded"}); return
        filepath = payload.get("file", "").strip()
        target = (payload.get("target", "") or "").strip() or \
                 _tbl.conversion_state.get("target", "").strip()
        if not filepath:
            self._json({"ok": False, "error": "No file path provided"}); return
        if not target:
            self._json({"ok": False, "error": "No target folder set."}); return
        p = Path(filepath)
        if not p.exists():
            self._json({"ok": False, "error": f"File not found: {filepath}"}); return
        if p.suffix.lower() not in ('.twb', '.twbx'):
            self._json({"ok": False, "error": f"Not a Tableau file: {p.name}"}); return
        sample = bool(payload.get("embed_sample_data", False))
        mode = payload.get("mode", "migrate")
        pbix_params = _tbl._extract_pbix_params(payload)
        threading.Thread(target=_tbl.process_file,
                         args=(p, Path(target), sample, mode, pbix_params), daemon=True).start()
        self._json({"ok": True, "queued": p.name})

    # ── Crystal delegates ─────────────────────────────────────────────────────
    def _crystal_start(self, payload):
        """
        Run Crystal conversion, then optionally trigger PBIX conversion on
        every .pbit produced in the output folder.

        Analysis/documentation .xlsx files are always written to
        output/analysis/ (matching Tableau engine behaviour).
        Converted .pbit / .rdl files are written to output/.
        """
        if not _CRYSTAL_OK:
            self._json({"ok": False, "error": "Crystal engine not loaded"}); return

        inp          = payload.get("input_folder", "").strip()
        out          = payload.get("output_folder", "").strip()
        crystal_mode = payload.get("crystal_mode", "both")
        out_format   = payload.get("output_format", "").lower().strip()
        seed_pbit    = payload.get("seed_pbit", "").strip()
        pbix_params  = _tbl._extract_pbix_params(payload) if _TABLEAU_OK else {}

        if not inp or not out:
            self._json({"ok": False, "error": "input_folder and output_folder are required"}); return

        # Analysis documents always go into output/analysis/ — same as Tableau
        analysis_dir = str(Path(out) / "analysis")

        def _make_body(mode_override, output_folder_override):
            return json.dumps({
                "input_folder":  inp,
                "output_folder": output_folder_override,
                "output_format": out_format,
                "seed_pbit":     seed_pbit,
            }).encode()

        def _worker():
            _broadcast("log", {"ts": time.strftime("%H:%M:%S"), "level": "info",
                                "file": "[Crystal]",
                                "message": f"Starting Crystal migration — mode: {crystal_mode}"})

            files_done = 0

            if crystal_mode == "both":
                # ── Step 1: document only → analysis subfolder ────────────────
                Path(analysis_dir).mkdir(parents=True, exist_ok=True)
                _broadcast("log", {"ts": time.strftime("%H:%M:%S"), "level": "info",
                                    "file": "[Crystal]",
                                    "message": f"Documenting reports → analysis/"})
                sc1, _, rb1 = _crystal_proxy(
                    "POST", "/document", _make_body("document", analysis_dir), "application/json")
                try:
                    r1 = json.loads(rb1)
                    files_done = r1.get("files_processed", 0)
                except Exception:
                    pass

                # ── Step 2: convert only → output folder ──────────────────────
                _broadcast("log", {"ts": time.strftime("%H:%M:%S"), "level": "info",
                                    "file": "[Crystal]",
                                    "message": f"Converting reports → output/"})
                sc2, _, rb2 = _crystal_proxy(
                    "POST", "/convert", _make_body("convert", out), "application/json")

            elif crystal_mode == "document":
                # Document only → analysis subfolder
                Path(analysis_dir).mkdir(parents=True, exist_ok=True)
                _broadcast("log", {"ts": time.strftime("%H:%M:%S"), "level": "info",
                                    "file": "[Crystal]",
                                    "message": f"Documenting reports → analysis/"})
                sc1, _, rb1 = _crystal_proxy(
                    "POST", "/document", _make_body("document", analysis_dir), "application/json")
                try:
                    files_done = json.loads(rb1).get("files_processed", 0)
                except Exception:
                    pass

            else:
                # convert only → output folder (no analysis subfolder needed)
                sc1, _, rb1 = _crystal_proxy(
                    "POST", "/convert", _make_body("convert", out), "application/json")
                try:
                    files_done = json.loads(rb1).get("files_processed", 0)
                except Exception:
                    pass

            _broadcast("log", {"ts": time.strftime("%H:%M:%S"), "level": "info",
                                "file": "[Crystal]",
                                "message": f"✓ Crystal done — {files_done} file(s) processed → {out}"})

            # ── Optional PBIX conversion on produced .pbit files ──────────────
            if pbix_params and _TABLEAU_OK and Path(out).is_dir():
                pbit_files = list(Path(out).glob("*.pbit"))
                if pbit_files:
                    _broadcast("log", {"ts": time.strftime("%H:%M:%S"), "level": "info",
                                       "file": "[Crystal→PBIX]",
                                       "message": f"Starting PBIX conversion for {len(pbit_files)} .pbit file(s)…"})
                    for pf in pbit_files:
                        method = pbix_params.get("method", "desktop")
                        if method == "desktop":
                            t = threading.Thread(
                                target=_tbl._pbix_via_desktop,
                                args=(pf, Path(out),
                                      pbix_params.get("pbi_exe", ""),
                                      pbix_params.get("timeout", 90)),
                                daemon=True)
                        elif method == "api":
                            t = threading.Thread(
                                target=_tbl._pbix_via_api,
                                args=(pf, Path(out),
                                      pbix_params.get("tenant_id", ""),
                                      pbix_params.get("client_id", ""),
                                      pbix_params.get("client_secret", ""),
                                      pbix_params.get("workspace_id", "")),
                                daemon=True)
                        else:
                            continue
                        t.start()

        threading.Thread(target=_worker, daemon=True).start()
        self._json({"ok": True})

    def _set_crystal_assembly_path(self, payload):
        """Allow the UI to update CRYSTAL_ASSEMBLY_PATH at runtime."""
        path = payload.get("path", "").strip()
        if path:
            os.environ["CRYSTAL_ASSEMBLY_PATH"] = path
            self._json({"ok": True, "path": path})
        else:
            os.environ.pop("CRYSTAL_ASSEMBLY_PATH", None)
            self._json({"ok": True, "path": ""})

    # ── Shared PBIX conversion endpoint ───────────────────────────────────────
    def _handle_convert_to_pbix(self, payload):
        if not _TABLEAU_OK:
            self._json({"ok": False, "error": "PBIX conversion requires Tableau engine"}); return
        pbit_path  = payload.get("pbit_path", "").strip()
        output_dir = payload.get("output_dir", "").strip()
        method     = payload.get("method", "desktop")
        if not pbit_path:
            self._json({"ok": False, "error": "No PBIT file path provided"}); return
        if not output_dir:
            self._json({"ok": False, "error": "No output folder provided"}); return
        p = Path(pbit_path)
        if not p.exists():
            self._json({"ok": False, "error": f"File not found: {pbit_path}"}); return
        if p.suffix.lower() != '.pbit':
            self._json({"ok": False, "error": f"Not a .pbit file: {p.name}"}); return
        if method == 'desktop':
            threading.Thread(
                target=_tbl._pbix_via_desktop,
                args=(p, Path(output_dir), payload.get("pbi_exe","").strip(),
                      int(payload.get("timeout", 90))), daemon=True).start()
        elif method == 'api':
            t_id = payload.get("tenant_id","").strip()
            c_id = payload.get("client_id","").strip()
            c_sec = payload.get("client_secret","").strip()
            w_id  = payload.get("workspace_id","").strip()
            if not all([t_id, c_id, c_sec, w_id]):
                self._json({"ok": False, "error": "All API credentials required"}); return
            threading.Thread(
                target=_tbl._pbix_via_api,
                args=(p, Path(output_dir), t_id, c_id, c_sec, w_id), daemon=True).start()
        else:
            self._json({"ok": False, "error": f"Unknown method: {method}"}); return
        self._json({"ok": True, "queued": p.name})

    # ── Response helpers ──────────────────────────────────────────────────────


    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=200)
        with _clients_lock:
            _clients.append(q)
        # Also register with Tableau's own SSE list
        if _TABLEAU_OK:
            with _tbl._clients_lock:
                _tbl._clients.append(q)

        try:
            if _TABLEAU_OK:
                init = f"event: state\ndata: {json.dumps(_tbl.conversion_state)}\n\n"
                self.wfile.write(init.encode("utf-8"))
                self.wfile.flush()
        except Exception:
            pass

        try:
            while True:
                try:
                    payload = q.get(timeout=25)
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)
            if _TABLEAU_OK:
                with _tbl._clients_lock:
                    if q in _tbl._clients:
                        _tbl._clients.remove(q)

    def _json(self, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _raw(self, status: int, hdrs: dict, body: bytes):
        self.send_response(status)
        ct = hdrs.get("Content-Type", hdrs.get("content-type", "application/json"))
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


# ─── Quiet server ─────────────────────────────────────────────────────────────
class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError, OSError)):
            return
        super().handle_error(request, client_address)


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Report Migration Tool — Tableau + Crystal Reports → Power BI")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--crystal-assembly-path", default="",
                        help="Path to folder containing CrystalDecisions DLLs. "
                             "Equivalent to: set CRYSTAL_ASSEMBLY_PATH=<path>")
    args = parser.parse_args()

    # Apply assembly path if provided via CLI (already applied above for pre-parse,
    # but re-apply here in case user passed it after the module imports)
    if args.crystal_assembly_path:
        os.environ["CRYSTAL_ASSEMBLY_PATH"] = args.crystal_assembly_path

    for d in ("input", "output", "source", "target"):
        os.makedirs(_HERE / d, exist_ok=True)

    server = _QuietServer(("0.0.0.0", args.port), Handler)

    print(f"""
╔══════════════════════════════════════════════════════════╗
║         Report Migration Tool  →  Power BI               ║
╠══════════════════════════════════════════════════════════╣
║  URL      : http://localhost:{args.port:<27}║
║  Tableau  : {'✓ Ready' if _TABLEAU_OK else '✗ Not loaded':<45}║
║  Crystal  : {'✓ Ready (SDK: ' + str(_cry.CRYSTAL_SDK_MODE) + ')' if _CRYSTAL_OK else '✗ Not loaded':<45}║
╚══════════════════════════════════════════════════════════╝
Tip: To set Crystal assembly path at startup:
  python report_migration_tool.py --crystal-assembly-path "C:\\path\\to\\dlls"
Or set it in the UI under Crystal Settings.
Press Ctrl+C to stop.
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[tool] Server stopped.")
        server.server_close()