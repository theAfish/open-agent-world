"""Focused client regressions; the backend suite covers the real user workflow."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

from open_agent_world.cli.client import Client, CliError, choose_instance
from open_agent_world.cli.main import main, read_input, read_text, wait_result


class CliArgumentsTests(unittest.TestCase):
    def test_global_options_before_and_after_command(self):
        for arguments in (
            ["--endpoint", "http://127.0.0.1:1234", "--json", "cards"],
            ["cards", "--json", "--endpoint", "http://127.0.0.1:1234"],
        ):
            with self.subTest(arguments=arguments), patch("open_agent_world.cli.main.execute") as execute, patch("open_agent_world.cli.main.emit"):
                execute.return_value = ({}, "value", 0)
                self.assertEqual(main(arguments), 0)
                args = execute.call_args.args[0]
                self.assertEqual(args.endpoint, "http://127.0.0.1:1234")
                self.assertTrue(args.json)

    def test_invalid_options_are_json_errors_without_connection(self):
        for arguments in (["--json", "wait", "abc", "--timeout", "nan"],
                          ["create", "text", "--at", "inf", "0", "--json"],
                          ["cards", "--fields", "id"]):
            with self.subTest(arguments=arguments), patch("open_agent_world.cli.main.execute") as execute:
                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(main(arguments), 2)
                execute.assert_not_called()
                if "--json" in arguments:
                    self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_arguments")
                    self.assertEqual(errors.getvalue(), "")

    def test_invalid_endpoint_is_a_json_error(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["cards", "--endpoint", "http://[", "--json"]), 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_endpoint")

    def test_file_input_rejects_non_json_numbers(self):
        with patch("open_agent_world.cli.main.read_text", return_value='{"x": NaN}'):
            with self.assertRaises(CliError) as error:
                read_input("request.json")
        self.assertEqual(error.exception.error["code"], "invalid_json")

    def test_stdin_is_utf8_even_when_windows_text_encoding_is_gbk(self):
        stream = io.TextIOWrapper(io.BytesIO("\ufeff中文输入".encode("utf-8")), encoding="gbk")
        with patch("sys.stdin", stream):
            self.assertEqual(read_text("-"), "中文输入")

    def test_wait_connection_error_retains_the_submitted_run(self):
        client = Mock()
        client.request.side_effect = CliError("connection_failed", "Cannot reach OAW.", hint="Check the server.")
        with self.assertRaises(CliError) as error:
            wait_result(client, {"run_id": "run-1", "status": "succeeded"}, 1)
        self.assertEqual(error.exception.error["run_id"], "run-1")
        self.assertIn("oaw result run-1", error.exception.error["hint"])

    def test_interrupted_wait_keeps_the_run_id(self):
        client = Mock()
        client.request.side_effect = KeyboardInterrupt()
        with self.assertRaises(CliError) as error:
            wait_result(client, {"run_id": "run-1", "status": "succeeded"}, 1)
        self.assertEqual(error.exception.exit_code, 130)
        self.assertEqual(error.exception.error["run_id"], "run-1")

    def test_waiting_is_not_terminal_and_timeout_does_not_cancel(self):
        client = Mock()
        with patch("open_agent_world.cli.main.time.monotonic", side_effect=[0, 2]):
            with self.assertRaises(CliError) as error:
                wait_result(client, {"run_id": "run-1", "status": "waiting"}, 1)
        self.assertEqual(error.exception.error["code"], "wait_timeout")
        self.assertEqual(error.exception.error["run_id"], "run-1")
        client.request.assert_not_called()

    def test_multiple_instances_require_explicit_selection(self):
        instances = [{"id": "first", "url": "http://127.0.0.1:1", "mode": "production"},
                     {"id": "second", "url": "http://127.0.0.1:2", "mode": "production"}]
        with patch("open_agent_world.instances.list_instances", return_value=instances), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CliError) as error:
                choose_instance()
            self.assertEqual(error.exception.error["code"], "ambiguous_instance")
            self.assertEqual(choose_instance(selector="second"), instances[1])
            with patch.dict(os.environ, {"OPEN_AGENT_WORLD_URL": "http://127.0.0.1:9999"}):
                self.assertEqual(choose_instance(selector="second"), instances[1])

    def test_normal_application_is_selected_ahead_of_development_instances(self):
        instances = [{"id": "normal", "url": "http://127.0.0.1:1", "mode": "production"},
                     {"id": "development", "url": "http://127.0.0.1:2", "mode": "development"}]
        with patch("open_agent_world.instances.list_instances", return_value=instances), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(choose_instance(), instances[0])
            self.assertEqual(choose_instance(selector="development"), instances[1])
        with patch("open_agent_world.instances.list_instances", return_value=instances[1:]), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CliError) as error:
                choose_instance()
            self.assertEqual(error.exception.error["code"], "not_connected")

    def test_start_opens_the_normal_application_without_an_extra_profile(self):
        with patch("open_agent_world.instances.list_instances", return_value=[]), \
                patch("open_agent_world.cli.main.Path.is_file", return_value=True), \
                patch("importlib.util.find_spec", return_value=object()), \
                patch("open_agent_world.cli.main.subprocess.call", return_value=0) as launch:
            self.assertEqual(main(["start"]), 0)
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index("--mode") + 1], "production")
        self.assertNotIn("--profile", command)


class CliHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cls.requests.append(self.path)
                if self.path == "/api/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/api/destination")
                    self.end_headers()
                    return
                if self.path == "/api/denied":
                    self.send_response(403)
                    value = {"error": {"code": "permission_denied", "message": "Access denied.",
                                       "request_id": "request-42", "retryable": False}}
                else:
                    self.send_response(200)
                    value = {"id": "replacement-instance"}
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode("utf-8"))

            def do_POST(self):
                cls.requests.append(self.path)
                self.close_connection = True

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.requests.clear()
        self.client = Client({"url": self.url}, timeout=2)

    def test_redirect_is_not_followed_with_host_credentials(self):
        with patch.dict(os.environ, {"OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN": "host-secret"}):
            with self.assertRaises(CliError):
                self.client.request("GET", "/redirect")
        self.assertEqual(self.requests, ["/api/redirect"])

    def test_server_error_keeps_code_and_request_id(self):
        with self.assertRaises(CliError) as error:
            self.client.request("GET", "/denied")
        self.assertEqual(error.exception.exit_code, 4)
        self.assertEqual(error.exception.error["request_id"], "request-42")
        self.assertEqual(error.exception.error["code"], "permission_denied")

    def test_uncertain_write_is_not_retried(self):
        with self.assertRaises(CliError) as error:
            self.client.request("POST", "/change", {"value": 1})
        self.assertEqual(error.exception.error["code"], "outcome_unknown")
        self.assertFalse(error.exception.error["retryable"])
        self.assertEqual(self.requests, ["/api/change"])

    def test_discovered_instance_must_match_server_identity(self):
        self.client.instance["id"] = "old-instance"
        with self.assertRaises(CliError) as error:
            self.client.verify()
        self.assertEqual(error.exception.error["code"], "instance_changed")


if __name__ == "__main__":
    unittest.main()
