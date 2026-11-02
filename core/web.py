"""내 컴퓨터에서만 열리는 대시보드 서버 (http://localhost:8765)."""
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import score

log = logging.getLogger("alimi")
PAGE = Path(__file__).with_name("dashboard.html")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            elif self.path.startswith("/api/data"):
                self._send(200, json.dumps(app.dashboard_data(), ensure_ascii=False), "application/json; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            # 다른 사이트가 몰래 수집을 돌리지 못하게 같은 출처 요청만 받는다
            origin = self.headers.get("Origin", "")
            if origin and not origin.startswith(("http://localhost", "http://127.0.0.1")):
                return self._send(403, "forbidden", "text/plain")
            if self.path == "/api/collect":
                started = app.start_collect_in_background()
                return self._send(200, json.dumps({"started": started}), "application/json")
            self._send(404, "not found", "text/plain")

    return Handler


class DashboardApp:
    def __init__(self, open_db, load_config, collect_and_notify):
        self.open_db = open_db
        self.load_config = load_config
        self.collect_and_notify = collect_and_notify
        self.collecting = False
        self._lock = threading.Lock()

    def start_collect_in_background(self):
        with self._lock:
            if self.collecting:
                return False
            self.collecting = True

        def work():
            try:
                self.collect_and_notify()
            except Exception:  # noqa: BLE001
                log.exception("수집 중 오류")
            finally:
                self.collecting = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def dashboard_data(self):
        config = self.load_config()
        today = datetime.now().strftime("%Y-%m-%d")
        passing = config.get("합격_점수", 50)
        con = self.open_db()
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT id, agency, scope, region, title, url, direct, posted, deadline, score, reasons, first_seen,"
                " notified_at"
                " FROM notices ORDER BY score DESC, COALESCE(posted, first_seen) DESC")]
            last = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
        days = config.get("신규_기준_일수", 3)
        for r in rows:
            r["is_new"] = score.is_new(r, today, days)
        return {
            "today": today,
            "regions": config.get("내_지역", []),
            "pass": passing,
            "collecting": self.collecting,
            "stats": {
                "total": len(rows),
                "passed": sum(1 for r in rows if r["score"] >= passing),
                "sent": sum(1 for r in rows if r["notified_at"]),
                "today_new": sum(1 for r in rows if r["is_new"]),
            },
            "last_run": dict(last) if last else None,
            "notices": rows,
        }


class _Server(ThreadingHTTPServer):
    # Windows 에서는 주소 재사용을 켜면 이미 켜진 대시보드와 같은 포트를 조용히 나눠 써 버린다
    allow_reuse_address = False


def serve(app, port):
    server = _Server(("127.0.0.1", port), make_handler(app))
    log.info("대시보드: http://localhost:%d  (끄려면 이 창을 닫거나 Ctrl+C)", port)
    server.serve_forever()
