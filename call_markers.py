import re
from dataclasses import dataclass


_MARKER_PATTERN = re.compile(
    r"(?:⟪|【|\[|<)\s*(挂断|结束通话|hang\s*up|hangup)\s*(?:⟫|】|\]|>)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class CallMarkerResult:
    text: str
    hangup: bool


def extract_call_markers(value: str) -> CallMarkerResult:
    """Remove private call-control markers before captions and TTS see them."""

    text = str(value or "")
    hangup = bool(_MARKER_PATTERN.search(text))
    visible = _MARKER_PATTERN.sub("", text)
    visible = re.sub(r"[ \t]+\n", "\n", visible)
    visible = re.sub(r"\n{3,}", "\n\n", visible).strip()
    return CallMarkerResult(text=visible, hangup=hangup)
