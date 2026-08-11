"""Shared ingest hardening — H2 + H5 (`35-outreach-crm.md` §11).

Applied at the shared ingest core (`_lib/ingest.ingest_note`) so **every** untrusted
channel is hardened once: `#capture`, Granola, Tartt content, and outreach/email/
Drive when they land.

- **H2 (`harden`)** — NFKC-normalize, then strip zero-width chars, bidi overrides,
  and the U+E0000 tag block. Deterministic and always safe (cleaning never harms
  legitimate text). The threat is no longer just model-steering: a bidi override
  makes displayed text differ from what's pasted, zero-width chars survive
  copy-paste, and both end up in an email a human sends.
- **H5 (`screen`)** — flags prompt-injection-shaped text (instruction patterns,
  base64 blobs, mixed confusable scripts) before it reaches the extraction LLM.
  Returns findings; the caller logs (→ `#system`) and can decay source trust (H7).

Pure and dependency-free (stdlib `re`/`unicodedata`), so it's unit-tested without
a DB or cognee.
"""

from __future__ import annotations

import re
import unicodedata

# --- H2: characters stripped at ingest --------------------------------------
# Built from chr() codepoints on purpose — these are exactly the invisible chars
# this module strips, so keeping literals out of the source keeps it readable + safe.

# ZWSP, ZWNJ, ZWJ, word-joiner, BOM/ZWNBSP.
_ZERO_WIDTH = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
# LRE, RLE, PDF, LRO, RLO + LRI, RLI, FSI, PDI (bidi overrides + isolates).
_BIDI = "".join(
    chr(c) for c in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069)
)
_TAG_BLOCK = "".join(chr(c) for c in range(0xE0000, 0xE0080))  # invisible tag chars
_STRIP = str.maketrans("", "", _ZERO_WIDTH + _BIDI + _TAG_BLOCK)


def harden(text: str) -> tuple[str, int]:
    """H2: NFKC-normalize + strip invisible/bidi/tag chars.

    Returns `(cleaned_text, n_chars_removed)`. n_removed>0 is worth logging (and
    feeds H7 source-trust decay).
    """
    normalized = unicodedata.normalize("NFKC", text)
    cleaned = normalized.translate(_STRIP)
    return cleaned, len(normalized) - len(cleaned)


# --- H5: injection screening -------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+|the\s+|previous\s+)*(instructions|prompts?)\b", re.I),
    re.compile(r"\b(disregard|forget)\s+(everything|all|the|prior|previous|above)\b", re.I),
    re.compile(r"\byou\s+(must|are\s+required\s+to|should\s+now)\b", re.I),
    re.compile(r"\b(instead|now)\s+(do|follow)\s+the\s+following\b", re.I),
    re.compile(r"^\s*system\s*:", re.I | re.M),
]
_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


def _has_confusable_scripts(text: str) -> bool:
    """True if letters mix Latin with Cyrillic/Greek — a homoglyph phishing signal."""
    scripts: set[str] = set()
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("LATIN"):
            scripts.add("latin")
        elif name.startswith("CYRILLIC"):
            scripts.add("cyrillic")
        elif name.startswith("GREEK"):
            scripts.add("greek")
    return "latin" in scripts and len(scripts) > 1


def screen(text: str) -> list[str]:
    """H5: flags of injection-shaped content — empty means clean. Non-blocking;
    the caller decides to log / quarantine / decay trust."""
    findings: list[str] = []
    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            findings.append("injection-pattern")
            break
    if _BASE64_BLOB.search(text):
        findings.append("base64-blob")
    if _has_confusable_scripts(text):
        findings.append("mixed-scripts")
    return findings
