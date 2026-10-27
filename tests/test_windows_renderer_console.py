"""RADIA: Windows preview subprocesses must not open console windows."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_warm  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows process flags")
class WindowsRendererConsoleTest(unittest.TestCase):
    def test_uno_and_version_probes_are_headless(self):
        with patch.object(render_warm.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "LibreOffice"
            self.assertTrue(render_warm._can_import_uno("python.exe"))
            render_warm.soffice_version()
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
                self.assertTrue(call.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_bridge_has_no_console_and_keeps_its_protocol_pipes(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(render_warm.tempfile, "mkdtemp", return_value=tmp), \
             patch.object(render_warm.subprocess, "Popen") as popen, \
             patch.object(render_warm.threading, "Thread"), \
             patch.object(render_warm.Bridge, "_next_line", return_value={"op": "ready"}):
            popen.return_value = MagicMock(pid=123)
            bridge = render_warm.Bridge("python.exe", "soffice.exe")
            bridge._spawn()
            kwargs = popen.call_args.kwargs
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)

    def test_version_probe_uses_libreoffice_console_launcher(self):
        import deck_preview

        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "soffice.exe"
            cli = binary.with_suffix(".com")
            cli.touch()
            with patch.object(deck_preview, "_soffice_bin", return_value=str(binary)), \
                 patch.object(render_warm.subprocess, "run") as run:
                run.return_value.stdout = "LibreOffice"
                self.assertEqual(render_warm.soffice_version(), "LibreOffice")
                self.assertEqual(run.call_args.args[0][0], str(cli))


if __name__ == "__main__":
    unittest.main()
