"""게시판 목록 페이지에서 공고(제목·링크·날짜)를 뽑아낸다.

사이트마다 구조가 달라서, 특정 선택자 대신 '날짜가 들어 있는 한 줄(tr/li)'을
공고 한 건으로 보는 방식을 쓴다. 대부분의 공공기관 게시판에서 통한다.
"""
import re
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ROW_TAGS = {"tr", "li", "dl"}
SKIP_TAGS = {"script", "style", "noscript", "head", "select", "option"}

DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?!\d)"
    r"|(?<![\d.])(\d{2})\.(\d{2})\.(\d{2})(?![\d.])"
)
JUNK_TITLES = re.compile(r"^(자세히|더보기|바로가기|상세보기|보기|다운로드|첨부|new|N|이전|다음|\d+)$", re.I)


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        out = []
        stack = [self]
        while stack:
            n = stack.pop()
            if isinstance(n, str):
                out.append(n)
            elif n.tag not in SKIP_TAGS:
                stack.extend(reversed(n.children))
        return re.sub(r"\s+", " ", " ".join(out)).strip()

    def iter(self, tags=None):
        stack = [self]
        while stack:
            n = stack.pop()
            if isinstance(n, Node):
                if tags is None or n.tag in tags:
                    yield n
                stack.extend(reversed(n.children))


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        # 닫는 태그를 빠뜨린 <tr>/<li>/<td> 는 같은 태그가 다시 열릴 때 닫아 준다
        if tag in ("tr", "li", "td", "th", "dt", "dd"):
            p = self.cur
            while p is not self.root and p.tag not in ("table", "tbody", "thead", "ul", "ol", "dl", "div"):
                if p.tag == tag:
                    self.cur = p.parent
                    break
                p = p.parent
        node = Node(tag, [(k, v or "") for k, v in attrs], self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, [(k, v or "") for k, v in attrs], self.cur))

    def handle_endtag(self, tag):
        p = self.cur
        while p is not self.root and p.tag != tag:
            p = p.parent
        if p is not self.root:
            self.cur = p.parent

    def handle_data(self, data):
        if data.strip():
            self.cur.children.append(data)


def parse_html(html):
    b = _Builder()
    try:
        b.feed(html)
        b.close()
    except Exception:  # noqa: BLE001 - 깨진 HTML 도 최대한 쓰기
        pass
    return b.root


def find_dates(text):
    """(날짜문자열, 시작위치, 끝위치) 목록"""
    out = []
    for m in DATE_RE.finditer(text):
        if m.group(1):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            y, mo, d = 2000 + int(m.group(4)), int(m.group(5)), int(m.group(6))
        if 1 <= mo <= 12 and 1 <= d <= 31 and 2015 <= y <= 2035:
            out.append((f"{y:04d}-{mo:02d}-{d:02d}", m.start(), m.end()))
    return out


def pick_dates(text):
    """줄 안의 날짜들에서 (게시일, 마감일)을 고른다. '시작 ~ 끝' 이 있으면 끝이 마감일."""
    ds = find_dates(text)
    if not ds:
        return None, None
    # '마감일자 2026-11-20', '등록일 2026-10-30' 처럼 이름표가 붙어 있으면 그대로 따른다
    labeled = {}
    prev_end = 0
    for d, s, e in ds:
        before = text[max(prev_end, s - 10):s]
        prev_end = e
        if re.search(r"마감|종료", before):
            labeled.setdefault("deadline", d)
        elif re.search(r"등록|게시|작성|공고일", before):
            labeled.setdefault("posted", d)
    if "deadline" in labeled:
        return labeled.get("posted") or next((d for d, _, _ in ds if d != labeled["deadline"]), None), labeled["deadline"]
    deadline = None
    range_idx = set()
    for i in range(len(ds) - 1):
        between = text[ds[i][2]:ds[i + 1][1]]
        if len(between) < 25 and re.search(r"[~∼〜]|부터|^\s*-\s*$", between):
            deadline = ds[i + 1][0]
            range_idx.update((i, i + 1))
    singles = [d[0] for i, d in enumerate(ds) if i not in range_idx]
    if singles:
        posted = singles[-1] if deadline else singles[0]
    else:
        posted = ds[0][0]  # 범위만 있으면 시작일을 게시일로
    if not deadline and len(ds) == 1 and re.search(r"마감|까지", text):
        deadline, posted = ds[0][0], None
    return posted, deadline


TAIL_JUNK = r"(새창|새 창|새창열림|새 창 열림|페이지 이동|새로운게시글|새글|new|NEW|N|첨부파일 있음|파일첨부|첨부파일)"


def _clean_title(t):
    t = re.sub(r"\s+", " ", t).strip()
    for _ in range(3):
        t = re.sub(r"\s*" + TAIL_JUNK + r"\s*(으로)?\s*(열림)?\s*$", "", t)
        t = re.sub(r"^(\[?공지\]?|NEW|new|N|새글|접수중|진행중|모집중|공고중|접수예정|예정|마감|완료)\s+", "", t)
    # 같은 제목이 두 번 들어간 경우(hover 용 복사본 등) 앞의 것만 남긴다
    if len(t) > 30:
        head = t[:15]
        i = t.find(head, 10)
        if i > 0:
            t = t[:i]
    return t.strip(" -·|")


def _title_of(a):
    # 링크 안에 제목 전용 요소가 있으면 그것만 쓴다(담당부서·날짜 같은 부가정보 제외)
    for n in a.iter():
        if n is not a and re.search(r"tit|subj", n.attrs.get("class", ""), re.I):
            t = _clean_title(n.text())
            if len(t) >= 6:
                return t
    return _clean_title(a.text())


def _best_link(row):
    best = None
    # <a> 가 없고 <span onclick="goView(…)"> 처럼 다른 요소에 클릭이 걸린 게시판도 있다
    links = [n for n in row.iter() if n.tag == "a" or n.tag in ("span", "strong", "p", "b") and "onclick" in n.attrs]
    for a in links:
        if a.tag != "a" and any(n is not a and n.tag == "a" for n in a.iter()):
            continue
        t = _clean_title(a.attrs.get("title") or "")
        txt = _title_of(a)
        cand = txt if len(txt) >= len(t) else t
        if len(cand) < 6 or JUNK_TITLES.match(cand) or find_dates(cand) and len(cand) < 25:
            continue
        # '공고문.pdf 다운로드' 같은 첨부파일 링크는 제목이 길어도 글 링크로 쓰지 않는다
        if re.search(r"\.\w{2,4}\s*(다운로드|내려받기)?$|다운로드$", cand):
            continue
        if best is None or len(cand) > len(best[0]):
            best = (cand, a)
    return best


def _resolve_href(a, row, base_url, view_url):
    href = (a.attrs.get("href") or "").strip()
    if href and not href.lower().startswith(("javascript", "#")):
        return re.sub(r";jsessionid=[^?#]*", "", urljoin(base_url, href), flags=re.I)
    # onclick="location.href='PostingDetail.aspx?mid=…'" 처럼 주소가 그대로 적혀 있으면 그걸 쓴다
    for script in (href, a.attrs.get("onclick", ""), *(n.attrs.get("onclick", "") for n in _ancestors(a, row, extra=4))):
        m = re.search(r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", script)
        if m:
            return urljoin(base_url, m.group(1))
    if view_url:
        # javascript:go_view(179660) / onclick="doBbsFView('310','1071948',...)" 같은 자바스크립트 호출에서
        # 인자를 꺼내 view_url 의 {0},{1}… (몇 번째 인자) 또는 {id} (가장 긴 숫자) 자리에 넣는다
        for script in (href, a.attrs.get("onclick", ""), *(n.attrs.get("onclick", "") for n in _ancestors(a, row))):
            m = re.search(r"(?<![\w.])(?!void\b)\w+\s*\((.*)\)", script)
            if not m:
                continue
            # 따옴표 안의 쉼표·괄호는 그대로 둔 채 인자를 나눈다 ('제목(상시), 2차' 같은 경우)
            args = [q1 or q2 or bare.strip() for q1, q2, bare in
                    re.findall(r"\s*(?:'([^']*)'|\"([^\"]*)\"|([^,]*))\s*(?:,|$)", m.group(1))]
            nums = re.findall(r"\d{3,}", m.group(1))
            url = view_url
            if "{id}" in url:
                if not nums:
                    continue
                url = url.replace("{id}", max(nums, key=len))
            try:
                url = re.sub(r"\{(\d)\}", lambda k: quote(args[int(k.group(1))], safe="/"), url)
            except IndexError:
                continue
            return urljoin(base_url, url)
    return base_url


def _ancestors(a, row, extra=0):
    """링크에서 줄(row)까지(그리고 extra 단계 더) 올라가는 요소들"""
    n, past = a.parent, None
    while n is not None:
        yield n
        if n is row:
            past = 0
        if past is not None:
            if past >= extra:
                break
            past += 1
        n = n.parent


def _dig(obj, path):
    for key in [p for p in (path or "").split(".") if p]:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def extract_json(data, spec, base_url):
    """JSON 으로 목록을 주는 사이트용. spec 예:
    {"list": "content", "title": "annoName", "posted": "createdDate", "deadline": "receiptEDate", "link": "https://.../{id}"}
    """
    rows = _dig(data, spec.get("list")) if spec.get("list") else data
    items = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        title = _clean_title(str(r.get(spec["title"]) or ""))
        if not title:
            continue
        posted = find_dates(str(r.get(spec.get("posted", ""), "") or ""))
        deadline = find_dates(str(r.get(spec.get("deadline", ""), "") or ""))
        link = spec.get("link") or base_url
        try:
            link = link.format(**r)
        except (KeyError, IndexError, ValueError):
            link = base_url
        items.append({
            "title": title, "url": link,
            "posted": posted[0][0] if posted else None,
            "deadline": deadline[0][0] if deadline else None,
            "row_text": title,
        })
    return items


def extract_items(html, base_url, view_url=None, max_dates=6):
    root = parse_html(html)
    rows = []
    for node in root.iter(ROW_TAGS | {"div"}):
        if node.tag == "div":
            cls = node.attrs.get("class", "")
            if not re.search(r"(^|[\s_-])(item|card|row|box|list_con)([\s_-]|$)|[a-z](Item|Card|Row|Box)(\s|$)", cls):
                continue
        rows.append(node)

    qualified = {}
    for row in rows:
        txt = row.text()
        if len(txt) > 700:
            continue
        ds = find_dates(txt)
        if not ds or len(ds) > max_dates:
            continue
        link = _best_link(row)
        if not link:
            continue
        qualified[id(row)] = (row, txt, link)

    # 바깥쪽 줄이 안쪽 줄을 품고 있으면 안쪽(더 작은) 줄만 쓴다
    items, seen = [], set()
    for row, txt, (title, a) in qualified.values():
        if any(id(n) in qualified for n in row.iter() if n is not row):
            continue
        # 제목 속 날짜(예: '~26.12.31')가 게시일로 잡히지 않게 제목을 빼고 날짜를 찾는다
        posted, deadline = pick_dates(txt.replace(a.text(), " ", 1))
        url = _resolve_href(a, row, base_url, view_url)
        # 머리글의 '인기 검색어' 순위처럼 검색 결과로 가는 링크는 공고가 아니다
        if title in seen or re.search(r"search\.\w+\?", url):
            continue
        seen.add(title)
        items.append({"title": title, "url": url, "posted": posted, "deadline": deadline, "row_text": txt})
    return items
