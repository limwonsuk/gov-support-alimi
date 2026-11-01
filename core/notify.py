"""텔레그램 알림.

- 새 합격 공고가 20건(요약_기준_건수) 이하: 공고마다 한 통
- 20건 초과: 요약 한 통
- 0건: "오늘은 없었습니다" 한 통
"""
import html
import json
import logging
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime

from . import fetch

log = logging.getLogger("alimi")


def _api(token, method, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=20, context=fetch._CTX) as res:
        body = json.loads(res.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(body.get("description", "텔레그램 오류"))
    return body["result"]


def send(tg, text):
    _api(tg["봇_토큰"], "sendMessage", {
        "chat_id": tg["채팅_ID"], "text": text[:4000],
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    })


def chat_ids(token):
    """봇에게 아무 메시지나 보낸 뒤 실행하면 채팅 ID 를 알려준다."""
    out = {}
    for u in _api(token, "getUpdates", {}):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat:
            out[chat["id"]] = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
    return out


def pick_alerts(con, new_ids, config):
    """새로 들어온 공고 중 알림 보낼 것. 다른 기관에 같은 공고가 이미 있으면 뺀다."""
    if not new_ids:
        return []
    passing = config.get("합격_점수", 50)
    today = datetime.now().strftime("%Y-%m-%d")
    marks = ",".join("?" * len(new_ids))
    rows = con.execute(
        f"SELECT * FROM notices WHERE id IN ({marks}) AND score >= ? AND notified_at IS NULL"
        " AND (deadline IS NULL OR deadline >= ?)"  # 이미 마감된 공고는 알리지 않는다
        " ORDER BY score DESC, scope DESC, COALESCE(deadline, '9999') ASC", (*new_ids, passing, today)).fetchall()
    picked, norms = [], set()
    for r in rows:
        if r["norm"] in norms:
            continue
        older = con.execute(
            f"SELECT 1 FROM notices WHERE norm=? AND id NOT IN ({marks}) LIMIT 1", (r["norm"], *new_ids)).fetchone()
        if older:
            continue
        norms.add(r["norm"])
        picked.append(r)
    return picked


def _line(r):
    dl = f" · 마감 {r['deadline']}" if r["deadline"] else ""
    hint = "" if r["direct"] else "\n(공고별 주소가 없는 사이트 — 열린 화면에서 제목으로 찾으세요)"
    return (f"<b>[{r['score']}점] {html.escape(r['title'])}</b>\n"
            f"{html.escape(r['agency'])}{dl}\n{html.escape(r['url'] or '')}{hint}")


def build_messages(rows, config, today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    limit = config.get("요약_기준_건수", 20)
    port = config.get("대시보드_포트", 8765)
    if not rows:
        return [f"📭 {today} 정부 지원사업 알리미\n오늘은 없었습니다."]
    if len(rows) <= limit:
        head = f"📬 {today} 새 지원사업 공고 {len(rows)}건"
        return [head] + [_line(r) for r in rows]
    by_agency = Counter(r["agency"] for r in rows)
    top = sorted(rows, key=lambda r: (-r["score"], r["deadline"] or "9999"))[:10]
    parts = [
        f"📋 {today} 새 지원사업 공고 <b>{len(rows)}건</b> (요약)",
        "기관별: " + ", ".join(f"{html.escape(k)} {v}" for k, v in by_agency.most_common()),
        "",
        "<b>점수 높은 순 10건</b>",
    ]
    for r in top:
        dl = f" (~{r['deadline'][5:]})" if r["deadline"] else ""
        parts.append(f"• [{r['score']}] {html.escape(r['title'][:60])}{dl}")
    parts += ["", f"전체 목록은 대시보드에서: http://localhost:{port}"]
    return ["\n".join(parts)]


def notify(con, new_ids, config):
    """알림을 보내고 보낸 공고 수를 돌려준다. 텔레그램을 안 쓰면 0."""
    tg = config.get("텔레그램", {})
    if not (tg.get("사용") and tg.get("봇_토큰") and tg.get("채팅_ID")):
        log.info("텔레그램 미사용 — 알림은 건너뜁니다 (대시보드에서 확인)")
        return 0
    rows = pick_alerts(con, new_ids, config)
    for msg in build_messages(rows, config):
        send(tg, msg)
        time.sleep(0.4)  # 텔레그램 초당 전송 제한
    now = datetime.now().isoformat(timespec="seconds")
    con.executemany("UPDATE notices SET notified_at=? WHERE id=?", [(now, r["id"]) for r in rows])
    con.execute("UPDATE runs SET sent=? WHERE id=(SELECT MAX(id) FROM runs)", (len(rows),))
    con.commit()
    log.info("텔레그램 알림 %d건 발송", len(rows))
    return len(rows)
