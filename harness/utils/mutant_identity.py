from __future__ import annotations

import hashlib


def normalize_mutant_code(code: str) -> str:
    return code.strip().replace("\r\n", "\n").replace("\r", "\n")


def compute_mutant_hash(code: str) -> str:
    normalized = normalize_mutant_code(code)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
