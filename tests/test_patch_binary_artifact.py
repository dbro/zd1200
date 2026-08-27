import gzip
import hashlib
import io
import json
import struct
import tarfile
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from binary_patch_catalog import ARTIFACTS, PATCHES, PatchRule, load_catalog
from build_zd1200_bundle import (
    make_payload,
    patch_kernel,
    scorpion_payload_paths,
    update_scorpion_control_files,
)
from release_manifest import RELEASES, ReleaseManifest, load_release_manifest
from ruckus_tac_decrypt import decrypt_bytes, decrypt_file
from verify_release_archive import sha256_file, verify_decrypted_archive, verify_encrypted_input
from patch_binary_artifact import (
    apply_rules,
    find_elf_member,
    find_masked,
    parse_masked_hex,
    rebuild_artifact,
)
from ruckus_bl7 import parse_bl7


def rule(
    signature,
    offset,
    expected,
    replacement,
    *,
    artifact="synthetic",
    rel32_exit=0,
):
    return PatchRule(
        artifact,
        "synthetic_rule",
        signature,
        offset,
        expected,
        replacement,
        "synthetic test rule",
        rel32_exit,
    )


class MaskedPatternTests(unittest.TestCase):
    def test_wildcards_and_overlapping_matches(self):
        pattern = parse_masked_hex("aa ?? aa")
        self.assertEqual(find_masked(bytes.fromhex("aa 01 aa aa 02 aa"), pattern), [0, 3])

    def test_rejects_invalid_patterns(self):
        for value in ("", "a", "xx"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_masked_hex(value)


class CatalogTests(unittest.TestCase):
    def write_catalog(self, document):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "catalog.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_repository_catalog_has_unique_artifacts_and_rules(self):
        self.assertEqual(set(ARTIFACTS), {"ap_11n_scorpion_wlan_ko", "zd1200_kernel_elf"})
        self.assertEqual(len({rule.name for rule in PATCHES}), len(PATCHES))
        self.assertTrue(all(rule.artifact_id in ARTIFACTS for rule in PATCHES))

    def test_catalog_rejects_unknown_artifact_reference(self):
        invalid = {
            "catalog_version": 1,
            "artifacts": [{"artifact_id": "known", "description": "x", "handler": "raw"}],
            "patches": [{
                "artifact_id": "missing", "name": "rule", "signature_hex": "aa",
                "patch_offset": 0, "expected_hex": "aa", "replacement_hex": "bb",
                "description": "x"
            }]
        }
        with self.assertRaisesRegex(ValueError, "unknown artifact"):
            load_catalog(self.write_catalog(invalid))

    def test_catalog_rejects_unknown_fields_and_invalid_byte_patterns(self):
        invalid = {
            "catalog_version": 1,
            "artifacts": [{
                "artifact_id": "known", "description": "x", "handler": "raw",
                "unreviewed_behavior": True,
            }],
            "patches": [{
                "artifact_id": "known", "name": "rule", "signature_hex": "gg",
                "patch_offset": 0, "expected_hex": "aa", "replacement_hex": "bb",
                "description": "x",
            }],
        }
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            load_catalog(self.write_catalog(invalid))

        del invalid["artifacts"][0]["unreviewed_behavior"]
        with self.assertRaisesRegex(ValueError, "invalid byte token"):
            load_catalog(self.write_catalog(invalid))

    def test_catalog_rejects_invalid_identifiers(self):
        invalid = {
            "catalog_version": 1,
            "artifacts": [{"artifact_id": "Not safe", "description": "x", "handler": "raw"}],
            "patches": [],
        }
        with self.assertRaisesRegex(ValueError, "invalid artifact id"):
            load_catalog(self.write_catalog(invalid))

    def test_catalog_rejects_unknown_root_fields_and_inconsistent_rule_sizes(self):
        invalid = {
            "catalog_version": 1,
            "artifacts": [{"artifact_id": "known", "description": "x", "handler": "raw"}],
            "patches": [{
                "artifact_id": "known", "name": "rule", "signature_hex": "aa bb",
                "patch_offset": 0, "expected_hex": "aa", "replacement_hex": "11 22",
                "description": "x",
            }],
            "accidentally_ignored": True,
        }
        with self.assertRaisesRegex(ValueError, "root has unknown fields"):
            load_catalog(self.write_catalog(invalid))

        del invalid["accidentally_ignored"]
        with self.assertRaisesRegex(ValueError, "length differs"):
            load_catalog(self.write_catalog(invalid))


class ReleaseManifestTests(unittest.TestCase):
    def write_manifest(self, document):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "manifest.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_known_exact_release_is_present(self):
        release = next(item for item in RELEASES if item.release_id == "zd1200_10_5_1_0_282")
        self.assertEqual(release.release_id, "zd1200_10_5_1_0_282")
        self.assertEqual(release.decrypted_sha256, "64dfbf4d67cc65cafa0e258e426c664c7387b1219209ec893b9b1e41ab202cb8")
        self.assertEqual(release.artifact_ids, ("zd1200_kernel_elf",))

    def test_release_manifest_rejects_unknown_artifact_or_fields(self):
        document = {
            "manifest_version": 1,
            "releases": [{
                "release_id": "test", "product": "zd1200", "version": "1.2.3.4",
                "build": 5, "support_status": "experimental", "archive_format": "gzip_tar",
                "metadata": {}, "required_paths": ["metadata"],
                "artifact_ids": ["missing"], "features": {},
            }],
        }
        with self.assertRaisesRegex(ValueError, "unknown artifact"):
            load_release_manifest(self.write_manifest(document))

        document = deepcopy(document)
        document["releases"][0]["artifact_ids"] = []
        document["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "root has unknown fields"):
            load_release_manifest(self.write_manifest(document))


class ReleaseArchiveVerificationTests(unittest.TestCase):
    def make_archive(self, members):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "input.img.tgz"
        with tarfile.open(path, "w:gz") as archive:
            for name, contents in members.items():
                info = tarfile.TarInfo(name)
                if contents is None:
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                else:
                    info.size = len(contents)
                    archive.addfile(info, io.BytesIO(contents))
        return path

    def manifest_for(self, archive):
        return ReleaseManifest(
            "synthetic", "zd1200", "1.2.3.4", 5, "experimental", None,
            sha256_file(archive), "gzip_tar", {"VERSION": "1.2.3.4"},
            ("metadata", "firmwares/"), ("zd1200_kernel_elf",), {},
        )

    def test_verify_archive_checks_safe_structure_and_metadata(self):
        archive = self.make_archive({
            "metadata": b"VERSION=1.2.3.4\n",
            "firmwares/": None,
        })
        result = verify_decrypted_archive(archive, self.manifest_for(archive))
        self.assertEqual(result["members"], 2)

        unsafe = self.make_archive({
            "metadata": b"VERSION=1.2.3.4\n",
            "firmwares/": None,
            "../escape": b"no",
        })
        with self.assertRaisesRegex(ValueError, "unsafe archive member path"):
            verify_decrypted_archive(unsafe, self.manifest_for(unsafe))


class TacDecryptionTests(unittest.TestCase):
    @staticmethod
    def encrypt_for_test(plain):
        if len(plain) % 8:
            raise ValueError("test plaintext must be word aligned")
        import ruckus_tac_decrypt as tac

        previous = 0
        xor_value = tac._INITIAL_XOR
        result = bytearray()
        for offset in range(0, len(plain), 8):
            current_plain, = tac._WORD.unpack_from(plain, offset)
            current_cipher = current_plain ^ previous ^ xor_value
            result += tac._WORD.pack(current_cipher)
            xor_value ^= tac._XOR_FLIP
            previous = current_cipher
        return bytes(result)

    def test_tac_round_trip_and_invalid_length(self):
        plain = b"one-word" + b"two-word"
        encrypted = self.encrypt_for_test(plain)
        self.assertEqual(decrypt_bytes(encrypted), plain)
        with self.assertRaisesRegex(ValueError, "multiple of eight"):
            decrypt_bytes(encrypted + b"x")

    def test_tac_file_decryption_is_atomic_on_invalid_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "bad.img"
            destination = directory / "output.tgz"
            source.write_bytes(b"not-aligned")
            destination.write_bytes(b"keep")
            with self.assertRaisesRegex(ValueError, "multiple of eight"):
                decrypt_file(source, destination)
            self.assertEqual(destination.read_bytes(), b"keep")

    def test_tac_file_canonicalizes_word_padding_after_gzip_member(self):
        member = gzip.compress(b"synthetic archive", mtime=0)
        padded = member + b"\x00" * ((-len(member)) % 8)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "encrypted.img"
            destination = directory / "decrypted.tgz"
            source.write_bytes(self.encrypt_for_test(padded))
            self.assertEqual(decrypt_file(source, destination), len(member))
            self.assertEqual(destination.read_bytes(), member)

    def test_encrypted_input_hash_is_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "encrypted.img"
            path.write_bytes(b"fixture")
            release = ReleaseManifest(
                "synthetic", "zd1200", "1.2.3.4", 5, "experimental",
                sha256_file(path), None, "gzip_tar", {}, ("metadata",), (), {},
            )
            self.assertEqual(verify_encrypted_input(path, release), sha256_file(path))
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "encrypted SHA-256 mismatch"):
                verify_encrypted_input(path, release)


class BundleBuilderTests(unittest.TestCase):
    def test_payload_tar_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "firmwares").mkdir()
            (source / "aidfs").mkdir()
            (source / "firmwares" / "r600").write_bytes(b"firmware")
            (source / "aidfs" / "file").write_bytes(b"aidfs")
            (source / "ap-models").write_text("r600\n", encoding="utf-8")
            (source / "file_list.txt").write_text("file\n", encoding="utf-8")
            first, second = root / "one.tgz", root / "two.tgz"
            make_payload(source, first)
            make_payload(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_kernel_builder_matches_known_patched_fixture(self):
        source = Path("/home/dan/src/zd1200/image/bzImage")
        if not source.is_file():
            self.skipTest("local proprietary kernel fixture is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bzImage.patched"
            patch_kernel(source, output)
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                "c3014270e817be56b2b3c79223bbb588ac8c28130662f35c42b77ede2c609803",
            )

    def test_r600_bl7_round_trip_preserves_known_ui_image(self):
        source = Path(
            "/home/dan/src/zd1200/reverse-engineering/r600-wlan/"
            "R600_10.5.1.0.282-unmodified-repack-UNSIGNED.bl7"
        )
        if not source.is_file():
            self.skipTest("local proprietary AP fixture is unavailable")
        original = source.read_bytes()
        image = parse_bl7(original)
        self.assertEqual(image.version, "10.5.1.0.282")
        self.assertEqual(image.rebuild(), original)

    def test_r600_bl7_refuses_signed_image(self):
        source = Path(
            "/home/dan/src/zd1200/reverse-engineering/r600-wlan/"
            "R600_10.5.1.0.282-unmodified-repack-UNSIGNED.bl7"
        )
        if not source.is_file():
            self.skipTest("local proprietary AP fixture is unavailable")
        signed = bytearray(source.read_bytes())
        signed[0x84:0x88] = (1).to_bytes(4, "big")
        with self.assertRaisesRegex(ValueError, "signed ISI/FSI"):
            parse_bl7(bytes(signed))

    def test_r600_control_sizes_follow_overridden_bl7(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = root / "firmwares/r600/10.5.1.0.282/r600_1051_cntrl.rcks"
            control.parent.mkdir(parents=True)
            control.write_text(
                "[rcks_fw.bl7.main]\n0.0.0.0\npath/main\n16682072\n\n"
                "[rcks_fw.bl7.bkup]\n0.0.0.0\npath/bkup\n16682072\n",
                encoding="ascii",
            )
            update_scorpion_control_files(root, {"r600"}, 16666624)
            self.assertNotIn("16682072", control.read_text(encoding="ascii"))
            self.assertEqual(control.read_text(encoding="ascii").count("16666624"), 2)

    def test_scorpion_aliases_require_the_exact_same_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            r600 = root / "firmwares/r600/10.5.1.0.282"
            r500 = root / "firmwares/r500/10.5.1.0.282"
            other = root / "firmwares/t300/10.5.1.0.282"
            r600.mkdir(parents=True)
            r500.mkdir(parents=True)
            other.mkdir(parents=True)
            main = r600 / "rcks_fw.bl7"
            backup = r600 / "rcks_fw.bl7.bkup"
            main.write_bytes(b"main")
            backup.write_bytes(b"backup")
            (r500 / "rcks_fw.bl7").symlink_to(main)
            (r500 / "rcks_fw.bl7.bkup").symlink_to(backup)
            (other / "rcks_fw.bl7").write_bytes(b"different")
            paths, models = scorpion_payload_paths(root)
            self.assertEqual(paths, sorted([backup.resolve(), main.resolve()]))
            self.assertEqual(models, {"r500", "r600"})


class PatchRuleTests(unittest.TestCase):
    def test_patch_and_idempotent_reapplication(self):
        patch = rule("aa bb cc dd", 1, "bb cc", "11 22")
        first, changed = apply_rules(bytes.fromhex("00 aa bb cc dd ff"), [patch], report=lambda *_: None)
        self.assertTrue(changed)
        self.assertEqual(first, bytes.fromhex("00 aa 11 22 dd ff"))
        second, changed = apply_rules(first, [patch], report=lambda *_: None)
        self.assertFalse(changed)
        self.assertEqual(second, first)

    def test_ambiguous_signature_fails_closed(self):
        patch = rule("aa bb cc dd", 1, "bb", "11")
        with self.assertRaisesRegex(ValueError, "original signature matched 2"):
            apply_rules(bytes.fromhex("aa bb cc dd 00 aa bb cc dd"), [patch], report=lambda *_: None)

    def test_mixed_artifacts_are_rejected(self):
        one = rule("aa bb", 0, "aa", "11", artifact="one")
        two = rule("cc dd", 0, "cc", "22", artifact="two")
        with self.assertRaisesRegex(ValueError, "multiple artifacts"):
            apply_rules(bytes.fromhex("aa bb cc dd"), [one, two], report=lambda *_: None)

    def test_rel32_rewrite_preserves_original_exit_target(self):
        # The original exit jump at offset 12 targets byte 30. The patch moves
        # that jump to offset 2, so its displacement must be recalculated.
        source = bytearray(b"\x90\x90\xc7\x04\x24\x00\x00\xe8\x00\x00\x00\x00\xe9")
        source += struct.pack("<i", 30 - 17)
        source += b"\x90" * 20
        patch = rule(
            "90 90 c7 04 24 ?? ?? e8 ?? ?? ?? ?? e9 ?? ?? ?? ??",
            2,
            "c7 04 24 ?? ??",
            "e9 ?? ?? ?? ??",
            rel32_exit=12,
        )
        result, changed = apply_rules(bytes(source), [patch], report=lambda *_: None)
        self.assertTrue(changed)
        displacement = struct.unpack_from("<i", result, 3)[0]
        self.assertEqual(2 + 5 + displacement, 30)


class ContainerTests(unittest.TestCase):
    def test_finds_and_rebuilds_elf_gzip_member_without_moving_suffix(self):
        payload = b"\x7fELF\x01" + b"A" * 4096
        member = gzip.compress(payload, compresslevel=1, mtime=1)
        source = b"PREFIX" + member + b"SUFFIX"
        start, end, found = find_elf_member(source)
        self.assertEqual(found, payload)
        patched_payload = payload[:-1] + b"B"
        rebuilt = rebuild_artifact(
            source,
            patched_payload,
            "zd1200_kernel_elf",
            (start, end),
        )
        self.assertEqual(len(rebuilt), len(source))
        self.assertEqual(rebuilt[:start], b"PREFIX")
        self.assertEqual(rebuilt[end:], b"SUFFIX")
        _new_start, _new_end, extracted = find_elf_member(rebuilt)
        self.assertEqual(extracted, patched_payload)


if __name__ == "__main__":
    unittest.main()
