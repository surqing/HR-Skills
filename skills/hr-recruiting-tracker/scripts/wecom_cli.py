#!/usr/bin/env python3
"""Small wrapper around the official @wecom/cli command."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


class WeComCliError(RuntimeError):
    """Raised when wecom-cli is unavailable or returns an error."""


@dataclass
class WeComCliStatus:
    available: bool
    command: str | None
    detail: str
    version: str | None = None


def find_wecom_cli() -> str | None:
    return shutil.which("wecom-cli")


def probe_wecom_cli(timeout_seconds: int = 10) -> WeComCliStatus:
    command = find_wecom_cli()
    if not command:
        return WeComCliStatus(False, None, "未找到 wecom-cli；请先运行 npm install -g @wecom/cli")

    for args in (["--version"], ["--help"]):
        try:
            proc = subprocess.run(
                [command, *args],
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except Exception as exc:
            return WeComCliStatus(False, command, f"wecom-cli 启动失败：{exc}")
        output = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode == 0:
            first_line = output.splitlines()[0] if output else command
            version = first_line if args == ["--version"] else None
            return WeComCliStatus(True, command, first_line, version=version)

    return WeComCliStatus(False, command, output[:500] if output else "wecom-cli 探测失败")


def run_wecom_cli(category: str, method: str, payload: dict[str, Any], timeout_seconds: int = 60) -> dict[str, Any]:
    command = find_wecom_cli()
    if not command:
        raise WeComCliError("未找到 wecom-cli；请先运行 npm install -g @wecom/cli")

    proc = subprocess.run(
        [command, category, method, json.dumps(payload, ensure_ascii=False)],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise WeComCliError(f"wecom-cli {category} {method} 失败 (exit={proc.returncode}): {detail[:800]}")

    output = proc.stdout.strip()
    try:
        result = json.loads(output) if output else {}
    except json.JSONDecodeError as exc:
        raise WeComCliError(f"wecom-cli 返回非 JSON: {output[:800]}") from exc

    result = unwrap_wecom_cli_result(result)

    errcode = result.get("errcode")
    if errcode not in (None, 0):
        raise WeComCliError(f"wecom-cli API 错误 {errcode}: {result.get('errmsg', '')}")
    return result


def unwrap_wecom_cli_result(result: dict[str, Any]) -> dict[str, Any]:
    """Handle both direct API JSON and the JSON-RPC envelope emitted by wecom-cli."""
    rpc_result = result.get("result")
    if not isinstance(rpc_result, dict):
        return result

    if rpc_result.get("isError"):
        raise WeComCliError(f"wecom-cli API 错误: {rpc_result}")

    content = rpc_result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
                if isinstance(decoded, dict):
                    return decoded
                return {"data": decoded}
    return rpc_result


def send_text_message(chat_type: int, chatid: str, content: str, timeout_seconds: int = 60) -> dict[str, Any]:
    return run_wecom_cli(
        "msg",
        "send_message",
        {
            "chat_type": chat_type,
            "chatid": chatid,
            "msgtype": "text",
            "text": {"content": content},
        },
        timeout_seconds=timeout_seconds,
    )


def get_messages(
    chat_type: int,
    chatid: str,
    begin_time: str,
    end_time: str,
    timeout_seconds: int = 60,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        payload: dict[str, Any] = {
            "chat_type": chat_type,
            "chatid": chatid,
            "begin_time": begin_time,
            "end_time": end_time,
        }
        if cursor:
            payload["cursor"] = cursor
        result = run_wecom_cli("msg", "get_message", payload, timeout_seconds=timeout_seconds)
        messages.extend(result.get("messages") or [])
        cursor = result.get("next_cursor")
        if not cursor:
            break
    return messages


def get_chat_list(begin_time: str, end_time: str, timeout_seconds: int = 60) -> list[dict[str, Any]]:
    chats: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        payload: dict[str, Any] = {"begin_time": begin_time, "end_time": end_time}
        if cursor:
            payload["cursor"] = cursor
        result = run_wecom_cli("msg", "get_msg_chat_list", payload, timeout_seconds=timeout_seconds)
        chats.extend(result.get("chats") or [])
        cursor = result.get("next_cursor")
        if not cursor or not result.get("has_more"):
            break
    return chats
