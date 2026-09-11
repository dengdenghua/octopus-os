from __future__ import annotations

import hashlib


def hash_password(plaintext: str) -> str:
    """Hash a password using bcrypt with a random salt."""
    import bcrypt

    pw_bytes = plaintext.encode("utf-8")
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12))
    return f"bcrypt:{hashed.decode('utf-8')}"


def verify_password(plaintext: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt or legacy SHA-256 hash."""
    if hashed.startswith("bcrypt:"):
        import bcrypt

        stored = hashed[7:].encode("utf-8")
        return bcrypt.checkpw(plaintext.encode("utf-8"), stored)

    legacy = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return hashed == legacy or hashed == f"sha256:{legacy}"
