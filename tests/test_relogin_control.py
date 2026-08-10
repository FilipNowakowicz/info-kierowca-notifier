import errno
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from info_kierowca_notifier.auth import relogin_control


class ProcessLivenessTests(unittest.TestCase):
    def test_posix_live_pid(self):
        probe = Mock(return_value=None)
        self.assertTrue(relogin_control._posix_process_alive(123, probe=probe))
        probe.assert_called_once_with(123, 0)

    def test_posix_missing_pid(self):
        probe = Mock(side_effect=ProcessLookupError(errno.ESRCH, "missing"))
        self.assertFalse(relogin_control._posix_process_alive(123, probe=probe))

    def test_posix_permission_denied_means_alive(self):
        probe = Mock(side_effect=PermissionError(errno.EPERM, "denied"))
        self.assertTrue(relogin_control._posix_process_alive(123, probe=probe))

    @staticmethod
    def kernel32(*, handle=42, wait_result=relogin_control._WAIT_TIMEOUT):
        api = Mock()
        api.OpenProcess.return_value = handle
        api.WaitForSingleObject.return_value = wait_result
        return api

    def test_windows_live_process_and_minimal_rights(self):
        api = self.kernel32()
        self.assertTrue(relogin_control._windows_process_alive(123, api, Mock()))
        api.OpenProcess.assert_called_once_with(relogin_control._SYNCHRONIZE, False, 123)
        api.WaitForSingleObject.assert_called_once_with(42, 0)
        api.CloseHandle.assert_called_once_with(42)

    def test_windows_exited_process(self):
        api = self.kernel32(wait_result=relogin_control._WAIT_OBJECT_0)
        self.assertFalse(relogin_control._windows_process_alive(123, api, Mock()))
        api.CloseHandle.assert_called_once_with(42)

    def test_windows_nonexistent_process(self):
        api = self.kernel32(handle=0)
        self.assertFalse(
            relogin_control._windows_process_alive(
                123, api, Mock(return_value=relogin_control._ERROR_INVALID_PARAMETER)
            )
        )
        api.WaitForSingleObject.assert_not_called()
        api.CloseHandle.assert_not_called()

    def test_windows_access_denied_is_conservatively_alive(self):
        api = self.kernel32(handle=0)
        self.assertTrue(
            relogin_control._windows_process_alive(
                123, api, Mock(return_value=relogin_control._ERROR_ACCESS_DENIED)
            )
        )

    def test_windows_handle_is_closed_when_wait_fails(self):
        api = self.kernel32()
        api.WaitForSingleObject.side_effect = OSError("query failed")
        with self.assertRaises(OSError):
            relogin_control._windows_process_alive(123, api, Mock())
        api.CloseHandle.assert_called_once_with(42)

    def test_windows_dispatch_never_invokes_os_kill(self):
        with patch.object(relogin_control.os, "name", "nt"), patch(
            "info_kierowca_notifier.auth.relogin_control._windows_process_alive", return_value=True
        ) as windows_probe, patch("info_kierowca_notifier.auth.relogin_control.os.kill") as kill:
            self.assertTrue(relogin_control.process_alive(123))
        windows_probe.assert_called_once_with(123)
        kill.assert_not_called()


class AtomicWriteTests(unittest.TestCase):
    def test_concurrent_writes_are_atomic_and_leave_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            barrier = threading.Barrier(12)
            errors = []

            def write(index):
                try:
                    barrier.wait()
                    relogin_control._atomic_write(path, {"writer": index})
                except BaseException as error:
                    errors.append(error)

            threads = [threading.Thread(target=write, args=(index,)) for index in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertIn(json.loads(path.read_text(encoding="utf-8"))["writer"], range(12))
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
