"""Non-secret project settings and lazy, cross-platform CLI discovery.

Never opens the CLI profile: authentication belongs exclusively to lark-cli.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent


class ConfigurationError(RuntimeError):
    pass


ENV_KEYS = {
    "user_open_id": "FEISHU_USER_OPEN_ID",
    "chat_id": "FEISHU_CHAT_ID",
    "cli_config_dir": "LARKSUITE_CLI_CONFIG_DIR",
    "node_exe": "FEISHU_NODE_EXE",
    "lark_cli_run_js": "FEISHU_LARK_CLI_RUN_JS",
    "lark_cli_executable": "FEISHU_LARK_CLI_EXECUTABLE",
    "results_dir": "FEISHU_RESULTS_DIR",
    "cards_dir": "FEISHU_CARDS_DIR",
    "message_identity": "FEISHU_MESSAGE_IDENTITY",
    "include_session_context": "FEISHU_INCLUDE_SESSION_CONTEXT",
}


def project_path(value):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_config():
    values = {
        "user_open_id": "", "chat_id": "", "cli_config_dir": ".lark-cli",
        "node_exe": "", "lark_cli_run_js": "", "lark_cli_executable": "",
        "results_dir": "results", "cards_dir": "cards",
        "message_identity": "bot", "include_session_context": False,
    }
    path = project_path(os.environ.get("FEISHU_MCP_CONFIG", "config.json"))
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ConfigurationError("Cannot read local config JSON; check its syntax.") from exc
        if not isinstance(data, dict):
            raise ConfigurationError("Local config must be a JSON object.")
        # Only accept documented non-secret fields; do not propagate arbitrary keys.
        values.update({key: data[key] for key in values if key in data})
    for key, env_key in ENV_KEYS.items():
        if env_key in os.environ:
            values[key] = os.environ[env_key]
    for key, value in values.items():
        if key != "include_session_context" and not isinstance(value, str):
            raise ConfigurationError("Configuration field must be a string: " + key)
    enabled = values["include_session_context"]
    values["include_session_context"] = enabled is True or str(enabled).lower() in ("1", "true", "yes")
    if values["message_identity"] not in ("bot", "user", "auto"):
        raise ConfigurationError("message_identity must be bot, user or auto.")
    for key in ("cli_config_dir", "results_dir", "cards_dir"):
        if not values[key].strip():
            raise ConfigurationError("Configuration field must not be empty: " + key)
        values[key] = str(project_path(values[key]).resolve())
    return values


def cli_env(config):
    env = dict(os.environ)
    env["LARKSUITE_CLI_CONFIG_DIR"] = config["cli_config_dir"]
    return env


def _executable(value, label):
    found = shutil.which(value)
    if found:
        return found
    path = project_path(value)
    if path.is_file():
        return str(path.resolve())
    raise ConfigurationError(label + " was not found; check config or PATH.")


def find_node():
    found = shutil.which("node")
    if found:
        return found
    candidates = []
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / "nodejs/node.exe")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs/nodejs/node.exe")
            packages = Path(local) / "Microsoft/WinGet/Packages"
            try:
                candidates.extend(sorted(packages.glob("*NodeJS*/*/node.exe"), reverse=True))
            except OSError:
                pass
    else:
        candidates.extend(Path(path) for path in ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"))
        candidates.append(Path.home() / ".volta/bin/node")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def lark_command(config):
    """Prefer explicit settings, then Node package runners, then native CLI.

    On Windows do not execute a .cmd through a shell (payload quoting would change).
    Find its neighboring Node runner or require an explicit JS/native override.
    """
    if config["lark_cli_executable"]:
        executable = _executable(config["lark_cli_executable"], "CLI executable")
        if Path(executable).suffix.lower() in (".cmd", ".bat", ".ps1"):
            raise ConfigurationError("Use lark_cli_run_js or a native executable instead of a shell shim.")
        return [executable]
    node = None
    if config["node_exe"]:
        node = _executable(config["node_exe"], "Node")
    else:
        node = find_node()
    if config["lark_cli_run_js"]:
        runner = project_path(config["lark_cli_run_js"])
        if not runner.is_file() or not node:
            raise ConfigurationError("Set a working Node executable and lark_cli_run_js.")
        return [node, str(runner.resolve())]

    cli = shutil.which("lark-cli")
    candidates = []
    if cli:
        cli_path = Path(cli).resolve()
        if cli_path.suffix.lower() in (".exe", ""):
            return [str(cli_path)]
        if cli_path.suffix.lower() == ".js":
            candidates.append(cli_path)
        candidates.append(cli_path.parent / "node_modules/@larksuite/cli/scripts/run.js")
    if node:
        node_dir = Path(node).resolve().parent
        candidates.extend([
            node_dir / "node_modules/@larksuite/cli/scripts/run.js",
            node_dir.parent / "lib/node_modules/@larksuite/cli/scripts/run.js",
            ROOT / "node_modules/@larksuite/cli/scripts/run.js",
            ROOT / ".local/deps/node_modules/@larksuite/cli/scripts/run.js",
        ])
        # Standard npm global prefix layouts; none contains a personal path literal.
        if os.environ.get("APPDATA"):
            candidates.append(Path(os.environ["APPDATA"]) / "npm/node_modules/@larksuite/cli/scripts/run.js")
        candidates.extend([
            Path.home() / ".npm-global/lib/node_modules/@larksuite/cli/scripts/run.js",
            Path("/opt/homebrew/lib/node_modules/@larksuite/cli/scripts/run.js"),
            Path("/usr/local/lib/node_modules/@larksuite/cli/scripts/run.js"),
        ])
        for runner in candidates:
            if runner.is_file():
                return [node, str(runner.resolve())]
    raise ConfigurationError("Cannot find lark-cli. Run python setup.py doctor; see docs/setup.md.")


def run_cli(config, args, timeout=30):
    return subprocess.run(
        lark_command(config) + list(args), env=cli_env(config), cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def cli_succeeded(proc):
    """Both CLI exit status and the JSON envelope must indicate success."""
    if proc.returncode:
        return False
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("ok") is True
