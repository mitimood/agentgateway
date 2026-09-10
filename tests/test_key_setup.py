"""Check first-start generation, safe restarts, and broken-volume behavior."""

from pathlib import Path
import stat
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization

from scripts.setup_backend_auth import setup_keys


class KeySetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.private_dir = self.root / "private"
        self.public_dir = self.root / "public"
        self.private = self.private_dir / "gateway-private.pem"
        self.public = self.public_dir / "gateway-public.pem"

    def setup_keys(self):
        setup_keys(self.private_dir, self.public_dir)

    def test_fresh_key_pair_matches_and_restart_preserves_identity(self):
        self.setup_keys()
        private = self.private.read_bytes()
        public = self.public.read_bytes()
        key = serialization.load_pem_private_key(private, password=None)
        self.assertEqual(key.key_size, 3072)
        self.assertEqual(key.public_key().public_numbers(),
                         serialization.load_pem_public_key(public).public_numbers())
        self.assertEqual(stat.S_IMODE(self.private.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(self.private_dir.stat().st_mode), 0o700)
        self.assertFalse((self.public_dir / self.private.name).exists())
        self.setup_keys()
        self.assertEqual(self.private.read_bytes(), private)
        self.assertEqual(self.public.read_bytes(), public)
        setup_keys(self.root / "other-private", self.root / "other-public")
        self.assertNotEqual((self.root / "other-public" / self.public.name).read_bytes(), public)

    def test_missing_public_key_is_recovered_from_existing_private_key(self):
        self.setup_keys()
        original = self.public.read_bytes()
        self.public.unlink()
        self.setup_keys()
        self.assertEqual(self.public.read_bytes(), original)

    def test_mismatched_or_missing_private_key_fails_closed(self):
        self.setup_keys()
        private = self.private.read_bytes()
        self.public.chmod(0o600)
        self.public.write_bytes(b"unexpected key")
        with self.assertRaises(ValueError):
            self.setup_keys()
        self.assertEqual(self.private.read_bytes(), private)
        self.assertEqual(self.public.read_bytes(), b"unexpected key")
        self.private.unlink()
        with self.assertRaises(ValueError):
            self.setup_keys()
        self.assertFalse(self.private.exists())

    def test_corrupt_private_key_is_not_replaced(self):
        self.setup_keys()
        self.private.chmod(0o600)
        self.private.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.setup_keys()
        self.assertEqual(self.private.read_bytes(), b"corrupt")


if __name__ == "__main__":
    unittest.main()
