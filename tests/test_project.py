"""Offline tests: no live Feishu calls and no CLI profile reads."""

import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Imports must not depend on the checkout owner's real config or environment.
with patch.dict(os.environ, {"FEISHU_MCP_CONFIG": str(ROOT / ".local/test-missing.json")}, clear=True):
    import configuration
    import server
    import listener
    import setup
    import package


class LocalTest(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"chat_id": "TEST_CHAT", "user_open_id": "TEST_USER"}), encoding="utf-8")
        environment = {"FEISHU_MCP_CONFIG": str(self.config), "PATH": os.environ.get("PATH", "")}
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if name in os.environ:
                environment[name] = os.environ[name]
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)


class ConfigTests(LocalTest):
    def test_environment_overrides_config_and_paths_are_project_relative(self):
        with patch.dict(os.environ, {"FEISHU_CHAT_ID": "OVERRIDE_CHAT", "FEISHU_RESULTS_DIR": "state/results"}):
            config = configuration.load_config()
        self.assertEqual(config["chat_id"], "OVERRIDE_CHAT")
        self.assertEqual(Path(config["results_dir"]), ROOT / "state/results")
        self.assertFalse(config["include_session_context"])

    def test_bad_json_and_non_object_fail_without_raw_content(self):
        for value in ("{broken", "[]", '{"chat_id": null}'):
            self.config.write_text(value, encoding="utf-8")
            with self.assertRaises(configuration.ConfigurationError):
                configuration.load_config()

    def test_explicit_node_and_runner(self):
        node = self.root / "node.exe"
        runner = self.root / "run.js"
        node.touch()
        runner.touch()
        config = configuration.load_config()
        config.update(node_exe=str(node), lark_cli_run_js=str(runner))
        self.assertEqual(configuration.lark_command(config), [str(node), str(runner)])

    def test_shell_shim_override_rejected(self):
        shim = self.root / "lark-cli.cmd"
        shim.touch()
        config = configuration.load_config()
        config["lark_cli_executable"] = str(shim)
        with self.assertRaises(configuration.ConfigurationError):
            configuration.lark_command(config)

    def test_cli_environment_preserves_channel_variables(self):
        with patch.dict(os.environ, {"LARK_CHANNEL_TEST": "preserved"}):
            env = configuration.cli_env(configuration.load_config())
        self.assertEqual(env["LARK_CHANNEL_TEST"], "preserved")

    def test_json_success_requires_envelope_and_exit_status(self):
        for code, text, expected in ((0, '{"ok":true}', True), (1, '{"ok":true}', False),
                                     (0, '{"ok":false}', False), (0, 'bad', False)):
            self.assertEqual(configuration.cli_succeeded(subprocess.CompletedProcess([], code, text)), expected)


class MCPTests(LocalTest):
    def test_stdio_protocol_and_notifications_from_any_cwd(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        proc = subprocess.run([sys.executable, str(ROOT / "server.py")],
                              input="\n".join(json.dumps(req) for req in requests) + "\n",
                              cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "ask_user")

    def test_cli_failure_is_a_tool_error_not_protocol_crash(self):
        with patch.object(server, "ask_user", side_effect=configuration.ConfigurationError("hidden")):
            reply = server.handle_request({"id": 1, "method": "tools/call", "params": {"name": "ask_user"}})
        self.assertTrue(reply["result"]["isError"])
        self.assertNotIn("hidden", json.dumps(reply))

    def test_context_is_opt_in(self):
        with patch.dict(server.CONFIG, {"include_session_context": False}), patch("builtins.open", side_effect=AssertionError("no read")):
            self.assertEqual(server._session_context(), {})

    def test_consumer_keeps_stdin_open_and_uses_bot(self):
        with patch.object(listener, "lark_command", return_value=["TEST_CLI"]), \
                patch.object(listener.subprocess, "Popen") as start:
            listener._start_consume()
        command = start.call_args.args[0]
        self.assertEqual(command[-2:], ["--as", "bot"])
        self.assertNotIn("--quiet", command)
        self.assertEqual(start.call_args.kwargs["stdin"], subprocess.PIPE)

    def test_card_form_and_name_encoding_and_disabled_rebuild(self):
        sent = []

        def fake_cli(args, timeout=30):
            sent.append(args)
            return {"ok": True}, "", 0

        request_id = "test_request_123"
        with patch.object(server, "_lark_json", side_effect=fake_cli), patch.object(server, "CARDS_DIR", str(self.root)):
            ok, _, _ = server._send_card("问题", "warning", ["中文_选项", "取消"], request_id)
        self.assertTrue(ok)
        args = sent[0]
        card = json.loads(args[args.index("--content") + 1])
        self.assertEqual(card["schema"], "2.0")
        self.assertTrue(card["config"]["update_multi"])
        forms = [node for node in card["body"]["elements"] if node["tag"] == "form"]
        self.assertEqual(len(forms), 1)
        children = forms[0]["elements"]
        self.assertNotIn("required", children[0])
        for button in children[1:]:
            self.assertEqual(button["form_action_type"], "submit")
        rid, option_hex = children[1]["name"][2:].rsplit("_", 1)
        self.assertEqual(rid, request_id)
        self.assertEqual(bytes.fromhex(option_hex).decode("utf-8"), "中文_选项")
        self.assertEqual(children[-1]["name"], "s_" + request_id)
        with patch.object(listener, "CARDS_DIR", str(self.root)):
            rebuilt = listener._rebuild_card(request_id, "中文_选项", "button")
        self.assertEqual(rebuilt["header"]["template"], "grey")
        form = next(node for node in rebuilt["body"]["elements"] if node["tag"] == "form")
        self.assertTrue(all(node["disabled"] for node in form["elements"]))
        self.assertIn("问题", json.dumps(rebuilt, ensure_ascii=False))

    def test_results_are_atomic_and_traversal_is_rejected(self):
        with patch.object(listener, "RESULTS_DIR", str(self.root)):
            listener._write_result("test_123", {"option": "yes"})
            listener._write_result("../invalid", {"option": "no"})
        self.assertEqual(json.loads((self.root / "test_123.json").read_text())["option"], "yes")
        self.assertFalse((self.root / "test_123.json.tmp").exists())
        self.assertFalse(listener._valid_request_id("../invalid"))

    def test_listener_parses_button_and_opinion_and_filters_operator(self):
        for action, expected in (("o_test_request_" + "中文_选择".encode().hex(), "中文_选择"),
                                 ("s_test_request", "自由意见")):
            event = {"operator_id": "TEST_USER", "chat_id": "TEST_CHAT", "action_name": action,
                     "form_value": json.dumps({"opinion": "自由意见"}), "token": "TEST_CALLBACK"}
            for operator, accepted in (("TEST_USER", True), ("OTHER_USER", False)):
                event["operator_id"] = operator
                proc = Mock(stdout=io.StringIO(json.dumps(event) + "\n"), stderr=io.StringIO(), stdin=io.StringIO())
                with patch.dict(listener.CONFIG, {"chat_id": "TEST_CHAT", "user_open_id": "TEST_USER"}), \
                        patch.object(listener, "RESULTS_DIR", str(self.root)), \
                        patch.object(listener, "lark_command", return_value=["TEST_CLI"]), \
                        patch.object(listener, "_start_consume", return_value=proc), \
                        patch.object(listener, "_write_result") as write, \
                        patch.object(listener, "_update_card") as update, \
                        patch.object(listener.time, "sleep", side_effect=KeyboardInterrupt), \
                        patch("builtins.print"):
                    with self.assertRaises(KeyboardInterrupt):
                        listener.main()
                self.assertEqual(write.called, accepted)
                if accepted:
                    self.assertEqual(write.call_args.args[0], "test_request")
                    self.assertEqual(write.call_args.args[1]["option"], expected)
                    update.assert_called_once()


class SetupTests(LocalTest):
    def test_registration_writes_only_local_snippets(self):
        with patch.object(setup, "LOCAL", self.root), patch("builtins.print"):
            setup.registration()
        snippet = json.loads((self.root / "claude-desktop.local.json").read_text())
        entry = snippet["mcpServers"]["desktop-feishu"]
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(entry["args"], [str(ROOT / "server.py")])
        self.assertEqual(entry["env"]["FEISHU_MCP_CONFIG"], str(self.config))
        self.assertIn("--transport", (self.root / "claude-code.local.txt").read_text())

    def test_private_write_refuses_accidental_replace(self):
        path = self.root / "generated.txt"
        setup.private_write(path, "first")
        with self.assertRaises(configuration.ConfigurationError):
            setup.private_write(path, "second")
        self.assertEqual(path.read_text(), "first")

    def test_native_startup_file_structure(self):
        with patch.object(setup, "LOCAL", self.root), patch("builtins.print"):
            setup.startup_files()
        if os.name == "nt":
            task = ET.parse(self.root / "listener-task.local.xml")
            namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            self.assertIsNotNone(task.find(".//t:LogonTrigger", namespace))
            script = (self.root / "start-listener.local.ps1").read_text()
            self.assertIn("FEISHU_MCP_CONFIG", script)
        elif sys.platform == "darwin":
            data = plistlib.loads((self.root / "org.desktop-feishu.listener.plist").read_bytes())
            self.assertTrue(data["RunAtLoad"])
            self.assertEqual(data["ProgramArguments"][0], str(Path(sys.executable).resolve()))
        else:
            unit = (self.root / "desktop-feishu-listener.service").read_text()
            self.assertIn("Restart=on-failure", unit)
            self.assertIn("FEISHU_MCP_CONFIG", unit)

    def test_public_manifest_has_no_private_data_paths(self):
        self.assertEqual(len(package.PUBLIC_FILES), len(set(package.PUBLIC_FILES)))
        self.assertNotIn("config.json", package.PUBLIC_FILES)
        for name in ("config.json", "cards/card.json", "results/result.json", ".lark-cli/profile.json", "listener.log"):
            self.assertTrue(package.ignored(ROOT / name))


if __name__ == "__main__":
    unittest.main()
