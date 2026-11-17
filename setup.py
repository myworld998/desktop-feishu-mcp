#!/usr/bin/env python3
"""Local setup helper; standard library only; never opens a CLI credential profile.

Autostart files and MCP snippets are generated under .local/ for manual review.
No global configuration or startup registration is changed by this script.
"""

import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import threading
import time
from xml.sax.saxutils import escape

from configuration import (
    ROOT, ConfigurationError, cli_env, cli_succeeded, lark_command,
    load_config, project_path, run_cli,
)


LOCAL = ROOT / ".local"
BOT_SCOPES = ("im:message:send_as_bot", "im:message:readonly")
OPTIONAL_SCOPES = ("im:chat:read", "im:message.group_msg")


def private_write(path, text, replace=False):
    """Create private generated files without silently replacing existing files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise ConfigurationError("File already exists; review it before replacing: " + str(path.relative_to(ROOT)))
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    descriptor = os.open(str(path), flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def prompt(label, current="", required=False):
    # Existing identifiers are retained on Enter and are never echoed as defaults.
    suffix = " [Enter 保留现值]" if current else ""
    while True:
        value = input(label + suffix + ": ").strip() or current
        if value or not required:
            return value
        print("此项不能为空。")


def configure(edit=False):
    path = project_path(os.environ.get("FEISHU_MCP_CONFIG", "config.json")).resolve()
    # Setup writes must stay in this checkout even when runtime config is elsewhere.
    if not path.is_relative_to(ROOT):
        raise ConfigurationError("Setup only writes configuration inside this project.")
    exists = path.exists()
    if exists and not edit:
        print("已有本地配置，已保留。需要修改时运行 python setup.py configure --edit。")
        return
    values = load_config() if exists else json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    print("只填写目标会话/用户标识与非敏感路径；应用认证请在 lark-cli 自己的交互向导中完成。")
    for key in ("user_open_id", "chat_id"):
        if values.get(key, "").startswith("YOUR_"):
            values[key] = ""
    values["user_open_id"] = prompt("允许回复的用户 open ID（留空允许目标会话所有成员）", values.get("user_open_id", ""))
    values["chat_id"] = prompt("目标 chat ID", values.get("chat_id", ""), required=True)
    values["cli_config_dir"] = prompt("lark-cli profile 目录（新安装建议 .lark-cli）", values.get("cli_config_dir", ".lark-cli"), required=True)
    if exists and input("确认保存修改到本地配置？输入 yes: ").strip().lower() != "yes":
        print("已取消修改。")
        return
    private_write(path, json.dumps(values, ensure_ascii=False, indent=2) + "\n", replace=exists)
    print("已保存本地配置；真实标识不会写入示例、文档或 MCP 注册片段。")


def instructions():
    print("""飞书后台人工步骤（详见 docs/setup.md）：
1. 飞书开放平台 > 开发者后台 > 创建企业自建应用 > 添加应用能力 > 机器人。
2. 权限管理 > 应用身份权限：开启以下 scope，创建版本并由管理员发布：""")
    print("   必需: " + ", ".join(BOT_SCOPES))
    print("   群聊相关: " + ", ".join(OPTIONAL_SCOPES))
    print("""3. 事件与回调 > 回调配置 > 使用长连接接收回调 > 添加 card.action.trigger。
   若提示需要已建立连接，先在另一终端运行 listener，再返回保存。
4. 版本管理与发布 > 创建版本 > 设置可用范围包含自己 > 申请发布/管理员审批。
5. 自己打开机器人会话发送一条消息；群聊则先把机器人加入群。
6. 在终端设置 LARKSUITE_CLI_CONFIG_DIR 指向本项目 .lark-cli（或已有隔离 profile）。
   新 profile 先运行 lark-cli config init，由 CLI 自己处理应用认证。
7. 默认 bot 模式不需要用户 OAuth；要自动列私聊时才执行：
   lark-cli auth login --scope \"im:chat:read\"
   打开 CLI 显示的链接，用飞书手机扫码并批准；不要复制凭据到本项目配置。
8. python setup.py chats --identity user：选择机器人私聊，然后配置 chat ID。
9. python setup.py verify：只测消息读取和长连接就绪，不能证明后台已订阅回调。
10. python setup.py verify --interactive：明确发送一张测试卡并等你点击，做端到端验收。
""")


def doctor():
    print("Python: " + sys.version.split()[0])
    print("Node: " + ("在 PATH 可用" if shutil.which("node") else "PATH 中未找到；原生 CLI 或显式 Node 配置仍可使用"))
    config = load_config()
    try:
        lark_command(config)
        proc = run_cli(config, ["--version"], timeout=15)
    except (ConfigurationError, OSError, subprocess.SubprocessError):
        print("lark-cli: 不可用。安装 Node LTS 后见 docs/setup.md 的本地安装命令。")
        return 1
    if proc.returncode:
        print("lark-cli: 入口存在但启动失败（未显示原始输出）。")
        return 1
    print("lark-cli: 可以运行。")
    print("目标会话: " + ("已填写" if config["chat_id"] and config["chat_id"] != "YOUR_CHAT_ID" else "未填写"))
    return 0


def chats(identity):
    config = load_config()
    args = ["im", "+chat-list", "--as", identity, "--page-size", "100"]
    if identity == "user":
        args += ["--types", "p2p,group"]
    proc = run_cli(config, args)
    if not cli_succeeded(proc):
        print("无法读取会话列表。检查应用权限；user 模式需扫码 OAuth。未显示原始响应。")
        return 1
    rows = json.loads(proc.stdout).get("data", {}).get("chats", [])
    if not rows:
        print("本页没有会话。bot 只能列群；找私聊请用 --identity user。")
        return 1
    for index, row in enumerate(rows, 1):
        # Intentionally show only display names locally, never the entire response.
        name = str(row.get("name") or "(未命名会话)")
        name = "".join(char for char in name if char.isprintable())
        print(f"{index}. {name}")
    selected = input("选择序号保存 chat ID，Enter 取消（只显示第一页）: ").strip()
    if not selected:
        return 0
    if not selected.isdigit() or not 1 <= int(selected) <= len(rows):
        raise ConfigurationError("无效序号。")
    chat_id = rows[int(selected) - 1].get("chat_id")
    if not isinstance(chat_id, str) or not chat_id:
        raise ConfigurationError("该会话没有有效 chat ID。")
    path = ROOT / "config.json"
    if os.environ.get("FEISHU_MCP_CONFIG") or os.environ.get("FEISHU_CHAT_ID"):
        raise ConfigurationError("自动选择只支持默认 config.json，先取消配置路径/chat ID 环境覆盖。")
    if path.exists():
        print("将更新现有 config.json 的 chat_id。")
        if input("确认写入？输入 yes: ").strip().lower() != "yes":
            return 0
        values = {key: value for key, value in json.loads(path.read_text(encoding="utf-8-sig")).items()
                  if key in config}
    else:
        values = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        values["user_open_id"] = ""
    values["chat_id"] = chat_id
    private_write(path, json.dumps(values, ensure_ascii=False, indent=2) + "\n", replace=path.exists())
    print("已保存选择到被忽略的本地配置。重新启动 server/listener 后生效。")
    return 0


def probe_consumer(config, seconds):
    """Check readiness, not callback configuration. Drop all raw CLI output."""
    proc = subprocess.Popen(
        lark_command(config) + ["event", "consume", "card.action.trigger", "--as", "bot"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=cli_env(config), cwd=ROOT,
    )
    ready = threading.Event()

    def drain(stream, markers=False):
        for line in stream:
            if markers and "[event] ready event_key=card.action.trigger" in line:
                ready.set()

    threads = [threading.Thread(target=drain, args=(proc.stdout,), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, True), daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + seconds + 5
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            if ready.wait(0.1):
                break
    finally:
        # Unbounded consume honors stdin EOF. Bounded CLI runs ignore EOF and
        # could leave a native child alive after only the Node wrapper is killed.
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for thread in threads:
            thread.join(timeout=2)
        for stream, thread in zip((proc.stdout, proc.stderr), threads):
            if not thread.is_alive():
                stream.close()
    return ready.is_set()


def verify(interactive=False, seconds=20):
    config = load_config()
    if not config["chat_id"] or config["chat_id"] == "YOUR_CHAT_ID":
        raise ConfigurationError("先执行 configure 或 chats 配置目标会话。")
    if config["message_identity"] == "user":
        proc = run_cli(config, ["auth", "check", "--scope", "im:message:readonly im:message.send_as_user im:message", "--json"])
        if not cli_succeeded(proc):
            print("用户 OAuth scope 检查失败；见 docs/setup.md。")
            return 1
    proc = run_cli(config, ["im", "+chat-messages-list", "--chat-id", config["chat_id"],
                            "--as", config["message_identity"], "--page-size", "1", "--no-reactions"])
    if not cli_succeeded(proc):
        print("消息读取失败：检查目标会话、机器人可用范围及读取权限。未显示消息内容。")
        return 1
    print("消息读取成功；这不能证明发消息权限已通过。")
    if interactive:
        print("将以配置身份向目标会话发送“连接测试：请选择连接正常。”卡片，等待最多 60 秒。")
        if input("同意发送请输入 yes: ").strip().lower() != "yes":
            print("未发送测试卡。")
            return 1
        # One temporary listener only. Stop any existing listener before this test.
        child = subprocess.Popen([sys.executable, "-u", str(ROOT / "listener.py")], cwd=ROOT,
                                 env=os.environ.copy(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace")
        ready = threading.Event()

        def drain_listener(stream, detect=False):
            for line in stream:
                if detect and "[listener] consume ready" in line:
                    ready.set()

        threads = [threading.Thread(target=drain_listener, args=(child.stdout, True), daemon=True),
                   threading.Thread(target=drain_listener, args=(child.stderr,), daemon=True)]
        for thread in threads:
            thread.start()
        try:
            # Readiness must belong to this listener, not a second competing consumer.
            deadline = time.monotonic() + seconds
            while child.poll() is None and time.monotonic() < deadline and not ready.wait(0.1):
                pass
            if not ready.is_set() or child.poll() is not None:
                print("长连接未就绪，未发送测试卡。")
                return 1
            import server
            answer = server.ask_user("连接测试：请选择连接正常。", timeout_sec=60, level="notice", options=["连接正常"])
            if answer == "连接正常":
                print("端到端验收成功：发送卡片 → 点击 → 回调 → MCP 回复。请检查卡片是否变灰禁用。")
                return 0
            print("端到端验收未通过：60 秒内没有收到预期回调。检查回调配置、用户限制及 listener。")
            return 1
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            for thread in threads:
                thread.join(timeout=2)
            child.stdout.close()
            child.stderr.close()
    if probe_consumer(config, seconds):
        print("consume 已就绪；回调订阅仍待实际点卡验证。停止已有 listener 后运行 verify --interactive。")
        return 0
    print("consume 未就绪：检查 bot 权限、CLI 配置及网络。此结果不等于未订阅回调。")
    return 1


def registration(replace=False):
    load_config()  # fail early on malformed config
    config_path = str(project_path(os.environ.get("FEISHU_MCP_CONFIG", "config.json")).resolve())
    # Snapshot only documented non-secret overrides; all generated files are ignored.
    from configuration import ENV_KEYS
    environment = {key: os.environ[key] for key in ENV_KEYS.values() if key in os.environ}
    environment["FEISHU_MCP_CONFIG"] = config_path
    entry = {"command": sys.executable, "args": [str(ROOT / "server.py")], "env": environment}
    snippet = {"mcpServers": {"desktop-feishu": entry}}
    private_write(LOCAL / "claude-desktop.local.json", json.dumps(snippet, ensure_ascii=False, indent=2) + "\n", replace)
    command = ["claude", "mcp", "add", "--transport", "stdio", "--scope", "user"]
    for key, value in environment.items():
        command += ["--env", key + "=" + value]
    command += ["desktop-feishu", "--", sys.executable, str(ROOT / "server.py")]
    if os.name == "nt":
        quoted = "& " + " ".join("'" + value.replace("'", "''") + "'" for value in command)
    else:
        quoted = shlex.join(command)
    private_write(LOCAL / "claude-code.local.txt", quoted + "\n", replace)
    print("已生成 .local/claude-desktop.local.json 和 .local/claude-code.local.txt；未修改客户端配置。")


def startup_files(replace=False):
    """Generate platform-native files only; deliberately never install them."""
    LOCAL.mkdir(exist_ok=True)
    environment = {"FEISHU_MCP_CONFIG": str(project_path(os.environ.get("FEISHU_MCP_CONFIG", "config.json")).resolve())}
    from configuration import ENV_KEYS
    environment.update({key: os.environ[key] for key in ENV_KEYS.values() if key in os.environ})
    python = str(Path(sys.executable).resolve())
    script = str(ROOT / "listener.py")
    if os.name == "nt":
        env_lines = "\n".join("$env:" + key + " = '" + value.replace("'", "''") + "'" for key, value in environment.items())
        launcher = env_lines + "\n& '" + python.replace("'", "''") + "' -u '" + script.replace("'", "''") + "' >> '" + str(ROOT / "listener.log").replace("'", "''") + "' 2>&1\n"
        private_write(LOCAL / "start-listener.local.ps1", launcher, replace)
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise ConfigurationError("PowerShell required to generate a Windows startup task.")
        arguments = subprocess.list2cmdline(["-NoProfile", "-NonInteractive", "-File", str(LOCAL / "start-listener.local.ps1")])
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + (
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            '<Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>'
            '<Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
            '<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>'
            '<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
            '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
            '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit>'
            '<RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>'
            '<Actions Context="Author"><Exec><Command>' + escape(powershell) + '</Command><Arguments>'
            + escape(arguments) + '</Arguments><WorkingDirectory>' + escape(str(ROOT))
            + '</WorkingDirectory></Exec></Actions></Task>\n'
        )
        private_write(LOCAL / "listener-task.local.xml", xml, replace)
        print("已生成 .local/listener-task.local.xml；请在任务计划程序中导入（详见 docs/setup.md）。")
    elif sys.platform == "darwin":
        data = {"Label": "org.desktop-feishu.listener", "ProgramArguments": [python, "-u", script],
                "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                "EnvironmentVariables": environment, "StandardOutPath": str(ROOT / "listener.log"),
                "StandardErrorPath": str(ROOT / "listener.log")}
        private_write(LOCAL / "org.desktop-feishu.listener.plist", plistlib.dumps(data).decode("utf-8"), replace)
        print("已生成 .local/org.desktop-feishu.listener.plist；手动安装 LaunchAgent，见 docs/setup.md。")
    else:
        def unit_quote(value):
            if "\n" in value or "\r" in value:
                raise ConfigurationError("Newlines are not supported in service paths/settings.")
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
        env_lines = "".join("Environment=" + unit_quote(key + "=" + value) + "\n" for key, value in environment.items())
        unit = ("[Unit]\nDescription=Desktop Feishu card listener\nAfter=network-online.target\n\n[Service]\n"
                + "Type=simple\nWorkingDirectory=" + unit_quote(str(ROOT)) + "\n"
                + "ExecStart=" + unit_quote(python) + " -u " + unit_quote(script) + "\n"
                + env_lines + "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n")
        private_write(LOCAL / "desktop-feishu-listener.service", unit, replace)
        print("已生成 .local/desktop-feishu-listener.service；手动安装 user service，见 docs/setup.md。")
    print("自启配置使用绝对路径；GUI/服务的 PATH 与终端不同，必要时在 config.json 显式填写 Node/CLI 路径。")


def listener(mode, replace=False):
    if mode == "startup":
        startup_files(replace)
        return 0
    config = load_config()
    lark_command(config)
    if not config["chat_id"] or config["chat_id"] == "YOUR_CHAT_ID":
        raise ConfigurationError("先配置目标 chat ID。")
    command = [sys.executable, "-u", str(ROOT / "listener.py")]
    if mode == "foreground":
        return subprocess.call(command, cwd=ROOT, env=os.environ.copy())
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
    with open(ROOT / "listener.log", "ab") as output:
        child = subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(), stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=output, **options)
    LOCAL.mkdir(exist_ok=True)
    (LOCAL / "listener.pid").write_text(str(child.pid) + "\n", encoding="ascii")
    print(f"已启动后台进程 PID={child.pid}；长连接是否就绪请检查 listener.log。停止方式见 docs/setup.md。")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check runtime dependencies without exposing credentials")
    configure_parser = commands.add_parser("configure", help="create local non-secret config")
    configure_parser.add_argument("--edit", action="store_true")
    commands.add_parser("instructions", help="print required manual console steps")
    chats_parser = commands.add_parser("chats", help="select a chat from one CLI page")
    chats_parser.add_argument("--identity", choices=("bot", "user"), default="user")
    verify_parser = commands.add_parser("verify", help="verify API access and consumer readiness")
    verify_parser.add_argument("--interactive", action="store_true")
    verify_parser.add_argument("--seconds", type=int, default=20, choices=range(5, 61), metavar="5..60")
    registration_parser = commands.add_parser("registration", help="generate ignored MCP snippets only")
    registration_parser.add_argument("--replace", action="store_true")
    listener_parser = commands.add_parser("listener", help="start listener or generate startup files")
    listener_parser.add_argument("--mode", choices=("foreground", "background", "startup"), default="foreground")
    listener_parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            configure(args.edit)
        elif args.command == "instructions":
            instructions()
        elif args.command == "chats":
            return chats(args.identity)
        elif args.command == "verify":
            return verify(args.interactive, args.seconds)
        elif args.command == "registration":
            registration(args.replace)
        elif args.command == "listener":
            return listener(args.mode, args.replace)
        elif args.command == "doctor":
            return doctor()
        else:
            doctor()
            configure()
            instructions()
            registration()
        return 0
    except ConfigurationError as exc:
        print("配置检查未通过：" + str(exc), file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError):
        print("操作未完成：检查配置、文件是否已存在、依赖及 docs/setup.md。未输出原始错误以免泄露命令内容。", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
