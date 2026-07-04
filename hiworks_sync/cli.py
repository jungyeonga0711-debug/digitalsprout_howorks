from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .config import load_settings
from .control_panel import run_control_panel
from .hiworks import HiworksClient
from .inspect import inspect_hiworks
from .summary import sync_summary
from .tasks import mark_task, selected_tasks
from .title_manager import generate_hiworks_titles
from .uploader import upload_selected


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="hiworks_sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-summary", help="A~Q 시트에서 하이웍스_종합을 갱신합니다.")
    sync_parser.add_argument("--settings", default="config/settings.yml")

    title_parser = subparsers.add_parser("generate-titles", help="A~Q 시트에 하이웍스 제목을 생성하고 전체 행 변경 로그를 남깁니다.")
    title_parser.add_argument("--settings", default="config/settings.yml")

    upload_parser = subparsers.add_parser("upload-selected", help="종합시트에서 체크된 항목을 하이웍스에 기안합니다.")
    upload_parser.add_argument("--settings", default="config/settings.yml")
    upload_parser.add_argument("--dry-run", action="store_true")

    inspect_parser = subparsers.add_parser("inspect-hiworks", help="하이웍스 화면 요소를 추출합니다.")
    inspect_parser.add_argument("--url", required=True)
    inspect_parser.add_argument("--wait", type=int, default=120)
    inspect_parser.add_argument("--output-dir", default="artifacts")

    panel_parser = subparsers.add_parser("control-panel", help="하이웍스 제목 생성 컨트롤 패널을 실행합니다.")
    panel_parser.add_argument("--settings", default="config/settings.yml")
    panel_parser.add_argument("--host", default="127.0.0.1")
    panel_parser.add_argument("--port", type=int, default=8765)
    panel_parser.add_argument("--sync-interval", type=int, default=15)

    args = parser.parse_args(argv)

    if args.command == "inspect-hiworks":
        elements_path = inspect_hiworks(args.url, args.wait, args.output_dir)
        print(f"하이웍스 화면 요소 저장 완료: {elements_path}")
        return 0

    settings = load_settings(args.settings)

    if args.command == "sync-summary":
        count = sync_summary(settings)
        print(f"하이웍스_종합 갱신 완료: {count}건")
        return 0

    if args.command == "generate-titles":
        result = generate_hiworks_titles(settings)
        print(
            "하이웍스 제목 생성 완료: "
            f"작업결과 품의 {result.get('titles_procurement', 0)}건 / "
            f"집행 {result.get('titles_execution', 0)}건"
        )
        return 0

    if args.command == "upload-selected":
        if args.dry_run:
            tasks = selected_tasks(settings)
            if not tasks:
                print("업로드 대상이 없습니다.")
                return 0
            client = HiworksClient(settings, dry_run=True)
            for task in tasks:
                print(f"[dry-run] {task.kind} {task.key}: {client._title(task)}")
            return 0

        stats = upload_selected(settings, print)
        print(
            "하이웍스 기안 생성 완료: "
            f"총 {stats.total}건 중 완료 {stats.completed}건, "
            f"실패 {stats.failed}건, 미처리 {stats.remaining}건"
        )
        return 0

    if args.command == "control-panel":
        run_control_panel(settings, args.host, args.port, args.sync_interval)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
