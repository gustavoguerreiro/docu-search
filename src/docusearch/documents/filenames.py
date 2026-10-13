"""Display-name sanitization for uploaded files.

The result is only ever shown back to the owner; it is never used to build a path (storage keys
are generated). The goal is a name that is safe to display and to put in a header: no directory
parts, no control or direction-override characters, bounded length.
"""

import re
import unicodedata

MAX_FILENAME_LENGTH = 255
FALLBACK_FILENAME = "unnamed"

_RESERVED = re.compile(r'[<>:"|?*]')
_SPACES = re.compile(r"\s+")


def sanitize_filename(raw: str | None) -> str:
    if not raw:
        return FALLBACK_FILENAME

    name = unicodedata.normalize("NFC", raw)
    # Browsers and scripts sometimes send a whole path; keep only the last component.
    name = re.split(r"[\\/]", name)[-1]
    name = name.replace("\t", " ")  # a tab separates words; other controls are dropped below
    # Drop control (Cc) and format (Cf) characters: NUL, newlines, and the bidirectional overrides
    # used to disguise extensions ("evil‮gnp.exe").
    name = "".join(ch for ch in name if unicodedata.category(ch) not in {"Cc", "Cf"})
    name = _RESERVED.sub("_", name)
    name = _SPACES.sub(" ", name).strip(" .")

    if not name:
        return FALLBACK_FILENAME
    return _truncate(name, MAX_FILENAME_LENGTH)


def _truncate(name: str, limit: int) -> str:
    if len(name) <= limit:
        return name
    stem, dot, extension = name.rpartition(".")
    # Keep a sane extension; otherwise just cut.
    if dot and 0 < len(extension) <= 10:
        return stem[: limit - len(extension) - 1] + "." + extension
    return name[:limit]
