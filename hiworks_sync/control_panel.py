from __future__ import annotations

import html
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .summary import sync_summary
from .title_manager import generate_hiworks_titles
from .uploader import upload_selected


class ControlState:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.lock = threading.Lock()
        self.logs: list[str] = []
        self.last_title_generation = "아직 없음"
        self.last_upload = "아직 없음"
        self.last_error = ""
        self.running_titles = False
        self.running_upload = False

    def log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"[{timestamp}] {message}")
            self.logs = self.logs[-200:]


def run_control_panel(settings: dict, host: str, port: int, sync_interval: int | None = None) -> None:
    state = ControlState(settings)
    server = ThreadingHTTPServer((host, port), _handler_factory(state))

    url = f"http://{host}:{port}/"
    print(f"컨트롤 패널: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def _handler_factory(state: ControlState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._html(_page(state))
                return
            if parsed.path == "/status":
                self._json(_status(state))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            _ = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))

            if parsed.path == "/generate-titles":
                if state.running_titles or state.running_upload:
                    self._json({"ok": False, "message": "이미 제목 생성이 실행 중입니다."}, status=409)
                    return
                thread = threading.Thread(target=_title_thread, args=(state,), daemon=True)
                thread.start()
                self._json({"ok": True, "message": "하이웍스 제목 생성을 시작했습니다."})
                return

            if parsed.path == "/upload-selected":
                if state.running_titles or state.running_upload:
                    self._json({"ok": False, "message": "이미 기안 생성이 실행 중입니다."}, status=409)
                    return
                thread = threading.Thread(target=_upload_only_thread, args=(state,), daemon=True)
                thread.start()
                self._json({"ok": True, "message": "하이웍스 기안 생성을 시작했습니다."})
                return

            if parsed.path == "/generate-and-upload":
                if state.running_titles or state.running_upload:
                    self._json({"ok": False, "message": "이미 기안 생성이 실행 중입니다."}, status=409)
                    return
                thread = threading.Thread(target=_generate_and_upload_thread, args=(state,), daemon=True)
                thread.start()
                self._json({"ok": True, "message": "제목, 기안 일괄 생성을 시작했습니다."})
                return

            if parsed.path == "/demo-logs":
                for message in _demo_logs():
                    state.log(message)
                self._json({"ok": True, "message": "테스트 로그를 표시했습니다."})
                return

            self.send_error(404)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _html(self, text: str, status: int = 200) -> None:
            self._send(text.encode("utf-8"), "text/html; charset=utf-8", status)

        def _json(self, payload: dict, status: int = 200) -> None:
            self._send(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _send(self, data: bytes, content_type: str, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionAbortedError, BrokenPipeError):
                return

    return Handler


def _title_thread(state: ControlState) -> None:
    with state.lock:
        state.running_titles = True
    try:
        state.log(_block("[제목 생성] 시작", ["작업지시: 하이웍스 제목 생성"]))
        result = generate_hiworks_titles(state.settings)
        state.last_title_generation = time.strftime("%Y-%m-%d %H:%M:%S")
        state.last_error = ""
        state.log(_title_result_message("[제목 생성] 완료", result))
        count = sync_summary(state.settings)
        state.log(_block("[종합시트] 갱신 완료", [f"작성된 행: {count}행"]))
    except Exception as exc:
        state.last_error = str(exc)
        state.log(_block("[제목 생성] 오류", [f"작업: 제목 생성 또는 종합시트 갱신", f"오류: {exc}"]))
    finally:
        with state.lock:
            state.running_titles = False


def _upload_only_thread(state: ControlState) -> None:
    with state.lock:
        state.running_upload = True
    try:
        state.log(_block("[기안 생성] 시작", ["작업지시: 하이웍스 기안 생성"]))
        uploaded = upload_selected(state.settings, state.log, keep_browser_open=False)
        state.last_upload = time.strftime("%Y-%m-%d %H:%M:%S")
        state.last_error = ""
        state.log(_upload_result_message("[기안 생성] 완료", uploaded))
    except Exception as exc:
        state.last_error = str(exc)
        state.log(_block("[기안 생성] 오류로 중단", [f"오류: {exc}"]))
    finally:
        with state.lock:
            state.running_upload = False


def _generate_and_upload_thread(state: ControlState) -> None:
    with state.lock:
        state.running_titles = True
        state.running_upload = True
    try:
        state.log(
            _block(
                "[일괄 생성] 시작",
                [
                    "작업지시: 제목 생성 후 하이웍스 기안 생성",
                ],
            )
        )
        title_result = generate_hiworks_titles(state.settings)
        state.last_title_generation = time.strftime("%Y-%m-%d %H:%M:%S")
        state.log(_title_result_message("[제목 생성] 완료", title_result))
        with state.lock:
            state.running_titles = False
        summary_count = sync_summary(state.settings)
        state.log(_block("[종합시트] 갱신 완료", [f"작성된 행: {summary_count}행"]))
        uploaded = upload_selected(state.settings, state.log, keep_browser_open=False)
        state.last_upload = time.strftime("%Y-%m-%d %H:%M:%S")
        state.last_error = ""
        state.log(_upload_result_message("[일괄 생성] 완료", uploaded))
    except Exception as exc:
        state.last_error = str(exc)
        state.log(_block("[일괄 생성] 오류로 중단", [f"오류: {exc}"]))
    finally:
        with state.lock:
            state.running_titles = False
            state.running_upload = False


def _title_result_message(prefix: str, result: dict[str, int]) -> str:
    lines = [f"작업결과: 품의 {result.get('titles_procurement', 0)}건 / 집행 {result.get('titles_execution', 0)}건"]
    return _block(
        prefix,
        lines,
    )


def _upload_result_message(prefix: str, uploaded) -> str:
    return _block(
        prefix,
        [
            f"전체: {uploaded.total}건",
            f"완료: {uploaded.completed}건",
            f"실패: {uploaded.failed}건",
            f"미처리: {uploaded.remaining}건",
        ],
    )


def _block(title: str, lines: list[str]) -> str:
    return "\n".join([title, *(f"- {line}" for line in lines)])


def _demo_logs() -> list[str]:
    return [
        _block(
            "[제목 생성] 완료",
            [
                "작업결과: 품의 2건 / 집행 1건",
            ],
        ),
        _block(
            "[기안 생성] 대상 확인",
            [
                "전체: 6건",
                "품의: 3건",
                "집행: 3건",
            ],
        ),
        _block(
            "[기안 생성] 시작 (1/3)",
            [
                "구분: 품의",
                "제목: X / [품의] 260515 / 테스트 품의",
            ],
        ),
        _block(
            "[기안 생성] 하이웍스 기안 완료",
            [
                "구분: 품의",
                "제목: X / [품의] 260515 / 테스트 품의",
                "URL: https://approval.office.hiworks.com/example",
            ],
        ),
        _block(
            "[기안 생성] 오류",
            [
                "작업: 제목 입력 실패",
                "구분: 품의",
                "제목: X / [품의] 260515 / 테스트 품의",
                "오류: 하이웍스 제목 칸에 제목을 입력하지 못했습니다.",
                "현황: 전체 6건 / 완료 1건 / 실패 1건 / 미처리 4건",
            ],
        ),
        _block(
            "[기안 생성] 오류",
            [
                "작업: 본문 작성 실패",
                "구분: 집행",
                "제목: X / [집행] 260515 / 테스트 집행",
                "오류: 하이웍스 본문 내용을 입력하지 못했습니다.",
                "현황: 전체 6건 / 완료 1건 / 실패 2건 / 미처리 3건",
            ],
        ),
        _block(
            "[기안 생성] 오류",
            [
                "작업: 참조자 등록 실패",
                "구분: 품의",
                "제목: X / [품의] 260515 / 테스트 참조자",
                "오류: 하이웍스 참조자를 등록하지 못했습니다.",
                "현황: 전체 6건 / 완료 1건 / 실패 3건 / 미처리 2건",
            ],
        ),
        _block(
            "[기안 생성] 오류",
            [
                "작업: 하이웍스 기안 실패",
                "구분: 집행",
                "제목: X / [집행] 260515 / 테스트 집행",
                "오류: 기안하기를 눌렀지만 하이웍스가 기안을 완료하지 않았습니다.",
                "현황: 전체 6건 / 완료 1건 / 실패 4건 / 미처리 1건",
            ],
        ),
        _block(
            "[기안 생성] 오류",
            [
                "작업: 기안 URL 확인 실패",
                "구분: 집행",
                "제목: X / [집행] 260515 / 테스트 URL",
                "오류: 기안 완료 후 문서 URL을 확인하지 못했습니다.",
                "확인: 하이웍스에 해당 문서 기안이 정상적으로 완료됐는지 확인해주세요.",
                "현황: 전체 6건 / 완료 1건 / 실패 5건 / 미처리 0건",
            ],
        ),
        _block(
            "[기안 생성] 오류",
            [
                "작업: 종합시트 URL 기록 실패",
                "결과: 하이웍스 기안은 완료됨",
                "구분: 출신",
                "제목: X / [출신] 260515 / 테스트 출장신청",
                "URL: https://approval.office.hiworks.com/example-travel",
            ],
        ),
    ]


def _status(state: ControlState) -> dict:
    with state.lock:
        return {
            "last_title_generation": state.last_title_generation,
            "last_upload": state.last_upload,
            "last_error": state.last_error,
            "running_titles": state.running_titles,
            "running_upload": state.running_upload,
            "logs": list(state.logs),
        }


def _page(state: ControlState) -> str:
    spreadsheet_url = html.escape(state.settings["spreadsheet"]["url"])
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>[2026 디싹] 하이웍스 제목, 기안 생성 자동화 프로그램</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --panel: #ffffff;
      --line: #d9e2ef;
      --text: #172033;
      --muted: #667085;
      --blue: #5b9df7;
      --blue-hover: #3f86e8;
      --green: #16a34a;
      --red: #dc2626;
      --amber: #d97706;
      --ink: #101828;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: Arial, 'Malgun Gothic', sans-serif;
      margin: 0;
      color: var(--text);
      background: var(--bg);
    }}
    main {{
      width: min(1120px, calc(100% - 48px));
      margin: 34px auto;
    }}
    .topbar {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 22px;
    }}
    h1 {{
      font-size: 25px;
      line-height: 1.35;
      margin: 0;
      letter-spacing: 0;
    }}
    .sheet-link {{
      color: var(--blue-hover);
      font-size: 14px;
      text-decoration: none;
      white-space: nowrap;
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      margin: 0 0 20px;
      flex-wrap: wrap;
    }}
    button {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      padding: 11px 16px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 700;
      min-height: 42px;
      line-height: 1.3;
    }}
    button.primary {{
      background: var(--blue);
      color: white;
      border-color: var(--blue);
    }}
    button.primary:hover {{
      background: var(--blue-hover);
      border-color: var(--blue-hover);
    }}
    button.primary.strong {{
      background: #2563eb;
      border-color: #2563eb;
    }}
    button.primary.strong:hover {{
      background: #1d4ed8;
      border-color: #1d4ed8;
    }}
    .status-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .status-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      min-height: 126px;
    }}
    .status-card h2 {{
      font-size: 14px;
      margin: 0 0 14px;
      color: var(--muted);
      font-weight: 700;
    }}
    .status-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 9px;
      font-size: 14px;
    }}
    .status-value {{
      color: var(--ink);
      font-weight: 700;
      text-align: right;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 64px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 13px;
      font-weight: 700;
      background: #eef4ff;
      color: #2563eb;
    }}
    .badge.idle {{
      background: #ecfdf3;
      color: var(--green);
    }}
    .badge.running {{
      background: #fff7ed;
      color: var(--amber);
    }}
    .badge.error {{
      background: #fef2f2;
      color: var(--red);
    }}
    .log-title {{
      font-size: 18px;
      margin: 0 0 10px;
    }}
    pre {{
      background: #101828;
      color: #edf2f7;
      border-radius: 8px;
      padding: 18px;
      min-height: 310px;
      max-height: 460px;
      overflow: auto;
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
    }}
    @media (max-width: 780px) {{
      main {{ width: min(100% - 28px, 1120px); margin: 22px auto; }}
      .topbar {{ align-items: flex-start; flex-direction: column; }}
      .status-grid {{ grid-template-columns: 1fr; }}
      button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="topbar">
      <h1>[2026 디싹] 하이웍스 제목, 기안 생성 자동화 프로그램</h1>
      <a class="sheet-link" href="{spreadsheet_url}" target="_blank">Google Sheets 열기</a>
    </div>
    <div class="toolbar">
      <button class="primary" onclick="post('/generate-titles')">하이웍스 제목 생성</button>
      <button class="primary" onclick="post('/upload-selected')">하이웍스 기안 생성</button>
      <button class="primary strong" onclick="post('/generate-and-upload')">제목, 기안 일괄 생성</button>
    </div>
    <div class="status-grid">
      <section class="status-card">
        <h2>제목 생성</h2>
        <div class="status-row">
          <span>상태</span>
          <span class="badge idle" id="runningTitles">대기중</span>
        </div>
        <div class="status-row">
          <span>마지막 실행</span>
          <span class="status-value" id="lastTitleGeneration"></span>
        </div>
      </section>
      <section class="status-card">
        <h2>기안 생성</h2>
        <div class="status-row">
          <span>상태</span>
          <span class="badge idle" id="runningUpload">대기중</span>
        </div>
        <div class="status-row">
          <span>마지막 실행</span>
          <span class="status-value" id="lastUpload"></span>
        </div>
      </section>
      <section class="status-card">
        <h2>오류 상태</h2>
        <div class="status-row">
          <span>현재 오류</span>
          <span class="badge idle" id="errorBadge">정상</span>
        </div>
        <div class="status-row">
          <span>내용</span>
          <span class="status-value" id="lastError"></span>
        </div>
      </section>
    </div>
    <h2 class="log-title">컨트롤 로그</h2>
    <pre id="logs"></pre>
  </main>
  <script>
    function setBadge(element, active) {{
      element.textContent = active ? '실행중' : '대기중';
      element.className = active ? 'badge running' : 'badge idle';
    }}

    async function post(path) {{
      const res = await fetch(path, {{ method: 'POST' }});
      const data = await res.json();
      await refresh();
    }}
    async function refresh() {{
      const res = await fetch('/status');
      const data = await res.json();
      document.getElementById('lastTitleGeneration').textContent = data.last_title_generation;
      document.getElementById('lastUpload').textContent = data.last_upload;
      setBadge(document.getElementById('runningTitles'), data.running_titles);
      setBadge(document.getElementById('runningUpload'), data.running_upload);
      document.getElementById('lastError').textContent = data.last_error || '-';
      const errorBadge = document.getElementById('errorBadge');
      errorBadge.textContent = data.last_error ? '확인 필요' : '정상';
      errorBadge.className = data.last_error ? 'badge error' : 'badge idle';
      document.getElementById('logs').textContent = data.logs.join('\\n');
    }}
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>"""
