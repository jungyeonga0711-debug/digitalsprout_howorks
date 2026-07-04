from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

from .models import ApprovalTask


@dataclass(frozen=True)
class SubmitResult:
    url: str


class HiworksStepError(RuntimeError):
    def __init__(self, stage: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.stage = stage
        self.action = action


class HiworksClient:
    def __init__(self, settings: dict, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.hiworks = settings.get("hiworks", {})
        self.selectors = self.hiworks.get("selectors", {})
        self.templates = self.hiworks.get("templates", {})
        self.artifacts_dir = Path(self.hiworks.get("artifacts_dir", "artifacts"))
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def submit_tasks(
        self,
        tasks: list[ApprovalTask],
        on_start: Callable[[ApprovalTask], None] | None = None,
        on_success: Callable[[ApprovalTask, SubmitResult], None] | None = None,
        on_error: Callable[[ApprovalTask, Exception], None] | None = None,
    ) -> list[tuple[ApprovalTask, SubmitResult]]:
        if self.dry_run:
            return [(task, SubmitResult(url="dry-run")) for task in tasks]

        with sync_playwright() as playwright:
            user_data_dir = self.hiworks.get("user_data_dir")
            if user_data_dir:
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=bool(self.hiworks.get("headless", False)),
                    slow_mo=int(self.hiworks.get("slow_mo_ms", 0)),
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
            else:
                browser = playwright.chromium.launch(
                    headless=bool(self.hiworks.get("headless", False)),
                    slow_mo=int(self.hiworks.get("slow_mo_ms", 0)),
                )
                page = browser.new_page()
            dialog_messages: list[str] = []
            page.on("dialog", lambda dialog: _handle_dialog(dialog, dialog_messages))
            self._login(page)
            results = []
            for task in tasks:
                if on_start:
                    on_start(task)
                try:
                    result = self._submit_one(page, task, dialog_messages)
                except Exception as exc:
                    if on_error:
                        on_error(task, exc)
                    raise
                results.append((task, result))
                if on_success:
                    on_success(task, result)
            if bool(self.hiworks.get("keep_browser_open_after_submit", True)):
                input("브라우저를 확인한 뒤 Enter를 누르면 자동화 창을 닫습니다...")
            browser.close()
            return results

    def _login(self, page: Page) -> None:
        login_url = self.hiworks.get("login_url")
        if not all(
            self.selectors.get(key)
            for key in ("login_username", "login_password", "login_submit")
        ):
            return

        if not login_url:
            raise ValueError("hiworks.login_url을 설정하세요.")

        username = os.environ.get(self.hiworks.get("username_env", "HIWORKS_ID"), "")
        password = os.environ.get(self.hiworks.get("password_env", "HIWORKS_PASSWORD"), "")
        if not username or not password:
            raise ValueError("하이웍스 ID/PW 환경변수를 설정하세요.")

        page.goto(login_url, wait_until="domcontentloaded")
        self._fill(page, "login_username", username)
        self._fill(page, "login_password", password)
        self._click(page, "login_submit")
        page.wait_for_load_state("networkidle")

    def _submit_one(self, page: Page, task: ApprovalTask, dialog_messages: list[str]) -> SubmitResult:
        dialog_messages.clear()

        try:
            self._open_write_page(page)
        except Exception as exc:
            raise _step_error(
                "작성 화면 열기 실패",
                "하이웍스 기안 작성 화면을 열지 못했습니다.",
                "",
                exc,
            ) from exc

        try:
            title = self._title(task)
        except Exception as exc:
            raise _step_error(
                "제목 만들기 실패",
                "하이웍스 제목을 만들지 못했습니다.",
                "",
                exc,
            ) from exc

        is_travel = _is_travel_title(title)
        try:
            form_label = self._form_label(task, title)
            form_select = self.selectors.get("form_select")
            if form_label and form_select:
                page.locator(form_select).select_option(label=form_label)
                page.wait_for_load_state("networkidle")
        except Exception as exc:
            raise _step_error(
                "문서 양식 선택 실패",
                f"하이웍스 문서 양식 '{form_label}'을 선택하지 못했습니다.",
                "",
                exc,
            ) from exc

        try:
            self._fill(page, "title_input", title)
        except Exception as exc:
            raise _step_error(
                "제목 입력 실패",
                "하이웍스 제목 칸에 제목을 입력하지 못했습니다.",
                "",
                exc,
            ) from exc

        if not is_travel:
            try:
                self._fill_body(page, self._body(task))
            except Exception as exc:
                raise _step_error(
                    "본문 작성 실패",
                    "하이웍스 본문 내용을 입력하지 못했습니다.",
                    "",
                    exc,
                ) from exc
            try:
                self._fill_existing_form_fields(page, task)
            except Exception as exc:
                raise _step_error(
                    "본문 양식 입력 실패",
                    "하이웍스 본문 표의 금액/예산 칸을 채우지 못했습니다.",
                    "",
                    exc,
                ) from exc

        try:
            self._add_references(page)
        except Exception as exc:
            raise _step_error(
                "참조자 등록 실패",
                "하이웍스 참조자를 등록하지 못했습니다.",
                "",
                exc,
            ) from exc

        before_url = page.url
        try:
            self._click(page, "submit_button")
        except Exception as exc:
            raise _step_error(
                "하이웍스 기안 실패",
                "기안하기 버튼을 누르지 못했습니다.",
                "",
                exc,
            ) from exc

        try:
            confirm_selector = self.selectors.get("submit_confirm_button")
            if confirm_selector:
                page.locator(confirm_selector).click()
        except Exception as exc:
            raise _step_error(
                "하이웍스 기안 확인 실패",
                "기안 확인 버튼을 처리하지 못했습니다.",
                "",
                exc,
            ) from exc

        try:
            wait_ms = int(self.hiworks.get("after_submit_wait_ms", 5000))
            page.wait_for_timeout(wait_ms)
            page.wait_for_load_state("domcontentloaded")
            screenshot_path = self.artifacts_dir / f"submit_{task.kind}_{task.row_number}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as exc:
            raise _step_error(
                "기안 결과 확인 실패",
                "기안 후 하이웍스 화면 상태를 확인하지 못했습니다.",
                "",
                exc,
            ) from exc

        if page.url == before_url:
            if dialog_messages:
                raise HiworksStepError(
                    "하이웍스 기안 실패",
                    "하이웍스가 기안을 완료하지 않았습니다. " + " / ".join(dialog_messages),
                    "",
                )
            raise HiworksStepError(
                "하이웍스 기안 실패",
                f"기안하기를 눌렀지만 하이웍스가 기안을 완료하지 않았습니다. 스크린샷: {screenshot_path}",
                "",
            )
        if "/approval/document/write" in page.url:
            if dialog_messages:
                raise HiworksStepError(
                    "하이웍스 기안 실패",
                    "하이웍스가 기안을 완료하지 않았습니다. " + " / ".join(dialog_messages),
                    "",
                )
            raise HiworksStepError(
                "하이웍스 기안 실패",
                f"기안하기를 눌렀지만 작성 화면으로 다시 돌아왔습니다. 스크린샷: {screenshot_path}",
                "",
            )
        try:
            document_url = self._resolve_submitted_document_url(page, task, title)
        except Exception as exc:
            raise _step_error(
                "기안 URL 확인 실패",
                "기안 완료 후 문서 URL을 확인하지 못했습니다.",
                "하이웍스에 해당 문서 기안이 정상적으로 완료됐는지 확인해주세요.",
                exc,
            ) from exc
        return SubmitResult(url=document_url)

    def _title(self, task: ApprovalTask) -> str:
        custom_title = str(task.payload.get("title") or "").strip()
        if custom_title:
            return _hiworks_edit_title(custom_title)
        template_key = "procurement_title" if task.kind == "procurement" else "execution_title"
        template = self.templates.get(template_key, "[{sheet_code}] {vendor} {kind} ({amount})")
        return _hiworks_edit_title(template.format(kind=task.kind, **_default_payload(task.payload)))

    def _form_label(self, task: ApprovalTask, title: str) -> str:
        travel_label = _travel_title_label(title)
        if travel_label == "출신":
            return self.selectors.get("travel_request_form_label", "출장신청서(신규)")
        if travel_label == "출보":
            return self.selectors.get("travel_report_form_label", "출장보고서")
        form_label_key = "procurement_form_label" if task.kind == "procurement" else "execution_form_label"
        return self.selectors.get(form_label_key, "")

    def _resolve_submitted_document_url(self, page: Page, task: ApprovalTask, title: str) -> str:
        list_url = page.url
        if _is_document_view_url(list_url):
            return list_url

        opened = bool(
            page.locator("body").evaluate(
                """() => {
                    const visible = (element) => {
                        const rect = element.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0
                            && rect.bottom >= 0 && rect.right >= 0
                            && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
                    };
                    const clickTarget = (element) => {
                        const link = element.querySelector('a[href]') || element;
                        link.click();
                        return true;
                    };
                    const tableRows = Array.from(document.querySelectorAll('table tbody tr, tbody tr, tr'))
                        .filter(visible)
                        .filter((row) => {
                            const text = (row.innerText || row.textContent || '').trim();
                            return text && !text.includes('문서 번호') && !text.includes('제목') && !text.includes('기안자');
                        });
                    if (tableRows.length) return clickTarget(tableRows[0]);

                    const titleLinks = Array.from(document.querySelectorAll('a, div, span'))
                        .filter(visible)
                        .filter((element) => {
                            const text = (element.innerText || element.textContent || '').trim();
                            return text.startsWith('[') || text.includes('260515-ED-03');
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.top - br.top) || (ar.left - br.left);
                        });
                    if (titleLinks.length) {
                        let node = titleLinks[0];
                        for (let depth = 0; node && depth < 5; depth += 1) {
                            if (node.tagName === 'TR') return clickTarget(node);
                            node = node.parentElement;
                        }
                        return clickTarget(titleLinks[0]);
                    }
                    return false;
                }"""
            )
        )
        if not opened:
            raise RuntimeError(f"기안 완료 후 목록 최상단 문서 행을 찾지 못했습니다. 목록 URL: {list_url}")
        try:
            page.wait_for_url(re.compile(r".*/approval/document/view/\d+.*"), timeout=10000)
        except Exception as exc:
            raise RuntimeError(f"목록 최상단 문서를 클릭했지만 상세 화면으로 이동하지 못했습니다. 목록 URL: {list_url}") from exc
        document_url = page.url
        if not _is_document_view_url(document_url):
            raise RuntimeError(f"기안 완료 상세 URL 형식이 아닙니다: {document_url}")
        return document_url

    def _open_write_page(self, page: Page) -> None:
        if "/approval/document/write" in page.url:
            return
        try:
            clicked = bool(
                page.locator("body").evaluate(
                    """() => {
                        const visible = (element) => {
                            const rect = element.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0
                                && rect.bottom >= 0 && rect.right >= 0
                                && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
                        };
                        const candidates = Array.from(document.querySelectorAll('a, button'))
                            .filter((element) => visible(element))
                            .filter((element) => {
                                const text = (element.innerText || element.textContent || '').trim();
                                const href = element.getAttribute('href') || '';
                                return text.includes('작성하기') || href.includes('/approval/document/write');
                            })
                            .sort((a, b) => {
                                const ar = a.getBoundingClientRect();
                                const br = b.getBoundingClientRect();
                                return (ar.left + ar.top) - (br.left + br.top);
                            });
                        if (!candidates.length) return false;
                        candidates[0].click();
                        return true;
                    }"""
                )
            )
            if clicked:
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1500)
                if "/approval/document/write" in page.url:
                    return
        except Exception:
            pass

        approval_url = self.selectors.get("new_approval_url")
        if not approval_url:
            raise ValueError("hiworks.selectors.new_approval_url을 설정하세요.")
        page.goto(approval_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)

    def _body(self, task: ApprovalTask) -> str:
        template_key = "procurement_body" if task.kind == "procurement" else "execution_body"
        template = self.templates.get(template_key, "{sheet_name}\n{vendor}\n{amount}")
        return template.format(**_default_payload(task.payload))

    def _fill(self, page: Page, selector_key: str, value: str) -> None:
        selector = self.selectors.get(selector_key)
        if not selector:
            raise ValueError(f"hiworks.selectors.{selector_key}을 설정하세요.")
        page.locator(selector).fill(value)

    def _click(self, page: Page, selector_key: str) -> None:
        selector = self.selectors.get(selector_key)
        if not selector:
            raise ValueError(f"hiworks.selectors.{selector_key}을 설정하세요.")
        page.locator(selector).click()

    def _fill_body(self, page: Page, body: str) -> None:
        selector = self.selectors.get("body_input")
        if selector:
            page.locator(selector).fill(body)
            return

        source_button = self.selectors.get("body_source_button")
        source_textarea = self.selectors.get("body_source_textarea")
        if source_button and source_textarea:
            page.locator(source_button).click()
            page.locator(source_textarea).fill(_text_to_html(body))
            return

        frame_selector = self.selectors.get("body_frame")
        editable_selector = self.selectors.get("body_editable")
        if frame_selector:
            target = page.frame_locator(frame_selector).locator(editable_selector or "body")
            mode = self.hiworks.get("body_mode", "prepend")
            html = _text_to_html(body)
            if mode == "replace":
                target.evaluate(
                    """(element, html) => {
                        element.innerHTML = html;
                        element.style.textAlign = 'center';
                        element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
                    }""",
                    html,
                )
            else:
                target.evaluate(
                    """(element, html) => {
                        const wrapper = document.createElement('div');
                        wrapper.innerHTML = html + '<hr><br>';
                        wrapper.style.textAlign = 'center';
                        element.prepend(wrapper);
                        element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
                    }""",
                    html,
                )
            return

        raise ValueError(
            "본문 입력 selector가 필요합니다. body_input 또는 "
            "body_source_button/body_source_textarea 또는 body_frame/body_editable을 설정하세요."
        )

    def _fill_existing_form_fields(self, page: Page, task: ApprovalTask) -> None:
        frame_selector = self.selectors.get("body_frame")
        if not frame_selector:
            return
        payload = _default_payload(task.payload)
        fields = {
            "매입처": payload["vendor"],
            "견적번호": payload["estimate_number"],
            "귀속견적번호": payload["estimate_number"],
            "매입금액": payload["amount"],
            "매입 금액": payload["amount"],
            "총예산": payload["total_budget"],
            "총 예산": payload["total_budget"],
            "잔여예산": payload["remaining_budget"],
            "잔여 예산": payload["remaining_budget"],
            "대금지급일": "",
            "대금 지급일": "",
        }
        page.frame_locator(frame_selector).locator("body").evaluate(
            """(body, fields) => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, '');
                const setCell = (cell, value) => {
                    cell.innerHTML = String(value || '').replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    cell.style.fontFamily = 'Malgun Gothic, 맑은 고딕, sans-serif';
                    cell.style.fontSize = '16px';
                    cell.style.textAlign = 'center';
                    cell.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
                };
                body.style.fontFamily = 'Malgun Gothic, 맑은 고딕, sans-serif';
                body.style.fontSize = '16px';
                for (const element of body.querySelectorAll('*')) {
                    element.style.fontFamily = 'Malgun Gothic, 맑은 고딕, sans-serif';
                    element.style.fontSize = '16px';
                }
                for (const row of body.querySelectorAll('tr')) {
                    const cells = Array.from(row.children).filter(
                        (cell) => ['TD', 'TH'].includes(cell.tagName)
                    );
                    for (let index = 0; index < cells.length; index += 1) {
                        const label = normalize(cells[index].innerText);
                        for (const [name, value] of Object.entries(fields)) {
                            const wanted = normalize(name);
                            if (!wanted || !label.includes(wanted)) continue;
                            const target = cells[index + 1];
                            if (target) setCell(target, value);
                        }
                    }
                }
            }""",
            fields,
        )

    def _add_references(self, page: Page) -> None:
        names = [str(name).strip() for name in self.hiworks.get("references", []) if str(name).strip()]
        if not names:
            return
        selector = self._reference_input_selector(page)
        if not selector:
            raise RuntimeError("참조 입력칸을 찾을 수 없습니다.")
        locator = page.locator(selector)
        if locator.count() == 0:
            raise RuntimeError(f"참조 입력칸을 찾을 수 없습니다: {selector}")
        for name in names:
            if self._reference_area_contains(page, selector, name):
                continue
            self._add_reference_name(page, selector, name)
            if not self._reference_area_contains(page, selector, name):
                screenshot_path = self.artifacts_dir / f"reference_missing_{name}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                raise RuntimeError(f"참조자 '{name}' 입력 확인 실패. 기안하지 않고 멈췄습니다. 스크린샷: {screenshot_path}")

    def _reference_input_selector(self, page: Page) -> str:
        selector = self.selectors.get("reference_input")
        if selector and page.locator(selector).count() > 0:
            return selector

        fallback_selector = "[data-hiworks-reference-input='true']"
        found = bool(
            page.locator("body").evaluate(
                """() => {
                    const visible = (element) => {
                        const rect = element.getBoundingClientRect();
                        const style = window.getComputedStyle(element);
                        return rect.width > 0 && rect.height > 0
                            && style.visibility !== 'hidden'
                            && style.display !== 'none';
                    };
                    const normalize = (value) => String(value || '').replace(/\\s+/g, '');
                    for (const element of document.querySelectorAll('[data-hiworks-reference-input]')) {
                        element.removeAttribute('data-hiworks-reference-input');
                    }
                    const inputs = Array.from(document.querySelectorAll(
                        'input, textarea, [contenteditable="true"]'
                    )).filter((element) => visible(element) && !element.disabled && !element.readOnly);
                    const scored = inputs.map((element) => {
                        let node = element;
                        let depth = 0;
                        let referenceAncestor = null;
                        while (node && depth < 8) {
                            const text = normalize(node.innerText || node.textContent || '');
                            if (text.includes('참조')) {
                                referenceAncestor = node;
                                break;
                            }
                            node = node.parentElement;
                            depth += 1;
                        }
                        const placeholder = normalize(element.getAttribute('placeholder'));
                        const text = normalize(element.innerText || element.textContent || element.value || '');
                        let score = 0;
                        if (referenceAncestor) score += 100 - depth;
                        if (placeholder.includes('클릭후입력') || placeholder.includes('입력')) score += 20;
                        if (text.includes('클릭후입력')) score += 20;
                        return {element, score};
                    }).filter((item) => item.score > 0).sort((a, b) => b.score - a.score);
                    if (!scored.length) return false;
                    scored[0].element.setAttribute('data-hiworks-reference-input', 'true');
                    return true;
                }"""
            )
        )
        return fallback_selector if found and page.locator(fallback_selector).count() > 0 else ""

    def _add_reference_name(self, page: Page, selector: str, name: str) -> None:
        wait_ms = int(self.hiworks.get("reference_add_wait_ms", 700))
        for attempt in range(3):
            target = page.locator(selector).first
            target.wait_for(state="visible", timeout=5000)
            target.click()
            target.fill("")
            page.wait_for_timeout(100)
            target.fill(name)
            target.evaluate(
                """(input) => {
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'Process'}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                }"""
            )
            page.wait_for_timeout(wait_ms)
            if self._click_reference_candidate(page, selector, name):
                page.wait_for_timeout(wait_ms)
            elif attempt == 0:
                target.press("Enter")
            else:
                target.press("ArrowDown")
                page.wait_for_timeout(200)
                target.press("Enter")
            page.wait_for_timeout(wait_ms)
            if self._reference_area_contains(page, selector, name):
                return

    def _click_reference_candidate(self, page: Page, selector: str, name: str) -> bool:
        if page.locator(selector).count() == 0:
            return False
        return bool(
            page.locator(selector).first.evaluate(
                """(input, name) => {
                    const inputRect = input.getBoundingClientRect();
                    const candidates = Array.from(document.querySelectorAll('li, div, a, span, button'))
                        .map((element) => {
                            const rect = element.getBoundingClientRect();
                            const text = (element.innerText || element.textContent || '').trim();
                            const visible = rect.width > 0 && rect.height > 0
                                && rect.bottom >= 0 && rect.right >= 0
                                && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
                            return {element, rect, text, visible};
                        })
                        .filter((item) => {
                            if (!item.visible || !item.text.includes(name)) return false;
                            if (item.element === input) return false;
                            const nearInput = item.rect.top >= inputRect.top - 20
                                && item.rect.left >= inputRect.left - 120
                                && item.rect.left <= inputRect.right + 360
                                && item.rect.top <= inputRect.bottom + 260;
                            return nearInput;
                        })
                        .sort((a, b) => {
                            const aDistance = Math.abs(a.rect.top - inputRect.bottom) + Math.abs(a.rect.left - inputRect.left);
                            const bDistance = Math.abs(b.rect.top - inputRect.bottom) + Math.abs(b.rect.left - inputRect.left);
                            return aDistance - bDistance;
                        });
                    if (!candidates.length) return false;
                    candidates[0].element.click();
                    return true;
                }""",
                name,
            )
        )

    def _reference_area_contains(self, page: Page, selector: str, name: str) -> bool:
        if page.locator(selector).count() == 0:
            return False
        text = page.locator(selector).first.evaluate(
            """(input) => {
                let node = input;
                for (let depth = 0; node && depth < 8; depth += 1) {
                    const text = node.innerText || node.textContent || '';
                    if (text.includes('참조')) return text;
                    node = node.parentElement;
                }
                node = input.parentElement;
                return node ? (node.innerText || node.textContent || '') : '';
            }"""
        )
        return name in str(text)


def _default_payload(payload: dict) -> dict:
    keys = [
        "sheet_code",
        "sheet_name",
        "source_row",
        "date",
        "vendor",
        "amount",
        "evidence",
        "code",
        "number",
        "note",
        "supply_amount",
        "vat",
        "usage",
        "payment_method",
        "school",
        "remaining_budget",
        "total_budget",
        "estimate_number",
    ]
    result = {key: payload.get(key, "") for key in keys}
    result["estimate_number"] = result.get("estimate_number") or "260515-ED-03"
    result["total_budget"] = result.get("total_budget") or "240,000,000"
    return result


def _is_document_view_url(url: str) -> bool:
    return bool(re.search(r"/approval/document/view/\d+", url or ""))


def _step_error(stage: str, message: str, action: str, exc: Exception) -> HiworksStepError:
    detail = str(exc).strip()
    full_message = f"{message} 원인: {detail}" if detail else message
    return HiworksStepError(stage, full_message, action)


def _hiworks_edit_title(title: str) -> str:
    title = title.strip()
    return title if title.startswith("X / ") else f"X / {title}"


def _travel_title_label(title: str) -> str:
    match = re.search(r"\[(출신|출보)\]", title or "")
    return match.group(1) if match else ""


def _is_travel_title(title: str) -> bool:
    return bool(_travel_title_label(title))


def _text_to_html(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        "<div style=\"font-family:'Malgun Gothic','맑은 고딕',sans-serif;font-size:16px;text-align:center;\">"
        + escaped.replace("\n", "<br>")
        + "</div>"
    )


def _handle_dialog(dialog, messages: list[str]) -> None:
    messages.append(dialog.message)
    dialog.accept()
