import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVICE_FILES = (
    REPOSITORY_ROOT / "systemd" / "info-kierowca-notifier.service",
    REPOSITORY_ROOT / "systemd" / "info-kierowca-dashboard.service",
)
REQUIRED_PATH_COMPONENTS = (
    "%h/.local/bin",
    "%h/.nix-profile/bin",
    "/etc/profiles/per-user/%u/bin",
    "/run/current-system/sw/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
)


class SystemdUnitPortabilityTests(unittest.TestCase):
    def test_service_paths_cover_portable_uv_install_locations(self):
        for service_file in SERVICE_FILES:
            with self.subTest(service=service_file.name):
                unit = service_file.read_text(encoding="utf-8")
                path_lines = [
                    line for line in unit.splitlines() if line.startswith("Environment=")
                ]
                self.assertEqual(len(path_lines), 1)
                path_line = path_lines[0]
                for component in REQUIRED_PATH_COMPONENTS:
                    self.assertIn(component, path_line)
                self.assertNotIn("/nix/store/", path_line)


if __name__ == "__main__":
    unittest.main()
