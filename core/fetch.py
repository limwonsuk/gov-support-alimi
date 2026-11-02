"""웹 페이지 가져오기 (표준 라이브러리만 사용)."""
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _new_context():
    # 국내 공공기관 사이트 상당수가 중간 인증서를 빠뜨려서 파이썬 기본 검증은 실패한다.
    # truststore 가 설치돼 있으면 브라우저처럼 Windows 인증서 저장소로 검증한다(검증은 그대로 유지).
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


_CTX = _new_context()

# 일부 서버는 오래된 재협상 방식·짧은 DH 키를 써서 기본 설정으로는 악수(handshake)가 실패한다.
# 인증서 검증은 그대로 두고, 기관 설정에 "legacy_tls": true 가 있을 때만 이 호환 설정을 쓴다.
_LEGACY_CTX = _new_context()
_LEGACY_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
try:
    _LEGACY_CTX.set_ciphers("DEFAULT:@SECLEVEL=1")
except ssl.SSLError:
    pass

# Linux(GitHub Actions)에서는 빠진 중간 인증서를 자동으로 받아 오지 않는다.
# certs/ 폴더의 중간 인증서를 더해 다시 검증한다 — 최상위(루트) 인증서까지 이어져야 통과하므로 검증은 그대로다.
_CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"


def _intermediate_context(legacy):
    ctx = ssl.create_default_context()
    for pem in sorted(_CERTS_DIR.glob("*.pem")):
        ctx.load_verify_locations(cafile=str(pem))
    if legacy:
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx


def _decode(raw, content_type):
    charset = None
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    else:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4000], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    charset = (charset or "utf-8").lower()
    if charset in ("euc-kr", "ks_c_5601-1987", "ksc5601"):
        charset = "cp949"
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", "ignore")


def get(url, timeout=20, retries=1, legacy_tls=False, headers=None, data=None):
    """data(bytes) 를 주면 POST 로 보낸다."""
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9",
                **(headers or {}),
            })
            ctx = _LEGACY_CTX if legacy_tls else _CTX
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as res:
                    return _decode(res.read(), res.headers.get("Content-Type"))
            except urllib.error.URLError as e:
                if not isinstance(e.reason, ssl.SSLCertVerificationError):
                    raise
                with urllib.request.urlopen(req, timeout=timeout, context=_intermediate_context(legacy_tls)) as res:
                    return _decode(res.read(), res.headers.get("Content-Type"))
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 재시도 후 보고
            last = e
            time.sleep(1.5)
    raise last
