"""정부 지원사업 알리미

사용법 (이 폴더에서):
    python alimi.py run            수집 + 텔레그램 알림 (매일 아침 자동 실행되는 명령)
    python alimi.py serve          대시보드 열기 (http://localhost:8765)
    python alimi.py check          기관별로 수집이 잘 되는지 점검
    python alimi.py rescore        config.json 을 바꾼 뒤 저장된 공고 점수 다시 계산
    python alimi.py telegram-id    텔레그램 채팅 ID 확인
    python alimi.py telegram-test  텔레그램 테스트 메시지 보내기
    python alimi.py schedule 08:00 매일 아침 자동 실행 등록 (Windows 작업 스케줄러)
    python alimi.py unschedule     자동 실행 해제
    python alimi.py export site    대시보드를 웹 게시용 파일로 내보내기 (GitHub Pages)
"""
import json
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core import collect, db, notify, web

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
AGENCIES = ROOT / "agencies.json"
DB_PATH = ROOT / "data" / "alimi.db"
LOG_PATH = ROOT / "data" / "alimi.log"
TASK_NAME = "정부지원사업알리미"

log = logging.getLogger("alimi")


def setup_logging():
    LOG_PATH.parent.mkdir(exist_ok=True)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout:  # pythonw 로 실행되면 콘솔이 없다
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(sh)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"[설정 오류] {path.name} {e.lineno}번째 줄 근처에 쉼표·따옴표가 빠졌습니다: {e.msg}")


def load_config():
    if not CONFIG.exists():
        # 처음 받은 경우: 예시 설정을 복사해서 시작 (config.json 은 개인 설정이라 저장소에 없음)
        CONFIG.write_text((ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    return load_json(CONFIG)


def open_db():
    return db.connect(DB_PATH)


def collect_and_notify():
    config, agencies = load_config(), load_json(AGENCIES)
    con = open_db()
    try:
        new_ids, report = collect.run(con, agencies, config)
        ok = sum(1 for r in report if r["count"])
        log.info("수집 완료: %d곳 중 %d곳 정상, 새 공고 %d건", len(report), ok, len(new_ids))
        try:
            notify.notify(con, new_ids, config)
        except Exception as e:  # noqa: BLE001 - 알림 실패해도 수집 결과는 남긴다
            log.error("텔레그램 발송 실패: %s (봇 토큰·채팅 ID 확인)", e)
    finally:
        con.close()


def cmd_check():
    config, agencies = load_config(), load_json(AGENCIES)
    targets = collect.active_agencies(agencies, config)
    print(f"내 지역: {' · '.join(config.get('내_지역', []))} → 점검할 기관 {len(targets)}곳\n")
    bad = 0
    for a in targets:
        try:
            items = collect.fetch_agency(a)
            if items:
                last = items[0]
                print(f"  ✓ {a['name']:<14} {len(items):>3}건   예) {last['title'][:40]}  (마감 {last['deadline'] or '-'})")
            else:
                bad += 1
                print(f"  ✗ {a['name']:<14}   0건   ← 페이지 주소가 바뀌었거나 구조가 달라졌습니다")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  ✗ {a['name']:<14} 접속 실패: {e}")
    print(f"\n{'모두 정상입니다.' if not bad else f'{bad}곳 확인 필요 — 설치설명서.md 의 수리법을 보세요.'}")


def cmd_serve(open_browser=True, port=None):
    config = load_config()
    port = int(port or config.get("대시보드_포트", 8765))
    app = web.DashboardApp(open_db, load_config, collect_and_notify)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    try:
        web.serve(app, port)
    except OSError:
        print(f"포트 {port} 가 이미 쓰이고 있습니다. 대시보드가 이미 켜져 있는지 확인하거나 config.json 의 대시보드_포트를 바꾸세요.")
        webbrowser.open(f"http://localhost:{port}")
    except KeyboardInterrupt:
        pass


def cmd_telegram_id():
    tg = load_config().get("텔레그램", {})
    if not tg.get("봇_토큰"):
        sys.exit("config.json 의 텔레그램 > 봇_토큰 을 먼저 넣으세요.")
    ids = notify.chat_ids(tg["봇_토큰"])
    if not ids:
        print("아직 받은 메시지가 없습니다. 텔레그램에서 내 봇에게 아무 말(예: 안녕)이나 보낸 뒤 다시 실행하세요.")
    for cid, name in ids.items():
        print(f"채팅 ID: {cid}   ({name})")
    if ids:
        print('\n이 숫자를 config.json 의 "채팅_ID" 에 넣고 "사용": true 로 바꾸세요.')


def cmd_telegram_test():
    tg = load_config().get("텔레그램", {})
    if not (tg.get("봇_토큰") and tg.get("채팅_ID")):
        sys.exit("config.json 의 봇_토큰·채팅_ID 를 먼저 넣으세요.")
    notify.send(tg, "✅ 정부 지원사업 알리미 연결 테스트입니다. 이 메시지가 보이면 설정 완료!")
    print("보냈습니다. 텔레그램을 확인하세요.")


def cmd_schedule(at):
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    # 컴퓨터가 꺼져 있어 시간을 놓치면 켜진 뒤 바로 실행(-StartWhenAvailable)
    ps = (
        f"$a = New-ScheduledTaskAction -Execute '{exe}' -Argument '\"{ROOT / 'alimi.py'}\" run' -WorkingDirectory '{ROOT}';"
        f"$t = New-ScheduledTaskTrigger -Daily -At {at};"
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"등록했습니다. 매일 {at} 에 자동으로 수집·알림합니다. (작업 스케줄러 > '{TASK_NAME}')")
    else:
        print("등록 실패:", r.stderr.strip())


def cmd_export(out_dir):
    """대시보드를 서버 없이 열리는 웹 페이지(index.html + data.json)로 내보낸다 — GitHub Pages 게시용"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = web.DashboardApp(open_db, load_config, None).dashboard_data()
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    repo = os.environ.get("GITHUB_REPOSITORY")
    static = {"actions": f"https://github.com/{repo}/actions/workflows/collect.yml" if repo else None}
    page = web.PAGE.read_text(encoding="utf-8").replace(
        "</head>", f"<script>window.ALIMI_STATIC = {json.dumps(static)};</script>\n</head>", 1)
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"내보냈습니다: {out.resolve()} (공고 {len(data['notices'])}건)")


def cmd_setup():
    config = load_config()
    print("=" * 56)
    print(" 정부 지원사업 알리미 설치")
    print("=" * 56)
    print(f"\n[1/3] 내 지역: {' · '.join(config.get('내_지역', []))}")
    print("      (바꾸려면 config.json 의 \"내_지역\" 을 고친 뒤 이 파일을 다시 실행)\n")
    print("[2/3] 기관 점검 및 첫 수집 (1~2분)\n")
    cmd_check()
    collect_and_notify()
    at = input("\n[3/3] 매일 몇 시에 수집할까요? (예: 07:30, 그냥 엔터 = 08:00, 건너뛰기 = n): ").strip() or "08:00"
    if at.lower() != "n":
        cmd_schedule(at)
    print("\n설치 끝! '2_대시보드_열기.bat' 으로 공고를 보세요. 텔레그램 연결은 설치설명서.md 3장을 보세요.")


def cmd_unschedule():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false"], capture_output=True, text=True)
    print("자동 실행을 해제했습니다." if r.returncode == 0 else "등록된 자동 실행이 없습니다.")


def main():
    setup_logging()
    args = sys.argv[1:] or ["serve"]
    cmd = args[0]
    if cmd == "run":
        collect_and_notify()
    elif cmd == "collect":
        config = load_config()
        con = open_db()
        collect.run(con, load_json(AGENCIES), config)
        con.close()
    elif cmd == "serve":
        port = args[args.index("--port") + 1] if "--port" in args else None
        cmd_serve(open_browser="--no-browser" not in args, port=port)
    elif cmd == "check":
        cmd_check()
    elif cmd == "rescore":
        con = open_db()
        print(f"{collect.rescore(con, load_config())}건 점수를 다시 계산했습니다.")
        con.close()
    elif cmd == "telegram-id":
        cmd_telegram_id()
    elif cmd == "telegram-test":
        cmd_telegram_test()
    elif cmd == "schedule":
        cmd_schedule(args[1] if len(args) > 1 else "08:00")
    elif cmd == "unschedule":
        cmd_unschedule()
    elif cmd == "setup":
        cmd_setup()
    elif cmd == "export":
        cmd_export(args[1] if len(args) > 1 else "site")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
