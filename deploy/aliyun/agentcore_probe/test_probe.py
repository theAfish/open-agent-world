"""Run with unittest; Linux tests also run offline inside the probe image."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from probe import Probe, ThreadingHTTPServer
from prepare import build_request


TOKEN = "test-only-token-" + "x" * 32
RUN_ID = str(uuid.uuid4())


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.probe = Probe(self.root, TOKEN, RUN_ID)

    def test_ordinary_directory_is_not_a_mount(self):
        self.assertFalse(self.probe.mounted())
        with self.assertRaises(OSError):
            self.probe.marker(create=True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_credentials_or_marker_paths_are_rejected(self):
        for token in ("", "short", " " * 64, "x" * 40 + "\n"):
            with self.assertRaises(ValueError):
                Probe(self.root, token, RUN_ID)
        with self.assertRaises(ValueError):
            Probe(self.root, TOKEN, "../../outside")

    def test_http_auth_and_missing_mount(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.probe.handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(stop)

        def request(path, method="GET", headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                conn.request(method, path, headers=headers or {})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        self.assertEqual(request("/health/live"), (200, {"alive": True}))
        self.assertEqual(request("/health/ready"), (503, {"mounted": False}))
        self.assertEqual(request("/probe/marker", "POST")[0], 401)
        self.assertEqual(request("/probe/marker", headers={"X-OAW-Probe-Token": TOKEN})[0], 400)
        headers = {"X-OAW-Probe-Token": TOKEN, "X-OAW-Session": "operator-test"}
        self.assertEqual(request("/probe/marker", "POST", headers), (503, {"error": "mount_or_marker_invalid"}))
        self.assertEqual(request("/probe/marker?token=" + TOKEN)[0], 401)
        self.assertEqual(request("/arbitrary-file", headers=headers)[0], 404)
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "Linux no-follow filesystem contract")
    def test_marker_survives_new_probe_and_lost_reply_does_not_overwrite(self):
        with patch.object(Probe, "mounted", return_value=True):
            first = self.probe.marker(create=True)
            original = (self.root / self.probe.filename).read_bytes()
            second = Probe(self.root, TOKEN, RUN_ID)
            recovered = second.marker(create=False)
            retried = second.marker(create=True)
        self.assertTrue(first["created"])
        self.assertFalse(first["from_previous_boot"])
        self.assertTrue(recovered["from_previous_boot"])
        self.assertFalse(retried["created"])
        self.assertEqual(retried["marker"], first["marker"])
        self.assertEqual((self.root / self.probe.filename).read_bytes(), original)

    @unittest.skipUnless(os.name == "posix", "Linux no-follow filesystem contract")
    def test_symlink_and_fifo_markers_fail_without_touching_target(self):
        outside = self.root / "outside"
        outside.write_text("keep")
        marker = self.root / self.probe.filename
        marker.symlink_to(outside)
        with patch.object(Probe, "mounted", return_value=True):
            with self.assertRaises(OSError):
                self.probe.marker(create=True)
        self.assertEqual(outside.read_text(), "keep")
        marker.unlink()
        os.mkfifo(marker)
        with patch.object(Probe, "mounted", return_value=True):
            with self.assertRaises(OSError):
                self.probe.marker(create=False)

    @unittest.skipUnless(os.name == "posix", "Linux no-follow filesystem contract")
    def test_corrupt_marker_is_preserved_for_reconciliation(self):
        marker = self.root / self.probe.filename
        for text in ("partial-write", "{}", "[]", "x" * 4097):
            marker.write_text(text)
            with patch.object(Probe, "mounted", return_value=True):
                with self.assertRaises((OSError, ValueError)):
                    self.probe.marker(create=True)
            self.assertEqual(marker.read_text(), text)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "acr_instance_id": "cri-test", "acr_namespace": "test", "acr_repository": "probe",
            "image_tag": "test", "role_arn": "acs:ram::123456789:role/test",
            "access_point_domain": "ap-test.test-fs.cn-hangzhou.nas.aliyuncs.com",
            "mount_subdirectory": "probe/test", "mount_subdirectory_verified": True,
            "probe_token": TOKEN, "probe_run_id": RUN_ID,
        }

    def test_only_probe_credentials_are_forwarded(self):
        self.config["ALIYUN_ACCESS_KEY_SECRET"] = "must-not-copy"
        request = build_request(self.config)
        self.assertNotIn("must-not-copy", json.dumps(request))
        self.assertEqual(request["network"]["accessInternet"], {"enabled": False})
        self.assertEqual(request["runtime"]["hpa"]["maxSandboxCount"], 1)
        self.assertEqual(request["runtime"]["sessionPolicy"]["type"], "ISOLATED_HEADER_FIELD")

    def test_missing_acr_or_unverified_directory_blocks_preparation(self):
        for change in ({"acr_instance_id": None}, {"acr_instance_id": "personal"},
                       {"role_arn": "anything"}, {"mount_subdirectory_verified": False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_request(self.config | change)

    def test_mount_root_traversal_and_url_rejected(self):
        for path in ("/", "", ".", "../x", "a/../b", "a//b", "a/", "/absolute", "a\\b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_request(self.config | {"mount_subdirectory": path})
        for server in ("https://ap-test.test-fs.cn-hangzhou.nas.aliyuncs.com",
                       self.config["access_point_domain"] + ":/", "example.com"):
            with self.subTest(server=server), self.assertRaises(ValueError):
                build_request(self.config | {"access_point_domain": server})


if __name__ == "__main__":
    unittest.main()
