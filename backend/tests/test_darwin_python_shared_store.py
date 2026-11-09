"""A macOS backend-Python copy is shared across OAW checkouts and profiles."""
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sandbox.models import SandboxPreparationError
from backend.sandbox.python_runtime import SharedPythonRuntime, DARWIN_MANAGED_PYTHON


def fake_python(root: Path) -> None:
    distribution = root / f"cpython-{DARWIN_MANAGED_PYTHON}-test"
    binary = distribution / "bin" / "python3"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    stdlib = distribution / "lib" / f"python{DARWIN_MANAGED_PYTHON}" / "os.py"
    stdlib.parent.mkdir(parents=True)
    stdlib.write_text("# standard library marker\n", encoding="utf-8")
    from backend.sandbox.darwin_python_copy import source_identity
    (root / 'backend-source.json').write_text(json.dumps(source_identity()))


class DarwinSharedStoreTests(unittest.TestCase):
    def test_versioned_distribution_is_found_without_uv_minor_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_python(root / "base")
            distribution = root / "base" / f"cpython-{DARWIN_MANAGED_PYTHON}.14-macos-aarch64-none"
            (root / "base" / f"cpython-{DARWIN_MANAGED_PYTHON}-test").rename(distribution)
            runtime = SharedPythonRuntime(root / "profile", darwin_store=root / "shared")
            self.assertEqual(runtime._darwin_base_python(root / "base"), distribution / "bin" / "python3")

    def test_macos_wheels_use_the_shared_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SharedPythonRuntime(root / "checkout", darwin_store=root / "shared")
            with patch("backend.sandbox.python_runtime.sys.platform", "darwin"):
                self.assertEqual(runtime._uv_cache(), (root / "shared" / "cache").resolve())

    def test_status_reports_cached_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SharedPythonRuntime(root / "checkout", darwin_store=root / "shared")
            fake_python(root / "shared" / "base")
            with patch("backend.sandbox.python_runtime.sys.platform", "darwin"):
                self.assertTrue(runtime.snapshot()["distribution_cached"])

    def test_second_profile_reuses_distribution_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "shared"
            copies = []

            def make_profile(name):
                runtime = SharedPythonRuntime(root / name, darwin_store=store)
                runtime._uv = lambda: "uv"
                def copy(root):
                    copies.append(name)
                    fake_python(root)
                runtime._copy_backend_python = copy

                def run(argv):
                    arguments = [str(arg) for arg in argv]
                    if "python" in arguments and "install" in arguments:
                        downloads.append(name)
                        staging = Path(arguments[arguments.index("--install-dir") + 1])
                        fake_python(staging)
                    elif "venv" in arguments:
                        runtime.python.parent.mkdir(parents=True)
                        runtime.python.write_text("#!/bin/sh\n", encoding="utf-8")
                        (runtime.venv / "pyvenv.cfg").write_text("ready", encoding="utf-8")

                runtime._run = run
                return runtime

            first, second = make_profile("checkout-a"), make_profile("checkout-b")
            first._ensure_darwin()
            second._ensure_darwin()

            self.assertEqual(copies, ["checkout-a"])
            self.assertIsNotNone(first._darwin_base_python())
            self.assertIsNotNone(second._darwin_base_python())
            self.assertTrue(first.python.is_file())
            self.assertTrue(second.python.is_file())
            self.assertNotEqual(first.base, second.base)
            self.assertIsNotNone(first._darwin_base_python(store / "base"))

    def test_existing_profile_seeds_shared_store_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SharedPythonRuntime(root / "checkout", darwin_store=root / "shared")
            fake_python(runtime.base)
            runtime._run = lambda _argv: self.fail("An existing distribution must not be downloaded")

            cached = runtime._cached_darwin_base()

            self.assertEqual(cached, (root / "shared" / "base").resolve())
            self.assertIsNotNone(runtime._darwin_base_python(cached))

    def test_failed_download_does_not_publish_partial_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SharedPythonRuntime(root / "checkout", darwin_store=root / "shared")

            def fail(staging):
                fake_python(staging)
                raise SandboxPreparationError("copy interrupted")

            runtime._copy_backend_python = fail
            with self.assertRaisesRegex(SandboxPreparationError, "copy interrupted"):
                runtime._cached_darwin_base()
            self.assertFalse((root / "shared" / "base").exists())
            self.assertFalse((root / "shared" / "base.partial").exists())

    def test_interrupted_cache_swap_restores_previous_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = SharedPythonRuntime(root / "checkout", darwin_store=root / "shared")
            fake_python(root / "shared" / "base.previous")
            incomplete = root / "shared" / "base" / "cpython-3.12-broken" / "bin"
            incomplete.mkdir(parents=True)
            (incomplete / "python3").write_text("broken", encoding="utf-8")
            runtime._run = lambda _argv: self.fail("Recovery must not download")

            cached = runtime._cached_darwin_base()

            self.assertIsNotNone(runtime._darwin_base_python(cached))
            self.assertFalse((root / "shared" / "base.previous").exists())

    def test_profile_copy_materializes_links_outside_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "shared"
            fake_python(store / "base")
            stdlib = store / "base" / f"cpython-{DARWIN_MANAGED_PYTHON}-test" / "lib" / f"python{DARWIN_MANAGED_PYTHON}" / "os.py"
            external = store / "source-stdlib" / "os.py"
            external.parent.mkdir(parents=True)
            external.write_text("# shared stdlib\n", encoding="utf-8")
            stdlib.unlink()
            try:
                stdlib.symlink_to(os.path.relpath(external, stdlib.parent))
                (store / "base" / "cpython-3.12-macos-alias").symlink_to(
                    store / "base.partial" / f"cpython-{DARWIN_MANAGED_PYTHON}-test", target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Symbolic links unavailable: {error}")
            runtime = SharedPythonRuntime(root / "checkout", darwin_store=store)
            runtime._uv = lambda: "uv"

            def run(argv):
                if "venv" in [str(arg) for arg in argv]:
                    runtime.python.parent.mkdir(parents=True)
                    runtime.python.write_text("#!/bin/sh\n", encoding="utf-8")
                    (runtime.venv / "pyvenv.cfg").write_text("ready", encoding="utf-8")

            runtime._run = run
            runtime._ensure_darwin()
            copied = runtime.base / f"cpython-{DARWIN_MANAGED_PYTHON}-test" / "lib" / f"python{DARWIN_MANAGED_PYTHON}" / "os.py"
            self.assertTrue(copied.is_file())
            self.assertFalse(copied.is_symlink())


if __name__ == "__main__":
    unittest.main()
