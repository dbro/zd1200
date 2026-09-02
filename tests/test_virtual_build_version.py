import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "resolve-source-revision.sh"


class VirtualBuildVersionTests(unittest.TestCase):
    def resolve(self, git_dir: Path, override: str = "") -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["ZD_VIRTUAL_BUILD_ID"] = override
        return subprocess.run(
            ["bash", str(RESOLVER), str(git_dir)],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

    def test_resolves_loose_branch_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git_dir = Path(temporary)
            (git_dir / "refs/heads").mkdir(parents=True)
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
            (git_dir / "refs/heads/main").write_text("ad95297f1234567890abcdef1234567890abcdef\n")
            result = self.resolve(git_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ad95297\n")

    def test_resolves_packed_and_detached_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            git_dir = Path(temporary)
            (git_dir / "HEAD").write_text("ref: refs/heads/release\n")
            (git_dir / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted\n"
                "0123456789abcdef0123456789abcdef01234567 refs/heads/release\n"
            )
            packed = self.resolve(git_dir)
            (git_dir / "HEAD").write_text("fedcba9876543210fedcba9876543210fedcba98\n")
            detached = self.resolve(git_dir)
        self.assertEqual(packed.stdout, "0123456\n")
        self.assertEqual(detached.stdout, "fedcba9\n")

    def test_validated_override_supports_exported_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            accepted = self.resolve(Path(temporary) / "missing", "ABCDEF012345")
            rejected = self.resolve(Path(temporary) / "missing", "not-a-revision")
        self.assertEqual(accepted.stdout, "abcdef0\n")
        self.assertNotEqual(rejected.returncode, 0)

    def test_runtime_and_admin_patch_are_wired(self) -> None:
        runtime = (ROOT / "make-runtime-initrd.sh").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        for compose_name in ("docker-compose.yml", "docker-compose.macvlan.yml"):
            compose = (ROOT / compose_name).read_text()
            self.assertIn("target: /source-git", compose)
            self.assertIn("ZD_VIRTUAL_BUILD_ID", compose)
        self.assertIn("VIRTUAL_BUILD_ID=%s", runtime)
        self.assertIn("ZD_RUNTIME_OPTIONS_FORMAT=2", runtime)
        self.assertIn("zd1200-virtual-build-id", handoff)
        self.assertIn("virtual $virtual_build_id", handoff)
        self.assertIn("$bundle_name.js version signature not found", handoff)
        self.assertIn("sysVersion:function(e){var t=Msg.SysVersion", handoff)
        self.assertIn("sysVersion:function(t){var e=Msg.SysVersion", handoff)
        self.assertIn("Menu patch failures must not prevent", handoff)
        self.assertIn("restored pristine vendor admin bundles", handoff)
        self.assertIn("zd-stock-web", runtime)
        self.assertIn("debugfs -R", runtime)


if __name__ == "__main__":
    unittest.main()
