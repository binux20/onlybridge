from __future__ import annotations

from functools import lru_cache

import tiktoken


@lru_cache(maxsize=1)
def _enc():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    return len(_enc().encode(text, disallowed_special=()))
