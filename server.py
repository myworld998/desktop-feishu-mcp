#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send a Feishu confirmation card and return its reply through MCP stdio."""

import json
import os
import subprocess
import sys
import time

from configuration import ConfigurationError, load_config, run_cli

CONFIG = load_config()
USER_OPEN_ID = CONFIG["user_open_id"]
CHAT_ID = CONFIG["chat_id"]
RESULTS_DIR = CONFIG["results_dir"]
CARDS_DIR = CONFIG["cards_dir"]
# 轮询参数
POLL_INTERVAL_SEC = 2        # 每 2 秒查一次新消息/按钮结果
DEFAULT_TIMEOUT_SEC = 600    # 默认等 10 分钟


# ---------------------------------------------------------------------------
# lark-cli 调用封装
# ---------------------------------------------------------------------------
def _lark_cli(args, timeout=30):
    """Delegate authentication and execution to lark-cli; never inspect its profile."""
    proc = run_cli(CONFIG, args, timeout=timeout)
    return proc.stdout, proc.stderr, proc.returncode

def _lark_json(args, timeout=30):
    out, err, code = _lark_cli(args, timeout=timeout)
    try:
        data = json.loads(out)
        if isinstance(data, dict) and data.get("ok") is False:
            code = code or 1
        return data, err, code
    except Exception:
        return None, err, code


def _list_messages(order="desc", page_size=10):
    data, err, code = _lark_json(
        ["im", "+chat-messages-list",
         "--chat-id", CHAT_ID,
         "--order", order,
         "--page-size", str(page_size),
         "--as", CONFIG["message_identity"],
         "--no-reactions"]
    )
    if code != 0 or not data:
        return None
    return data.get("data", {}).get("messages", [])


def _latest_position():
    msgs = _list_messages(order="desc", page_size=1)
    if not msgs:
        return None
    try:
        return int(msgs[0].get("message_position", "0"))
    except (ValueError, TypeError):
        return None


# 卡片等级 → header 颜色/图标/标题文案
_LEVEL_META = {
    "notice":  {"emoji": "ℹ️", "template": "blue",   "label": "Desktop 需要你确认"},
    "warning": {"emoji": "⚠️", "template": "orange", "label": "Desktop 需要你确认"},
    "urgent":  {"emoji": "⚠️", "template": "orange", "label": "Desktop 需要你确认"},
}


def _send_message(markdown, as_identity="bot"):
    data, err, code = _lark_json(
        ["im", "+messages-send",
         "--chat-id", CHAT_ID,
         "--as", as_identity,
         "--markdown", markdown]
    )
    return code == 0 and bool(data)


_OPTION_NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _render_options(options):
    if not options:
        return None
    lines = []
    for i, opt in enumerate(options):
        tag = _OPTION_NUMS[i] if i < len(_OPTION_NUMS) else f"{i + 1}."
        lines.append(f"**{tag} {opt}**")
    return "**请选择（回复编号或直接回复内容）：**\n\n" + "\n".join(lines)


def _session_context():
    if not CONFIG["include_session_context"]:
        return {}
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not sid:
        return {}
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    target = None
    try:
        for d in os.listdir(base):
            cand = os.path.join(base, d, f"{sid}.jsonl")
            if os.path.exists(cand):
                target = cand
                break
    except Exception:
        return {}
    if not target:
        return {}

    title, last = "", ""
    try:
        with open(target, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 512 * 1024)  # 只读尾部 512KB，够找最近记录
            f.seek(size - chunk)
            raw = f.read().decode("utf-8", errors="replace")
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t == "custom-title" and not title:
                title = (o.get("customTitle") or "").strip()
            elif t == "last-prompt" and not last:
                last = (o.get("lastPrompt") or "").strip()
            if title and last:
                break
    except Exception:
        pass
    return {"title": title, "last_prompt": last}


def _gen_request_id():
    return f"{int(time.time() * 1000)}{os.urandom(3).hex()}"


def _result_path(request_id):
    return os.path.join(RESULTS_DIR, f"{request_id}.json")


def _save_card(request_id, card):
    try:
        os.makedirs(CARDS_DIR, exist_ok=True)
        with open(os.path.join(CARDS_DIR, f"{request_id}.json"), "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False)
    except Exception:
        pass


def _read_result(request_id):
    p = _result_path(request_id)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cleanup_result(request_id):
    try:
        os.remove(_result_path(request_id))
    except Exception:
        pass


def _send_card(question, level="warning", options=None, request_id=None):
    meta = _LEVEL_META.get(level, _LEVEL_META["warning"])
    elements = []
    # 先带上当前 Desktop 会话标题 + 用户最后一句，方便知道是哪个会话、你让它干啥
    ctx = _session_context()
    if ctx.get("title"):
        elements.append({"tag": "markdown", "content": f"📌 **{ctx['title']}**"})
    if ctx.get("last_prompt"):
        lp = ctx["last_prompt"]
        if len(lp) > 120:
            lp = lp[:120] + "…"
        elements.append({"tag": "markdown", "content": f"💬 **你最后说**：{lp}"})
    elements.append({"tag": "markdown", "content": question})

    # 意见表单：快捷选项按钮 + 输入框 + 提交按钮，全部放进同一个 form。
    # 关键：form 外的普通 button 会被飞书吞掉，所以所有交互元素都必须进 form。
    # 表单内按钮用 form_action_type:"submit"（不用 behaviors），靠按钮 name(action_name) 区分点的是哪个。
    if request_id:
        form_elements = []
        # 输入框：不能 required，否则点快捷按钮会被「必填未填」拦截
        form_elements.append({
            "tag": "input",
            "name": "opinion",
            "input_type": "multiline_text",
            "placeholder": {"tag": "plain_text", "content": "写你自己的意见（可选）…"},
        })
        # 快捷选项按钮：name 编码 request_id + 选项文本(hex)，点一下即答
        if options:
            for opt in options:
                opt_hex = opt.encode("utf-8").hex()
                form_elements.append({
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": opt},
                    "type": "default",
                    "form_action_type": "submit",
                    "name": f"o_{request_id}_{opt_hex}",
                })
        # 提交意见按钮
        form_elements.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "提交意见"},
            "type": "primary_filled",
            "form_action_type": "submit",
            "name": f"s_{request_id}",
        })
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "form",
            "name": f"form_{request_id}",
            "elements": form_elements,
        })

    if options and request_id:
        tip = "点按钮选一个；想自己写意见，填输入框后点「提交意见」。"
    elif request_id:
        tip = "在输入框写下你的决定，点「提交意见」回复。"
    else:
        tip = "请直接在飞书回复你的决定，回复内容会返回给 Desktop 会话。"

    elements.append({"tag": "hr"})
    elements.append({"tag": "markdown", "content": tip})

    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{meta['emoji']} {meta['label']}"},
            "template": meta["template"],
        },
        "body": {"elements": elements},
    }
    card_str = json.dumps(card, ensure_ascii=False)
    data, err, code = _lark_json(
        ["im", "+messages-send",
         "--chat-id", CHAT_ID,
         "--as", "bot",
         "--msg-type", "interactive",
         "--content", card_str]
    )
    ok = code == 0 and bool(data)
    if ok and request_id:
        _save_card(request_id, card)  # 存原卡，供 listener 点完重建
    return ok, err, code


def _wait_for_answer(request_id, baseline_position, timeout_sec):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if request_id:
            # 按钮 / 意见表单回调，listener.py 写入结果文件，request_id 天然隔离并发
            result = _read_result(request_id)
            if result is not None:
                option = result.get("option", "")
                return (option if option else "(已确认)"), "button", False
        else:
            # 卡片发送失败后的降级通道：轮询打字新消息（无 request_id，单任务场景才安全）
            msgs = _list_messages(order="desc", page_size=10)
            if msgs:
                for m in msgs:
                    sender = m.get("sender") or {}
                    if sender.get("sender_type") != "user":
                        continue
                    if USER_OPEN_ID and sender.get("id") != USER_OPEN_ID:
                        continue
                    try:
                        pos = int(m.get("message_position", "0"))
                    except (ValueError, TypeError):
                        continue
                    if baseline_position is None or pos > baseline_position:
                        content = (m.get("content") or "").strip()
                        return content, "text", False
        time.sleep(POLL_INTERVAL_SEC)
    return "", "timeout", True


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
def ask_user(question, timeout_sec=DEFAULT_TIMEOUT_SEC, level="warning", options=None):
    if not CHAT_ID or CHAT_ID == "YOUR_CHAT_ID":
        return "尚未配置 chat_id，请先运行 python setup.py configure。"
    if USER_OPEN_ID == "YOUR_OPEN_ID":
        return "请先配置真实用户 open ID，或清空该字段以允许目标会话成员回复。"
    # 无论有没有选项都生成 request_id：选项按钮和意见表单都走回调，并发安全
    request_id = _gen_request_id()
    baseline = _latest_position()

    ok, err, code = _send_card(question, level, options, request_id)
    if not ok:
        # 卡片发送失败时退回纯 markdown 文本，保证消息至少能送达
        md = f"【Desktop 需要你确认】\n\n{question}\n\n请直接在飞书回复你的决定。"
        if not _send_message(md):
            return "发送飞书消息失败，请检查 lark-cli 是否可用。"
        request_id = None  # 回退文本后没有回调可等，退回打字轮询

    reply, source, timed_out = _wait_for_answer(request_id, baseline, timeout_sec)
    if request_id:
        _cleanup_result(request_id)
    if timed_out:
        return "TIMEOUT：等待你的回复超时了。"
    if not reply:
        return "收到空回复。"
    return reply


# ---------------------------------------------------------------------------
# MCP stdio 协议（newline-delimited JSON-RPC）
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "ask_user",
        "description": (
            "当需要用户确认、拍板、或在手机上做决定时调用此工具。"
            "发一张飞书交互卡片给用户并阻塞等待回复，返回用户的回复文本。"
            "question 支持 markdown（**加粗**、- 列表、--- 分割线、# 标题）突出重点；"
            "有明确选项时用 options 传入选项数组，卡片会把每个选项渲染成可点击的按钮；"
            "卡片里还有一个意见输入框，用户可以点按钮选、或在输入框写意见点「提交意见」，两者都能回传。"
            "适合：任务执行到关键节点需要确认时、需要用户选择时。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题。支持 markdown：**加粗**、- 列表、--- 分割线、# 标题，用来突出关键信息。",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选项列表（可选）。每个元素是一个选项的简短文字（建议≤10字），会渲染成可点击的按钮让用户选。有明确选项时用它，没有就省略。",
                },
                "level": {
                    "type": "string",
                    "enum": ["notice", "warning", "urgent"],
                    "description": "重要程度，决定卡片标题颜色：notice=蓝(普通确认)、warning=橙(默认，需要拍板)、urgent=红(紧急)。",
                },
                "timeout_sec": {
                    "type": "number",
                    "description": "等待回复的秒数，默认 600（10 分钟）。需要用户走开一阵再回来拍板时，可传更大的值（比如 3600）让任务等更久。",
                },
            },
            "required": ["question"],
        },
    }
]


def handle_request(req):
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "desktop-feishu-mcp", "version": "1.0.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "ask_user":
            question = args.get("question", "")
            timeout_sec = args.get("timeout_sec") or DEFAULT_TIMEOUT_SEC
            level = args.get("level") or "warning"
            options = args.get("options") or None
            if options is not None:
                options = [str(o) for o in options if o is not None]
                if not options:
                    options = None
            try:
                timeout_sec = int(timeout_sec)
            except (ValueError, TypeError):
                timeout_sec = DEFAULT_TIMEOUT_SEC
            try:
                text = ask_user(question, timeout_sec, level, options)
            except (ConfigurationError, OSError, subprocess.SubprocessError):
                return {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "飞书 CLI 调用失败，请运行 python setup.py doctor 检查配置。"}],
                        "isError": True,
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"unknown tool: {name}"},
        }

    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {method.split("/")[0]: []}}

    # 未知方法
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main():
    # Windows 下强制 stdin/stdout 用 UTF-8，避免中文乱码（Desktop 按 UTF-8 收发）
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        # 通知（无 id）不回复
        if req.get("id") is None and req.get("method", "").startswith("notifications/"):
            continue

        resp = handle_request(req)
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()


