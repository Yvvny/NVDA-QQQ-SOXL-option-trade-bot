from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket

DEVTOOLS_LIST_URL = "http://127.0.0.1:9222/json/list"
DEVTOOLS_NEW_URL = "http://127.0.0.1:9222/json/new?https://chatgpt.com/"


class CdpSession:
    def __init__(self, websocket_url: str) -> None:
        self.ws = websocket.create_connection(websocket_url, timeout=60)
        self._message_id = 1

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self._message_id
        self._message_id += 1
        self.ws.send(
            json.dumps(
                {"id": message_id, "method": method, "params": params or {}},
                ensure_ascii=False,
            )
        )
        while True:
            payload = json.loads(self.ws.recv())
            if payload.get("id") == message_id:
                if "exceptionDetails" in payload:
                    raise RuntimeError(json.dumps(payload["exceptionDetails"], ensure_ascii=False))
                if "error" in payload:
                    raise RuntimeError(json.dumps(payload["error"], ensure_ascii=False))
                return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--new-chat", action="store_true")
    parser.add_argument("--tab-title-contains")
    parser.add_argument("--extract-only", action="store_true")
    args = parser.parse_args()

    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.tab_title_contains:
        page = _chatgpt_page_by_title(args.tab_title_contains)
    elif args.new_chat:
        page = _new_chatgpt_page()
    else:
        page = _chatgpt_page()
    page_websocket_url = str(page["webSocketDebuggerUrl"])
    session = CdpSession(page_websocket_url)
    try:
        session.call("Runtime.enable")
        session.call("Input.setIgnoreInputEvents", {"ignore": False})
        if args.extract_only:
            response = _last_assistant_response(session)
        else:
            if args.new_chat:
                _wait_for_composer(session)
            before_count = len(_messages(session))
            _send_prompt(session, prompt)
            response = _wait_for_response(
                session,
                before_count=before_count,
                timeout_seconds=args.timeout_seconds,
            )
    finally:
        session.close()

    output_path.write_text(response, encoding="utf-8")
    print(
        json.dumps(
            {
                "output_file": str(output_path),
                "chars": len(response),
                "tab_id": page.get("id"),
                "tab_title": page.get("title"),
                "tab_url": page.get("url"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _chatgpt_page() -> dict[str, Any]:
    tabs = json.loads(urllib.request.urlopen(DEVTOOLS_LIST_URL, timeout=10).read().decode())
    for tab in reversed(tabs):
        if tab.get("type") == "page" and "chatgpt.com" in str(tab.get("url", "")):
            return dict(tab)
    raise RuntimeError("No chatgpt.com page found on DevTools port 9222.")


def _chatgpt_page_by_title(title_contains: str) -> dict[str, Any]:
    tabs = json.loads(urllib.request.urlopen(DEVTOOLS_LIST_URL, timeout=10).read().decode())
    needle = title_contains.lower()
    for tab in reversed(tabs):
        title = str(tab.get("title", ""))
        url = str(tab.get("url", ""))
        if tab.get("type") == "page" and "chatgpt.com" in url and needle in title.lower():
            return dict(tab)
    raise RuntimeError(f"No chatgpt.com page title contains {title_contains!r}.")


def _new_chatgpt_page() -> dict[str, Any]:
    request = urllib.request.Request(DEVTOOLS_NEW_URL, method="PUT")
    tab = json.loads(urllib.request.urlopen(request, timeout=10).read().decode())
    return dict(tab)


def _wait_for_composer(session: CdpSession) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if _composer_ready(session):
            return
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for ChatGPT composer.")


def _composer_ready(session: CdpSession) -> bool:
    result = session.call(
        "Runtime.evaluate",
        {
            "expression": (
                "!!document.querySelector('div[contenteditable=\"true\"][role=\"textbox\"], "
                "textarea[aria-label]')"
            ),
            "returnByValue": True,
        },
    )
    return bool(_runtime_value(result))


def _send_prompt(session: CdpSession, prompt: str) -> None:
    focus_result = session.call(
        "Runtime.evaluate",
        {
            "expression": (
                "(() => {"
                "const box = document.querySelector("
                "'div[contenteditable=\"true\"][role=\"textbox\"], textarea[aria-label]'"
                ");"
                "if (!box) return false;"
                "box.focus();"
                "return true;"
                "})()"
            ),
            "returnByValue": True,
        },
    )
    if not _runtime_value(focus_result):
        raise RuntimeError("ChatGPT composer textbox was not found.")

    # Insert in chunks to avoid dropping large prompts through CDP.
    for index in range(0, len(prompt), 4000):
        session.call("Input.insertText", {"text": prompt[index : index + 4000]})
        time.sleep(0.05)

    clicked_send = session.call(
        "Runtime.evaluate",
        {
            "expression": (
                "(() => {"
                "const buttons = [...document.querySelectorAll('button')];"
                "const send = buttons.find(b => {"
                "const label = ["
                "b.innerText || '',"
                "b.getAttribute('aria-label') || '',"
                "b.getAttribute('data-testid') || ''"
                "].join(' ');"
                "return /send|发送/i.test(label) && !b.disabled;"
                "});"
                "if (!send) return false;"
                "send.click();"
                "return true;"
                "})()"
            ),
            "returnByValue": True,
        },
    )
    if _runtime_value(clicked_send):
        return

    session.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
            "key": "Enter",
            "code": "Enter",
            "unmodifiedText": "\r",
            "text": "\r",
        },
    )
    session.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
            "key": "Enter",
            "code": "Enter",
        },
    )


def _wait_for_response(
    session: CdpSession,
    *,
    before_count: int,
    timeout_seconds: int,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_response = ""
    stable_since: float | None = None
    while time.monotonic() < deadline:
        messages = _messages(session)
        assistant_messages = [
            message
            for message in messages[before_count:]
            if message.get("role") == "assistant"
        ]
        if assistant_messages:
            current = str(assistant_messages[-1].get("text") or "").strip()
            streaming = _streaming(session)
            if current and current != "正在思考":
                if current != last_response:
                    last_response = current
                    stable_since = time.monotonic()
                elif not streaming and stable_since and time.monotonic() - stable_since >= 3:
                    return current
        time.sleep(1)
    if last_response:
        return last_response
    raise TimeoutError("Timed out waiting for ChatGPT browser response.")


def _messages(session: CdpSession) -> list[dict[str, str]]:
    result = session.call(
        "Runtime.evaluate",
        {
            "expression": (
                "(() => [...document.querySelectorAll('[data-message-author-role]')]"
                ".map(n => ({"
                "role: n.getAttribute('data-message-author-role'),"
                "text: n.innerText || ''"
                "})))()"
            ),
            "returnByValue": True,
        },
    )
    value = _runtime_value(result)
    return value if isinstance(value, list) else []


def _last_assistant_response(session: CdpSession) -> str:
    assistant_messages = [
        message for message in _messages(session) if message.get("role") == "assistant"
    ]
    if not assistant_messages:
        raise RuntimeError("No assistant messages found in selected ChatGPT tab.")
    return str(assistant_messages[-1].get("text") or "").strip()


def _streaming(session: CdpSession) -> bool:
    result = session.call(
        "Runtime.evaluate",
        {
            "expression": (
                "(() => [...document.querySelectorAll('button')]"
                ".some(b => /停止|Stop/.test(b.innerText || b.getAttribute('aria-label') || ''))"
                ")()"
            ),
            "returnByValue": True,
        },
    )
    return bool(_runtime_value(result))


def _runtime_value(payload: dict[str, Any]) -> Any:
    return payload.get("result", {}).get("result", {}).get("value")


if __name__ == "__main__":
    raise SystemExit(main())
