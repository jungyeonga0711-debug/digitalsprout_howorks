from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright


def inspect_hiworks(url: str, wait_seconds: int, output_dir: str = "artifacts") -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    elements_path = output_path / "hiworks_elements.txt"
    screenshot_path = output_path / "hiworks_page.png"

    with sync_playwright() as playwright:
        user_data_dir = str(output_path / "browser-profile")
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            slow_mo=80,
        )
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print()
        print("브라우저가 열렸습니다.")
        print("1. 하이웍스에 로그인하세요.")
        print("2. 실제 기안 작성 화면까지 이동하세요.")
        print(f"3. {wait_seconds}초 동안 기다린 뒤 화면 요소를 저장합니다.")
        print()
        page.wait_for_timeout(wait_seconds * 1000)
        page.screenshot(path=str(screenshot_path), full_page=True)
        elements_path.write_text(_collect_elements(page), encoding="utf-8")
        browser.close()

    return elements_path


def _collect_elements(page: Page) -> str:
    rows = page.locator("input, textarea, select, button, a, [contenteditable='true'], iframe").evaluate_all(
        """
        elements => elements.map((el, index) => {
          const rect = el.getBoundingClientRect();
          const label = el.labels && el.labels.length
            ? Array.from(el.labels).map(item => item.innerText).join(' / ')
            : '';
          const options = el.tagName.toLowerCase() === 'select'
            ? Array.from(el.options).map(option => ({
                text: option.text.trim(),
                value: option.value,
                selected: option.selected
              }))
            : [];
          return {
            index,
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            text: (el.innerText || el.value || '').trim().slice(0, 120),
            label: label.trim().slice(0, 120),
            placeholder: (el.getAttribute('placeholder') || '').trim(),
            name: el.getAttribute('name') || '',
            id: el.id || '',
            className: typeof el.className === 'string' ? el.className : '',
            href: el.getAttribute('href') || '',
            src: el.getAttribute('src') || '',
            contenteditable: el.getAttribute('contenteditable') || '',
            options,
            visible: rect.width > 0 && rect.height > 0,
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
          };
        })
        """
    )

    lines = [
        f"URL: {page.url}",
        "",
        "아래 목록에서 제목 입력칸, 본문 입력칸, 기안하기 버튼 후보를 찾으면 됩니다.",
        "selector 후보는 id/name/placeholder/text를 보고 config/settings.yml에 옮겨 적습니다.",
        "",
    ]

    for item in rows:
        if not item.get("visible"):
            continue
        lines.append(f"[{item['index']}] <{item['tag']}> type={item['type']}")
        for key in ("text", "label", "placeholder", "name", "id", "className", "href", "src", "contenteditable"):
            value = str(item.get(key) or "").strip()
            if value:
                lines.append(f"  {key}: {value}")
        options = item.get("options") or []
        for option in options:
            selected = " selected" if option.get("selected") else ""
            lines.append(f"  option{selected}: value={option.get('value')} text={option.get('text')}")
        lines.append(
            f"  box: x={item['x']} y={item['y']} w={item['width']} h={item['height']}"
        )
        lines.append("")

    lines.extend(_collect_frames(page))
    return "\n".join(lines)


def _collect_frames(page: Page) -> list[str]:
    lines = ["", "IFRAMES", ""]
    for frame_index, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        lines.append(f"[frame {frame_index}] {frame.url}")
        try:
            frame_rows = frame.locator("body, input, textarea, [contenteditable='true']").evaluate_all(
                """
                elements => elements.map((el, index) => {
                  const rect = el.getBoundingClientRect();
                  return {
                    index,
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || el.value || '').trim().slice(0, 300),
                    placeholder: (el.getAttribute('placeholder') || '').trim(),
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    className: typeof el.className === 'string' ? el.className : '',
                    contenteditable: el.getAttribute('contenteditable') || '',
                    visible: rect.width > 0 && rect.height > 0,
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                  };
                })
                """
            )
        except Exception as exc:
            lines.append(f"  frame read error: {exc}")
            continue

        for item in frame_rows:
            if not item.get("visible"):
                continue
            lines.append(f"  [{item['index']}] <{item['tag']}>")
            for key in ("text", "placeholder", "name", "id", "className", "contenteditable"):
                value = str(item.get(key) or "").strip()
                if value:
                    lines.append(f"    {key}: {value}")
            lines.append(
                f"    box: x={item['x']} y={item['y']} w={item['width']} h={item['height']}"
            )
    return lines
