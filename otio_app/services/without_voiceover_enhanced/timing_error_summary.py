"""Menschlesbare Zusammenfassung von Python-Timing-Fehlern."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "TimingIssueGroup",
    "classify_timing_errors",
    "format_grouped_timing_errors",
    "format_timing_error_overview",
    "split_timing_error_blob",
    "timing_failure_headline",
]


@dataclass
class TimingIssueGroup:
    category: str
    title: str
    explanation: str
    next_step: str
    items: list[str] = field(default_factory=list)


_MINI_SHORTFALL_SEC = 1.01

_BATCH_HEADER_RE = re.compile(
    r"(?P<fail>\d+)\s*/\s*(?P<total>\d+)\s+Python-Timing(?:\(s\))?"
    r"(?:\s+fehlgeschlagen[^\n]*)?:?\s*",
    re.IGNORECASE,
)
_SHORT_RE = re.compile(
    r"(?P<slot>\S+_slot_\d+):\s*Asset\s+(?P<asset>\S+)\s+zu kurz\s+"
    r"\(nutzbar\s+(?P<usable>[\d.]+)s\s*<\s*nötig\s+(?P<need>[\d.]+)s",
    re.IGNORECASE,
)
_INSPECT_SHORT_RE = re.compile(
    r"(?P<slot>\S+_slot_\d+)\s*:\s*(?P<body>.*?)"
    r"nutzbar\s+(?P<usable>[\d.]+)s,\s*Slot braucht\s+(?P<need>[\d.]+)s",
    re.IGNORECASE,
)
_NO_ASSET_RE = re.compile(
    r"(?P<slot>\S+_slot_\d+)\s*:\s*kein Asset"
    r"(?:\s*[—–-]\s*es fehlen\s+(?P<missing>[\d.]+)s)?",
    re.IGNORECASE,
)
_CLAMP_RE = re.compile(
    r"slot\[(?P<idx>\d+)\]:\s*span\s+(?P<span>[\d.]+)s\s*>\s*usable\s+"
    r"(?P<usable>[\d.]+)s.*?shortfall\s+(?P<short>[\d.]+)s",
    re.IGNORECASE,
)
_UNKNOWN_ID_RE = re.compile(
    r"Unbekannte Asset-ID:\s*(?P<asset>\S+)",
    re.IGNORECASE,
)
_SLOT_TOKEN_RE = re.compile(
    r"(?:slot\[(?P<idx>\d+)\]|(?P<named>\S+_slot_\d+))",
    re.IGNORECASE,
)
_GAP_RE = re.compile(
    r"(?P<label>Führende|Abschließende|Visuelle)?\s*visuelle Lücke|"
    r"visuelle Lücke",
    re.IGNORECASE,
)
_BRIDGE_RE = re.compile(
    r"unterschiedlichen Kapiteln|bridge_\d+",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(Pfad|/Users/|/home/|[A-Za-z]:\\)\S+")
_SLOT_START_RE = re.compile(
    r"(?:slot\[\d+\]|\S+_slot_\d+)\s*:",
    re.IGNORECASE,
)
_GAP_CHAPTER_RE = re.compile(r"in Kapitel\s+(?P<chap>[^:]+)", re.IGNORECASE)
_GAP_TIMES_RE = re.compile(
    r"letzter Shot endet\s+(?P<end>[\d.]+)s,\s*Audio bis\s+(?P<audio>[\d.]+)s",
    re.IGNORECASE,
)
_BRIDGE_PAIR_RE = re.compile(
    r"unterschiedlichen Kapiteln\s*\((?P<a>[^)]+?)\s+vs\s+(?P<b>[^)]+)\)",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*[-•]\s+", re.MULTILINE)


def split_timing_error_blob(blob: str | Exception) -> list[str]:
    if hasattr(blob, "errors") and getattr(blob, "errors"):
        return [str(x).strip() for x in blob.errors if str(x).strip()]
    text = str(blob or "").strip()
    if not text:
        return []
    text = _BATCH_HEADER_RE.sub("", text, count=1).strip()
    if _BULLET_RE.search(text):
        chunks = [p.strip(" \n-•") for p in _BULLET_RE.split(text) if p.strip(" \n-•")]
    elif "\n" in text:
        chunks = [p.strip() for p in text.splitlines() if p.strip()]
    else:
        chunks = [
            p.strip()
            for p in re.split(
                r"\s*;\s*(?=(?:[A-Za-z0-9_]+_slot_\d+|bridge_\d+|Abschließende|"
                r"Führende|Visuelle|Kapitel|Asset\s|slot\[))",
                text,
            )
            if p.strip()
        ]
    out: list[str] = []
    current_chapter = ""
    for chunk in chunks:
        chapter, rest = _split_chapter_prefix(chunk)
        if chapter:
            current_chapter = chapter
        elif current_chapter and not _looks_like_slot_start(chunk):
            rest = chunk
        else:
            rest = chunk
        fragments = _split_slot_fragments(rest)
        if not fragments:
            fragments = [rest]
        for fragment in fragments:
            piece = fragment.strip()
            if not piece:
                continue
            if current_chapter and not _split_chapter_prefix(piece)[0]:
                out.append(f"{current_chapter}: {piece}")
            else:
                out.append(piece)
    return out


def _looks_like_slot_start(text: str) -> bool:
    stripped = text.strip()
    return bool(re.match(r"(slot\[\d+\]|\S+_slot_\d+)", stripped, re.IGNORECASE))


def _split_chapter_prefix(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if ": " not in raw:
        return "", raw
    left, right = raw.split(": ", 1)
    left = left.strip().strip("„“\"")
    if not left or _looks_like_slot_start(left) or "_slot_" in left:
        return "", raw
    if left.lower().startswith("python timing"):
        return "", raw
    if any(
        token in left.lower()
        for token in (
            "lücke",
            "visuelle",
            "shortfall",
            "toleranz",
            "asset-id",
            "unbekannte",
        )
    ):
        return "", raw
    if len(left) > 80:
        return "", raw
    return left, right.strip()


def _split_slot_fragments(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    matches = list(_SLOT_START_RE.finditer(raw))
    if not matches:
        return [raw]
    fragments: list[str] = []
    prefix = raw[: matches[0].start()].strip(" ,;.")
    if prefix:
        fragments.append(prefix)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        piece = raw[match.start() : end].strip(" ,;.")
        if piece:
            fragments.append(piece)
    return fragments


def _slot_label(slot: str) -> str:
    text = str(slot or "").strip()
    numbered = re.search(r"slot[_\[ ](\d+)", text, re.IGNORECASE)
    if numbered:
        return f"Slot {int(numbered.group(1))}"
    return text or "Slot"


def _fmt_sec(value: str | float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number))}s"
    return f"{number:.1f}s".replace(".0s", "s")


def _with_chapter(chapter: str, body: str) -> str:
    chap = (chapter or "").strip()
    if chap:
        return f"**{chap}** · {body}"
    return body


def _filename_hint(message: str) -> str:
    path_match = re.search(r"Pfad\s+(\S+)", message)
    if path_match:
        return f" · `{Path(path_match.group(1)).name}`"
    file_match = re.search(
        r"·\s+([^·\n]+?\.(?:mp4|mov|m4v|jpg|jpeg|png|webm))\b",
        message,
        re.IGNORECASE,
    )
    if file_match:
        return f" · `{Path(file_match.group(1).strip()).name}`"
    return ""


def _strip_paths(message: str) -> str:
    cleaned = _PATH_RE.sub("", message)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ·")
    cleaned = re.sub(
        r"asset__[a-z0-9_]+__[a-z0-9_]+__[a-f0-9]{6,}",
        lambda m: m.group(0).split("__")[2] if "__" in m.group(0) else m.group(0),
        cleaned,
    )
    cleaned = re.sub(
        r"Unter „Zu kurze Clips ansehen“[^.]*\.",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"Im Funnel längeres Material holen[^.]*\.",
        "",
        cleaned,
    )
    return cleaned.strip()


def _short_item(message: str, *, chapter: str = "") -> str:
    match = _SHORT_RE.search(message) or _INSPECT_SHORT_RE.search(message)
    if not match:
        return _with_chapter(chapter, _strip_paths(message))
    slot = _slot_label(match.group("slot"))
    usable = _fmt_sec(match.group("usable"))
    need = _fmt_sec(match.group("need"))
    try:
        missing = max(0.0, float(match.group("need")) - float(match.group("usable")))
        missing_bit = f" — es fehlen {_fmt_sec(missing)}"
    except (TypeError, ValueError):
        missing_bit = ""
    return _with_chapter(
        chapter,
        f"{slot}: Clip {usable}, Sprecher {need}{missing_bit}"
        f"{_filename_hint(message)}",
    )


def _missing_item(message: str, *, chapter: str = "") -> str:
    match = _NO_ASSET_RE.search(message)
    if not match:
        return _with_chapter(chapter, _strip_paths(message))
    slot = _slot_label(match.group("slot"))
    missing = match.group("missing")
    extra = f" — es fehlen {_fmt_sec(missing)}" if missing else ""
    return _with_chapter(chapter, f"{slot}: kein Video/Foto gewählt{extra}")


def _clamp_item(message: str, *, chapter: str = "") -> str:
    match = _CLAMP_RE.search(message)
    if not match:
        slot_match = _SLOT_TOKEN_RE.search(message)
        slot = _slot_label(slot_match.group(0)) if slot_match else "Slot"
        return _with_chapter(
            chapter,
            f"{slot}: Mini-Zeit ließ sich nicht auf die Nachbarclips legen",
        )
    slot = _slot_label(f"slot_{match.group('idx')}")
    short = _fmt_sec(match.group("short"))
    return _with_chapter(
        chapter,
        f"{slot}: Clip {_fmt_sec(match.group('usable'))}, "
        f"Sprecher {_fmt_sec(match.group('span'))} — es fehlen {short}",
    )


def _unknown_item(message: str, *, chapter: str = "") -> str:
    match = _UNKNOWN_ID_RE.search(message)
    asset = match.group("asset") if match else ""
    extra = f" (`{asset}`)" if asset else ""
    return _with_chapter(chapter, f"Gewähltes Asset ist unbekannt{extra}")


def _gap_item(message: str, *, chapter: str = "") -> str:
    chap = (chapter or "").strip()
    if not chap:
        found = _GAP_CHAPTER_RE.search(message)
        if found:
            chap = found.group("chap").strip()
    times = _GAP_TIMES_RE.search(message)
    if times:
        end = _fmt_sec(times.group("end"))
        audio = _fmt_sec(times.group("audio"))
        try:
            missing = max(
                0.0, float(times.group("audio")) - float(times.group("end"))
            )
            extra = f" — {_fmt_sec(missing)} ohne Bild"
        except (TypeError, ValueError):
            extra = ""
        lower = message.lower()
        if "abschließende" in lower:
            where = "am Ende: "
        elif "führende" in lower:
            where = "am Anfang: "
        else:
            where = ""
        return _with_chapter(
            chap,
            f"{where}letzter Clip bis {end}, Sprecher bis {audio}{extra}",
        )
    return _with_chapter(chap, _strip_paths(message))


def _bridge_item(message: str, *, chapter: str = "") -> str:
    pair = _BRIDGE_PAIR_RE.search(message)
    if pair:
        left = pair.group("a").strip()
        right = pair.group("b").strip()
        return _with_chapter(chapter, f"Übergang zwischen {left} und {right}")
    return _with_chapter(chapter, _strip_paths(message))


def _shortfall_seconds(message: str) -> float | None:
    inspect = _INSPECT_SHORT_RE.search(message)
    if inspect:
        try:
            return max(
                0.0, float(inspect.group("need")) - float(inspect.group("usable"))
            )
        except (TypeError, ValueError):
            return None
    short = _SHORT_RE.search(message)
    if short:
        try:
            return max(0.0, float(short.group("need")) - float(short.group("usable")))
        except (TypeError, ValueError):
            return None
    clamp = _CLAMP_RE.search(message)
    if clamp:
        try:
            return float(clamp.group("short"))
        except (TypeError, ValueError):
            return None
    return None


def _is_boilerplate_only(text: str) -> bool:
    """Header ohne konkreten Slot — der steht in den Folge-Fragmenten."""
    stripped = str(text or "").strip(" :·—–-")
    if not stripped:
        return True
    if _SLOT_TOKEN_RE.search(text) or _NO_ASSET_RE.search(text) or _CLAMP_RE.search(text):
        return False
    if _INSPECT_SHORT_RE.search(text) or _SHORT_RE.search(text):
        return False
    lower = stripped.lower()
    if lower in {"betroffen", "details", "hinweis"}:
        return True
    return (
        "nicht exportfähig" in lower
        or "placeholder/shortfall" in lower
        or "im funnel längeres" in lower
        or "zu kurze clips ansehen" in lower
    )


def _empty_groups() -> dict[str, TimingIssueGroup]:
    return {
        "missing_asset": TimingIssueGroup(
            category="missing_asset",
            title="Kein Clip für diesen Abschnitt",
            explanation=(
                "Für diese Stelle im Film wurde kein Video und kein Foto gelegt. "
                "Der Sprecher läuft, das Bild fehlt."
            ),
            next_step=(
                "Im Funnel oder per Hand ein passendes Video/Foto zuweisen. "
                "Danach Python Timing für dieses Kapitel erneut starten."
            ),
        ),
        "short_mini": TimingIssueGroup(
            category="short_mini",
            title="Nur ein winziger Rest fehlt (unter 1s)",
            explanation=(
                "Das Video reicht fast — es fehlen nur Bruchteile einer Sekunde. "
                "Python kann diese Mini-Zeit normalerweise auf den Clip davor "
                "oder danach legen."
            ),
            next_step=(
                "Python Timing für das Kapitel noch einmal starten. "
                "Das vorgesehene Video siehst du unter „Zu kurze Clips ansehen“. "
                "Wenn es bleibt: die Nachbarclips sind selbst zu knapp, "
                "dann im Funnel etwas Längeres holen."
            ),
        ),
        "short_asset": TimingIssueGroup(
            category="short_asset",
            title="Clip zu kurz für den Sprecher",
            explanation=(
                "Das gewählte Video ist kürzer als der Sprecher in diesem "
                "Abschnitt. Deshalb bleibt ein rotes Stück in der Timeline."
            ),
            next_step=(
                "Unter „Zu kurze Clips ansehen“ das Video prüfen. "
                "Im Funnel ein längeres Video holen, dann Python Timing erneut."
            ),
        ),
        "clamp_unstable": TimingIssueGroup(
            category="clamp_unstable",
            title="Mini-Zeit ließ sich nicht auf Nachbarn legen",
            explanation=(
                "Es fehlt nur wenig, und das liegt noch in der Toleranz. "
                "Python legt die Mini-Zeit zuerst auf den Clip davor/danach, "
                "und wenn die selbst knapp sind, auf die nächsten Clips daneben. "
                "Hier hat das nicht gereicht."
            ),
            next_step=(
                "Python Timing erneut starten. "
                "Wenn es bleibt: auch zwei Clips weiter sind zu knapp. "
                "Dann im Funnel längeres Material holen."
            ),
        ),
        "unknown_id": TimingIssueGroup(
            category="unknown_id",
            title="Unbekanntes Asset",
            explanation=(
                "Der Cut-Plan nennt eine Datei-ID, die im Inventar nicht "
                "vorkommt. Ohne Datei kann Timing den Slot nicht legen."
            ),
            next_step=(
                "LLM Cut für das Kapitel neu erzeugen, oder die Datei "
                "ins Kapitel legen und Inventar aktualisieren."
            ),
        ),
        "visual_gap": TimingIssueGroup(
            category="visual_gap",
            title="Bildlücke bei laufendem Sprecher",
            explanation=(
                "Zwischen zwei Einstellungen oder am Kapitelende fehlt Bild, "
                "während der Sprecher weiterredet."
            ),
            next_step=(
                "Zuerst fehlende oder zu kurze Clips schließen, "
                "dann Python Timing erneut."
            ),
        ),
        "chapter_bridge": TimingIssueGroup(
            category="chapter_bridge",
            title="Kapitelübergang",
            explanation=(
                "Zwischen zwei Kapiteln liegt ein Übergang. Das ist kein "
                "normaler Inhalts-Shot."
            ),
            next_step="Meist ignorierbar. Nur füllen, wenn der Übergang schwarz bleibt.",
        ),
        "other": TimingIssueGroup(
            category="other",
            title="Weitere Timing-Hinweise",
            explanation="Sonstige Prüfungen, die nicht in die Gruppen oben passen.",
            next_step="Den betreffenden Slot prüfen und Plan oder Medien korrigieren.",
        ),
    }


def classify_timing_errors(messages: list[str] | str | Exception) -> list[TimingIssueGroup]:
    """Gruppiert Roh-Fehler in wenige, erklärbare Blöcke."""
    if isinstance(messages, (str, Exception)):
        items = split_timing_error_blob(messages)
    else:
        items = []
        for raw in messages or []:
            items.extend(split_timing_error_blob(str(raw)))
    groups = _empty_groups()

    for message in items:
        chapter, rest = _split_chapter_prefix(message)
        body = rest or message
        if _is_boilerplate_only(body):
            continue
        lower = body.lower()
        if _NO_ASSET_RE.search(body) or "kein asset" in lower:
            groups["missing_asset"].items.append(
                _missing_item(body, chapter=chapter)
            )
        elif "grenzen-klemme nicht stabil" in lower or (
            "innerhalb toleranz" in lower and "shortfall" in lower
        ):
            groups["clamp_unstable"].items.append(
                _clamp_item(body, chapter=chapter)
            )
        elif _UNKNOWN_ID_RE.search(body):
            groups["unknown_id"].items.append(
                _unknown_item(body, chapter=chapter)
            )
        elif (
            "zu kurz" in lower
            or "placeholder/shortfall" in lower
            or "nicht exportfähig" in lower
            or _INSPECT_SHORT_RE.search(body)
        ):
            missing = _shortfall_seconds(body)
            target = (
                "short_mini"
                if missing is not None and missing <= _MINI_SHORTFALL_SEC
                else "short_asset"
            )
            groups[target].items.append(_short_item(body, chapter=chapter))
        elif _BRIDGE_RE.search(body):
            groups["chapter_bridge"].items.append(
                _bridge_item(body, chapter=chapter)
            )
        elif _GAP_RE.search(body) or "lücke" in lower:
            groups["visual_gap"].items.append(
                _gap_item(body, chapter=chapter)
            )
        else:
            groups["other"].items.append(
                _with_chapter(chapter, _strip_paths(body))
            )

    return [g for g in groups.values() if g.items]


def timing_failure_headline(messages: list[str] | str | Exception) -> str:
    """Kurze Kopfzeile für die Fehlerbox."""
    text = ""
    if isinstance(messages, (str, Exception)):
        text = str(messages or "")
    elif messages:
        text = "\n".join(str(m) for m in messages)
    match = _BATCH_HEADER_RE.search(text)
    if match:
        failed = int(match.group("fail"))
        total = int(match.group("total"))
        ok = max(0, total - failed)
        chapter_word = "Kapitel" if failed != 1 else "Kapitel"
        return (
            f"{failed} von {total} {chapter_word}: Timing nicht fertig "
            f"({ok} ok)"
        )
    groups = classify_timing_errors(messages)
    count = sum(len(group.items) for group in groups)
    if count <= 0:
        return "Python-Timing: Probleme gefunden"
    problem = "Problem" if count == 1 else "Probleme"
    return f"Python-Timing: {count} {problem} gefunden"


def format_timing_error_overview(messages: list[str] | str | Exception) -> str:
    """Kompakte Textübersicht (eine Zeile pro Gruppe)."""
    groups = classify_timing_errors(messages)
    if not groups:
        return ""
    parts = [f"{g.title}: {len(g.items)}" for g in groups]
    return " · ".join(parts)


def format_grouped_timing_errors(messages: list[str] | str | Exception) -> str:
    """Volle gruppierte Übersicht als Markdown (Tests + Logs)."""
    groups = classify_timing_errors(messages)
    if not groups:
        return ""
    lines = [f"**{timing_failure_headline(messages)}**", ""]
    for group in groups:
        lines.append(f"### {group.title} ({len(group.items)})")
        lines.append(f"Was los ist: {group.explanation}")
        lines.append(f"Was du tun kannst: {group.next_step}")
        for item in group.items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).strip()
