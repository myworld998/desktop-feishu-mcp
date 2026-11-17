#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Receive card callbacks through lark-cli and write per-request reply files."""

import json
import os
import re
import subprocess
import sys
import threading
import time

from configuration import ConfigurationError, cli_env, lark_command, load_config

CONFIG = load_config()
RESULTS_DIR = CONFIG["results_dir"]
CARDS_DIR = CONFIG["cards_dir"]


def _env():
    return cli_env(CONFIG)

def _parse_action_value(action_value):
    if not action_value:
        return None
    try:
        value = action_value if isinstance(action_value, dict) else json.loads(action_value)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _write_result(request_id, payload):
    if not _valid_request_id(request_id):
        return
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{request_id}.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(temporary, path)


def _valid_request_id(request_id):
    return isinstance(request_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id))


def _rebuild_card(request_id, option, kind):
    orig = None
    try:
        p = os.path.join(CARDS_DIR, f"{request_id}.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                orig = json.load(f)
    except Exception:
        orig = None

    result_md = f"**你的意见：{option}**" if kind == "form" else f"**你的选择：{option}**"

    if not orig or not isinstance(orig, dict):
        # 兜底：无原卡时只显示结果
        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"✅ 已回复"},
                "template": "grey",
            },
            "body": {"elements": [{"tag": "markdown", "content": result_md}]},
        }

    # 保留原卡全部内容，只做三处改动：header 变灰、交互组件禁用、尾部加结果行
    header = orig.get("header") or {}
    header["template"] = "grey"
    emoji = "✅"
    header["title"] = {"tag": "plain_text", "content": f"{emoji} 已回复 · {header.get('title', {}).get('content', '')}"}

    body = orig.get("body") or {}
    elements = body.get("elements") or []

    def _disable_recursive(node):
        # 把 form 内的 input/button 全部禁用，按钮不再可点、输入框不再可编辑
        if isinstance(node, dict):
            if node.get("tag") == "form":
                for child in node.get("elements", []):
                    if isinstance(child, dict) and child.get("tag") in ("input", "button"):
                        child["disabled"] = True
            elif node.get("tag") in ("input", "button"):
                node["disabled"] = True

    for el in elements:
        _disable_recursive(el)

    elements.append({"tag": "hr"})
    elements.append({"tag": "markdown", "content": result_md})

    new_card = dict(orig)
    new_card["header"] = header
    new_card["body"] = {"elements": elements}
    return new_card


def _update_card(request_id, token, option, kind="button"):
    if not token:
        print("[listener] 无 token，跳过卡片更新", flush=True)
        return
    card = _rebuild_card(request_id, option, kind)
    body = {"token": token, "card": card}
    try:
        proc = subprocess.run(
            lark_command(CONFIG) + ["api", "POST", "interactive/v1/card/update",
             "--data", json.dumps(body, ensure_ascii=False),
             "--as", "bot"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, env=_env(),
        )
        ok = proc.returncode == 0 and json.loads(proc.stdout).get("ok") is True
        print(f"[listener] 卡片更新{'成功' if ok else '失败'} rc={proc.returncode}", flush=True)
    except Exception:
        print("[listener] 卡片更新异常（未记录原始响应）", flush=True)


def _start_consume():
    return subprocess.Popen(
        lark_command(CONFIG) + ["event", "consume", "card.action.trigger", "--as", "bot"],
        stdin=subprocess.PIPE,  # 关键：consume 把 stdin EOF 当退出信号，给它一个常开管道
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
    )


def _drain_stderr(proc):
    try:
        for line in proc.stderr:
            line = line.strip()
            if line:
                if "[event] ready event_key=" in line:
                    print("[listener] consume ready", flush=True)
                elif "[event] exited" in line:
                    print("[listener] consume exited", flush=True)
    except Exception:
        pass


def main():
    # Windows 下 stdout 强制 UTF-8，避免中文乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print(f"[listener] 结果目录：{RESULTS_DIR}", flush=True)
    if CONFIG["user_open_id"] == "YOUR_OPEN_ID":
        raise ConfigurationError("Replace or clear the user placeholder first.")
    if not CONFIG["chat_id"] or CONFIG["chat_id"] == "YOUR_CHAT_ID":
        raise ConfigurationError("Configure the target chat first.")
    lark_command(CONFIG)  # fail once on missing dependencies instead of reconnecting forever
    os.makedirs(RESULTS_DIR, exist_ok=True)

    while True:
        proc = _start_consume()
        stderr_thread = threading.Thread(target=_drain_stderr, args=(proc,), daemon=True)
        stderr_thread.start()
        print("[listener] 已启动 card.action.trigger 长连接", flush=True)

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue

                # Never log raw events, tokens, identifiers, action names or opinions.

                if not isinstance(event, dict):
                    continue
                expected_user = CONFIG["user_open_id"]
                if expected_user and expected_user != "YOUR_OPEN_ID" and event.get("operator_id") != expected_user:
                    continue
                if CONFIG["chat_id"] and event.get("chat_id") != CONFIG["chat_id"]:
                    continue
                form_value = event.get("form_value") or ""
                action_name = event.get("action_name") or ""
                if form_value and action_name:
                    # 表单提交：request_id + 选项/意见都编码在触发按钮的 name 里
                    try:
                        fv = form_value if isinstance(form_value, dict) else json.loads(form_value)
                        if not isinstance(fv, dict):
                            fv = {}
                    except Exception:
                        fv = {}
                    if action_name.startswith("o_"):
                        # 快捷选项：o_<request_id>_<hex(option)>。从右边切最后一个 _，
                        # 因为 request_id 可能含下划线、而 hex(option) 一定不含。
                        try:
                            body = action_name[2:]
                            request_id, opt_hex = body.rsplit("_", 1)
                            option = bytes.fromhex(opt_hex).decode("utf-8", errors="replace")
                        except Exception:
                            request_id, option = "", ""
                        kind = "button"
                    elif action_name.startswith("s_"):
                        # 提交意见：s_<request_id>
                        request_id = action_name[2:]
                        option = str(fv.get("opinion", "") or "").strip()
                        kind = "form"
                    else:
                        print("[listener] 未知表单提交，跳过", flush=True)
                        continue
                    if not _valid_request_id(request_id):
                        print("[listener] 无效 request_id，跳过", flush=True)
                        continue
                    payload = {
                        "source": "button",
                        "option": option,
                        "raw": fv,
                        "received_at": time.time(),
                    }
                    _write_result(request_id, payload)
                    _update_card(request_id, event.get("token") or "", option, kind=kind)
                    print(f"[listener] 表单回复已保存 kind={kind}", flush=True)
                    continue

                action_value = event.get("action_value") or ""
                parsed = _parse_action_value(action_value)
                if not parsed:
                    # 无 request_id 的回调（异常情况）跳过
                    print("[listener] 收到无 request_id 的回调，跳过", flush=True)
                    continue

                request_id = parsed.get("request_id")
                if not _valid_request_id(request_id):
                    print("[listener] action_value 缺有效 request_id，跳过", flush=True)
                    continue

                option = parsed.get("option", "")
                payload = {
                    "source": "button",
                    "option": option,
                    "raw": parsed,
                    "received_at": time.time(),
                }
                _write_result(request_id, payload)
                # 立刻更新卡片，让用户看到「已选择」反馈
                _update_card(request_id, event.get("token") or "", option, kind="button")
                print("[listener] 按钮回复已保存", flush=True)
        except Exception:
            print("[listener] consume 循环异常（未记录原始事件）", flush=True)
        finally:
            # Close stdin first: the CLI treats EOF as graceful shutdown. Do not
            # close stderr while its reader is blocked holding a buffer lock.
            try:
                proc.stdin.close()
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            except Exception:
                pass
            stderr_thread.join(timeout=2)
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not proc.stderr or not stderr_thread.is_alive():
                        stream.close()
                except Exception:
                    pass

        # 长连接断了就等几秒重连，保持常驻
        print("[listener] 长连接断开，5 秒后重连", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ConfigurationError, OSError, subprocess.SubprocessError):
        print("[listener] 启动失败，请运行 python setup.py doctor；未记录原始错误。", file=sys.stderr)
        raise SystemExit(1)


