#!/usr/bin/env python3
"""Initialize persistent Docker key volumes; never export keys into the checkout."""

import argparse
import fcntl
import os
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def write_key(path: Path, data: bytes, mode: int, owner_uid: int | None = None):
    # Publish complete files, so an interrupted initializer can safely be rerun.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            os.fchmod(output.fileno(), mode)
            if owner_uid is not None:
                os.fchown(output.fileno(), owner_uid, -1)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def setup_keys(private_dir: Path, public_dir: Path, gateway_uid: int | None = None):
    private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    public_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    private_dir.chmod(0o700)
    public_dir.chmod(0o755)
    if gateway_uid is not None:
        os.chown(private_dir, gateway_uid, -1)
    private_path = private_dir / "gateway-private.pem"
    public_path = public_dir / "gateway-public.pem"
    # Explicit simultaneous runs must agree on the same identity too.
    with (private_dir / ".setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if private_path.exists():
            key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 3072:
                raise ValueError("Expected an RSA signing key of at least 3072 bits")
        elif public_path.exists():
            raise ValueError("Private key is missing; refusing to replace the existing identity")
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            write_key(private_path, key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ), 0o400, gateway_uid)
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if public_path.exists() and public_path.read_bytes() != public:
            raise ValueError("Public key does not match the signing key; refusing to overwrite it")
        if not public_path.exists():
            write_key(public_path, public, 0o444)
        private_path.chmod(0o400)
        public_path.chmod(0o444)
        if gateway_uid is not None:
            os.chown(private_path, gateway_uid, -1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, default=Path("/run/keys/private"))
    parser.add_argument("--public-dir", type=Path, default=Path("/run/keys/public"))
    parser.add_argument("--gateway-uid", type=int)
    args = parser.parse_args()
    os.umask(0o077)
    setup_keys(args.private_dir, args.public_dir, args.gateway_uid)
    print("Gateway key pair ready in Docker volumes (existing identity preserved).")


if __name__ == "__main__":
    main()
