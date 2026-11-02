"""공고 점수 매기기.

- 내 지역 기관(테크노파크·진흥원) 공고: 100점에서 시작 → 전건 알림
- 전국 기관 공고: 70점에서 시작
    · 제목에 내 지역이 나오면 +30
    · 제목에 다른 지역만 나오면 -60  (예: 부산 사용자에게 '[서울] …' 공고)
    · 관심 키워드가 있으면 +30
- 제외 키워드(채용·입찰 등)가 있으면 어느 기관이든 -60
점수는 0~100 사이로 자르고, 합격_점수(기본 50) 이상만 알림/합격으로 칩니다.
"""
import re
from datetime import date, timedelta

REGION_WORDS = {
    "서울": ["서울"],
    "부산": ["부산"],
    "대구": ["대구"],
    "인천": ["인천"],
    "광주": ["광주광역시", "광주시", "[광주]", "광주 "],
    "대전": ["대전"],
    "울산": ["울산"],
    "세종": ["세종시", "세종특별", "[세종]", "세종 "],
    "경기": ["경기도", "[경기]", "경기 ", "수원", "성남", "용인", "고양", "화성시", "안산", "안양", "부천", "평택", "시흥", "파주", "김포"],
    "강원": ["강원", "춘천", "원주", "강릉"],
    "충북": ["충북", "충청북도", "청주", "충주"],
    "충남": ["충남", "충청남도", "천안", "아산", "당진", "서산"],
    "전북": ["전북", "전라북도", "전주", "익산", "군산", "남원"],
    "전남": ["전남", "전라남도", "목포", "여수", "순천", "광양", "나주"],
    "경북": ["경북", "경상북도", "포항", "구미", "경주", "안동"],
    "경남": ["경남", "경상남도", "창원", "김해", "진주", "양산", "거제", "통영"],
    "제주": ["제주"],
}


def regions_in(title):
    t = title + " "
    return [r for r, words in REGION_WORDS.items() if any(w in t for w in words)]


def _hit(title, words):
    low = title.lower()
    return [w for w in words if w and w.lower() in low]


def score(notice, config):
    """(점수, 이유 문자열) 를 돌려준다."""
    title = notice["title"]
    my = config.get("내_지역", [])
    reasons = []

    if notice["scope"] == "지역" and notice.get("region") not in (None, *my):
        # 예전에 골랐다가 뺀 지역의 기관 공고 (이미 모아 둔 것)
        s = 10
        reasons.append(f"내 지역 아님({notice['region']})")
    elif notice["scope"] == "지역":
        s = 100
        reasons.append(f"{notice['agency']} 공고 (전건 알림)")
    else:
        s = 70
        found = regions_in(title)
        mine = [r for r in found if r in my]
        others = [r for r in found if r not in my]
        if mine:
            s += 30
            reasons.append(f"내 지역({'·'.join(mine)})")
        elif others:
            s -= 60
            reasons.append(f"다른 지역({'·'.join(others)})")
        else:
            reasons.append("전국 공고")
        hits = _hit(title, config.get("관심_키워드", []))
        if hits:
            s += 30
            reasons.append("관심: " + ", ".join(hits[:3]))

    bad = _hit(title, config.get("제외_키워드", []))
    if bad:
        s -= 60
        reasons.append("제외: " + ", ".join(bad[:3]))

    s = max(0, min(100, s))
    return s, " · ".join(reasons)


def is_new(notice, today, days=3):
    """'오늘 신규' = 오늘 처음 수집했고, 게시일도 최근(기본 3일 이내)인 공고.

    예전에 올라온 공고가 오늘 처음 보이는 경우(접수 중 목록에 새로 들어옴, 제목 수정 등)는 신규가 아니다.
    3일 여유는 어제 오후·주말에 올라온 공고를 아침에 처음 수집하는 경우를 위한 것.
    """
    if notice["first_seen"] != today:
        return False
    posted = notice["posted"]
    if not posted:
        return True
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    return posted >= cutoff


def norm_title(title):
    """같은 공고가 기관마다 조금씩 다르게 올라와도 한 건으로 보기 위한 비교용 제목"""
    t = re.sub(r"\[[^\]]{1,10}\]|\([^)]{0,12}\)|「|」|『|』|'|\"", "", title)
    return re.sub(r"[\s·ㆍ,.\-]", "", t)
