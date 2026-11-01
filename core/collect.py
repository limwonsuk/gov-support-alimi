"""기관별 수집 → 점수 → DB 저장."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import db, extract, fetch, score

log = logging.getLogger("alimi")


def active_agencies(agencies, config):
    out = []
    for a in agencies.get("전국", []):
        if a.get("enabled", True):
            out.append({**a, "scope": "전국", "region": None})
    for region in config.get("내_지역", []):
        for a in agencies.get("지역", {}).get(region, []):
            if a.get("enabled", True):
                out.append({**a, "scope": "지역", "region": region})
    return out


def fetch_agency(agency):
    """한 기관의 공고 목록을 가져온다. 실패하면 예외."""
    items = []
    pages = int(agency.get("pages", 1))
    body = json.dumps(agency["body"], ensure_ascii=False) if "body" in agency else None
    paged = "{page}" in agency["url"] or "{page}" in (body or "")
    for page in range(1, pages + 1):
        url = agency["url"].replace("{page}", str(page))
        data = body.replace("{page}", str(page)).encode("utf-8") if body else None
        text = fetch.get(url, legacy_tls=agency.get("legacy_tls", False), headers=agency.get("headers"), data=data)
        link_base = agency.get("link_url") or url
        if agency.get("type") == "json":
            got = extract.extract_json(json.loads(text), agency["json"], link_base)
        else:
            got = extract.extract_items(text, link_base, agency.get("view_url"))
        for it in got:
            # 글 주소를 못 찾아 목록/첫 화면으로 가는 공고 표시 (대시보드에서 제목 복사 후 열기)
            it["direct"] = it["url"] not in (link_base, url, agency["url"])
        items.extend(got)
        if not paged or not got:
            break
    seen, uniq = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            uniq.append(it)
    return uniq


def run(con, agencies, config):
    """수집 한 번. 반환: (이번에 새로 들어온 공고 id 목록, 기관별 결과 목록)"""
    today = datetime.now().strftime("%Y-%m-%d")
    started = datetime.now().isoformat(timespec="seconds")
    targets = active_agencies(agencies, config)

    def job(a):
        try:
            return a, fetch_agency(a), None
        except Exception as e:  # noqa: BLE001 - 기관 하나가 실패해도 나머지는 계속
            return a, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(job, targets))

    new_ids, report, found = [], [], 0
    for a, items, err in results:
        if err:
            log.warning("[실패] %s — %s", a["name"], err)
        elif not items:
            log.warning("[0건] %s — 페이지 구조가 바뀌었을 수 있습니다 (설치설명서 '수리법' 참고)", a["name"])
        else:
            log.info("[성공] %s — %d건", a["name"], len(items))
        report.append({"name": a["name"], "count": len(items), "error": err})
        found += len(items)
        for it in items:
            nid = db.notice_id(a["name"], it["title"])
            row = {"agency": a["name"], "scope": a["scope"], "title": it["title"]}
            s, why = score.score(row, config)
            exists = con.execute("SELECT 1 FROM notices WHERE id=?", (nid,)).fetchone()
            if exists:
                con.execute(
                    "UPDATE notices SET last_seen=?, url=?, direct=?, posted=COALESCE(?, posted),"
                    " deadline=COALESCE(?, deadline), score=?, reasons=? WHERE id=?",
                    (today, it["url"], int(it["direct"]), it["posted"], it["deadline"], s, why, nid))
            else:
                con.execute(
                    "INSERT INTO notices (id, agency, scope, region, title, norm, url, direct, posted, deadline, score,"
                    " reasons, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, a["name"], a["scope"], a["region"], it["title"], score.norm_title(it["title"]), it["url"],
                     int(it["direct"]), it["posted"] or today, it["deadline"], s, why, today, today))
                new_ids.append(nid)
    con.execute("INSERT INTO runs (started, finished, found, new_count, sent, report) VALUES (?,?,?,?,?,?)",
                (started, datetime.now().isoformat(timespec="seconds"), found, len(new_ids), 0,
                 json.dumps(report, ensure_ascii=False)))
    con.commit()
    return new_ids, report


def rescore(con, config):
    """설정(지역·키워드)을 바꾼 뒤 저장된 공고 점수를 다시 계산"""
    rows = con.execute("SELECT id, agency, scope, title FROM notices").fetchall()
    for r in rows:
        s, why = score.score(dict(r), config)
        con.execute("UPDATE notices SET score=?, reasons=? WHERE id=?", (s, why, r["id"]))
    con.commit()
    return len(rows)
