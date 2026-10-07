# -*- coding: utf-8 -*-
"""شبیه‌ساز محلی REST کوئری D1 (فقط برای تست). شکل پاسخ‌ها طبق مستندات Cloudflare نوشته شده؛ تطابق با D1 واقعی را
`python d1sync.py selftest` روی حساب خود کاربر تأیید می‌کند."""
import json, re, sqlite3, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeD1:
    def __init__(self, path, token="tok-SECRET"):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None); self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock(); self.token = token; self.faults = []; self.batch_supported = True
        self.reject_marker = None; self.requests = 0; self.stmts = []
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            wbufsize = 65536  # هدر و بدنه در یک send (جلوگیری از تأخیر Nagle/delayed-ACK ~۴۰ms)
            def log_message(self, *a): pass
            def _send(self, code, obj, headers=None):
                data = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items(): self.send_header(k, v)
                self.end_headers(); self.wfile.write(data)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                with fake.lock:
                    fake.requests += 1
                    if self.headers.get("Authorization") != f"Bearer {fake.token}":
                        return self._send(403, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}], "result": []})
                    fault = fake.faults.pop(0) if fake.faults else None
                    if fault and fault[0] == "status":
                        return self._send(fault[1], {"success": False, "errors": [{"code": 1, "message": f"injected {fault[1]}"}], "result": []}, fault[2] if len(fault) > 2 else None)
                    if fault and fault[0] == "drop":
                        self.close_connection = True; return
                    try:
                        if "batch" in body:
                            if not fake.batch_supported:
                                return self._send(400, {"success": False, "errors": [{"code": 7400, "message": "missing field sql"}], "result": []})
                            partial = fault[1] if fault and fault[0] == "partial" else None
                            fake.db.execute("BEGIN")
                            out = []
                            for i, st in enumerate(body["batch"]):
                                if partial is not None and i >= partial:
                                    fake.db.execute("COMMIT")  # شبیه‌سازی اعمال ناقص (بدترین حالت): نیمی اعمال، نیمی نه
                                    return self._send(500, {"success": False, "errors": [{"code": 1, "message": "injected partial failure"}], "result": []})
                                out.append(fake.run(st["sql"], st.get("params") or []))
                            fake.db.execute("COMMIT")
                            return self._send(200, {"success": True, "errors": [], "messages": [], "result": out})
                        return self._send(200, {"success": True, "errors": [], "messages": [], "result": [fake.run(body["sql"], body.get("params") or [])]})
                    except sqlite3.Error as e:
                        try: fake.db.execute("ROLLBACK")
                        except sqlite3.Error: pass
                        return self._send(400, {"success": False, "errors": [{"code": 7500, "message": f"D1_ERROR: {e}"}], "result": []})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H); self.server.daemon_threads = True
        self.port = self.server.server_address[1]; self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def run(self, sql, params):
        if self.reject_marker is not None and any(p == self.reject_marker for p in params):
            raise sqlite3.IntegrityError("CHECK constraint failed: injected poison")
        self.stmts.append((sql, list(params)))
        cur = self.db.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()] if cur.description else []
        return {"results": rows, "success": True, "meta": {"changes": cur.rowcount}}

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.db.close()
