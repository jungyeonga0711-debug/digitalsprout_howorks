from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from .hiworks import HiworksClient, HiworksStepError
from .models import ApprovalTask
from .tasks import mark_task, selected_tasks


LogFn = Callable[[str], None]


@dataclass
class UploadStats:
    total: int = 0
    procurement: int = 0
    execution: int = 0
    completed: int = 0
    failed: int = 0

    @property
    def remaining(self) -> int:
        return max(self.total - self.completed - self.failed, 0)


def dry_run_tasks(settings: dict) -> list[str]:
    tasks = selected_tasks(settings)
    client = HiworksClient(settings, dry_run=True)
    return [f"{task.kind} {task.key}: {client._title(task)}" for task in tasks]


def upload_selected(settings: dict, log: LogFn | None = None, keep_browser_open: bool | None = None) -> UploadStats:
    logger = log or (lambda message: None)
    tasks = selected_tasks(settings)
    stats = UploadStats(
        total=len(tasks),
        procurement=sum(1 for task in tasks if task.kind == "procurement"),
        execution=sum(1 for task in tasks if task.kind == "execution"),
    )
    if not tasks:
        logger(_block("[기안 생성] 대상 없음", ["체크된 업로드 대상이 없습니다."]))
        return stats

    run_settings = deepcopy(settings)
    if keep_browser_open is not None:
        run_settings.setdefault("hiworks", {})["keep_browser_open_after_submit"] = keep_browser_open

    client = HiworksClient(run_settings)
    titles = {id(task): client._title(task) for task in tasks}
    positions = {id(task): index for index, task in enumerate(tasks, start=1)}

    logger(
        _block(
            "[기안 생성] 대상 확인",
            [
                f"전체: {stats.total}건",
                f"품의: {stats.procurement}건",
                f"집행: {stats.execution}건",
            ],
        )
    )

    def on_start(task: ApprovalTask) -> None:
        title = titles[id(task)]
        logger(
            _block(
                f"[기안 생성] 시작 ({positions[id(task)]}/{stats.total})",
                [
                    f"구분: {_kind_label(task, title)}",
                    f"제목: {title}",
                ],
            )
        )
        try:
            mark_task(run_settings, task, "기안중")
        except Exception as exc:
            stats.failed += 1
            logger(
                _block(
                    "[기안 생성] 오류",
                    [
                        "작업: 종합시트 상태 기록 실패",
                        "결과: 하이웍스 기안 시작 전 중단",
                        f"구분: {_kind_label(task, title)}",
                        f"제목: {title}",
                        f"오류: {exc}",
                        _progress_text(stats),
                    ],
                )
            )
            raise

    def on_success(task: ApprovalTask, result) -> None:
        title = titles[id(task)]
        logger(
            _block(
                "[기안 생성] 하이웍스 기안 완료",
                [
                    f"구분: {_kind_label(task, title)}",
                    f"제목: {title}",
                    f"URL: {result.url}",
                ],
            )
        )
        try:
            mark_task(run_settings, task, "기안완료", result.url)
        except Exception as exc:
            stats.failed += 1
            logger(
                _block(
                    "[기안 생성] 오류",
                    [
                        "작업: 종합시트 URL 기록 실패",
                        "결과: 하이웍스 기안은 완료됨",
                        f"구분: {_kind_label(task, title)}",
                        f"제목: {title}",
                        f"URL: {result.url}",
                        f"오류: {exc}",
                        _progress_text(stats),
                    ],
                )
            )
            raise
        stats.completed += 1
        logger(
            _block(
                "[기안 생성] 진행 현황",
                [_progress_text(stats)],
            )
        )

    def on_error(task: ApprovalTask, exc: Exception) -> None:
        title = titles.get(id(task), str(task.payload.get("title") or task.key))
        stats.failed += 1
        stage, action = _submit_error_stage(exc)
        try:
            mark_task(run_settings, task, "오류", "", str(exc))
        except Exception as mark_exc:
            logger(
                _block(
                    "[기안 생성] 종합시트 오류 기록 실패",
                    [
                        f"구분: {_kind_label(task, title)}",
                        f"제목: {title}",
                        f"원래 오류: {stage} - {exc}",
                        f"기록 오류: {mark_exc}",
                    ],
                )
            )
        logger(
            _block(
                "[기안 생성] 오류",
                [
                    f"작업: {stage}",
                    f"구분: {_kind_label(task, title)}",
                    f"제목: {title}",
                    f"오류: {exc}",
                    *([f"확인: {action}"] if action else []),
                    _progress_text(stats),
                ],
            )
        )

    try:
        client.submit_tasks(tasks, on_start=on_start, on_success=on_success, on_error=on_error)
    finally:
        logger(
            _block(
                "[기안 생성] 최종 현황",
                [_progress_text(stats)],
            )
        )
    return stats


def _kind_label(task: ApprovalTask, title: str = "") -> str:
    if "[출신]" in title:
        return "출신"
    if "[출보]" in title:
        return "출보"
    return "품의" if task.kind == "procurement" else "집행"


def _block(title: str, lines: list[str]) -> str:
    return "\n".join([title, *(f"- {line}" for line in lines)])


def _progress_text(stats: UploadStats) -> str:
    return f"현황: 전체 {stats.total}건 / 완료 {stats.completed}건 / 실패 {stats.failed}건 / 미처리 {stats.remaining}건"


def _submit_error_stage(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HiworksStepError):
        return (exc.stage, exc.action)
    message = str(exc)
    if "상세 URL" in message or "작성 화면 URL" in message:
        return ("기안 URL 확인 실패", "하이웍스에 해당 문서 기안이 정상적으로 완료됐는지 확인해주세요.")
    if "작성 화면에 그대로" in message or "기안하기" in message:
        return ("하이웍스 기안 실패", "")
    return ("하이웍스 기안 처리", "")
