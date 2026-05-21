"""Trust-score for synkede Confluence-sider.

Bygger på blame-data: hver linje har attribusjon (versjon, dato), og hver
side har en versjons-liste. Score er en vektet kombinasjon av linje-recency,
doc-type og versjons-stabilitet.

Vekter, halveringstid og type-mønstre er konfigurerbare via
.gonfluence-trust.json i docs-roten. Defaults gir fornuftige tall uten config.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from atlassinate.blame import BlameLine

CONFIG_FILENAME = ".gonfluence-trust.json"
CACHE_FILENAME = ".gonfluence-trust-cache.json"

DEFAULT_HALF_LIFE_DAYS = 365.0

DEFAULT_TYPE_PATTERNS: list[tuple[str, float]] = [
    (r"\b(kladd|draft|wip|sandbox|playground)\b", 0.4),
    (r"\b(m[øo]tereferat|referat|notat)\b", 0.5),
    (r"\b(spesifikasjon|policy|standard|krav|arkitektur|adr)\b", 1.0),
]

DEFAULT_AGGREGATION_WEIGHTS: dict[str, float] = {
    "recency": 0.5,
    "doc_type": 0.2,
    "stability": 0.3,
}

DEFAULT_LEVEL_THRESHOLDS: list[tuple[float, str]] = [
    (0.85, "A"),
    (0.70, "B"),
    (0.55, "C"),
    (0.40, "D"),
    (0.0,  "F"),
]


@dataclass
class TrustConfig:
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    type_patterns: list[tuple[str, float]] = field(
        default_factory=lambda: list(DEFAULT_TYPE_PATTERNS)
    )
    default_type_weight: float = 0.7
    weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_AGGREGATION_WEIGHTS)
    )

    @classmethod
    def load(cls, docs_dir: Path) -> "TrustConfig":
        path = docs_dir / CONFIG_FILENAME
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()

        cfg = cls()
        if "half_life_days" in data:
            cfg.half_life_days = float(data["half_life_days"])
        if "type_patterns" in data:
            cfg.type_patterns = [(p["pattern"], float(p["weight"])) for p in data["type_patterns"]]
        if "default_type_weight" in data:
            cfg.default_type_weight = float(data["default_type_weight"])
        if "weights" in data:
            cfg.weights = {**DEFAULT_AGGREGATION_WEIGHTS, **data["weights"]}
        return cfg


@dataclass
class ScoreComponents:
    recency: float
    doc_type: float
    stability: float


@dataclass
class TrustScore:
    total: float
    level: str
    components: ScoreComponents
    flags: list[str]
    stats: dict


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def recency_score(created_at: str, now: datetime, half_life_days: float) -> float:
    """Eksponentiell halvering: ferske datoer ~1.0, gamle datoer mot 0."""
    dt = _parse_iso(created_at)
    if dt is None:
        return 0.5  # ukjent dato → nøytral
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return math.exp(-math.log(2) * age_days / half_life_days)


def doc_type_score(title: str, config: TrustConfig) -> tuple[float, str | None]:
    """Returner (score, matched_pattern_name) basert på tittel-regex."""
    haystack = title.lower()
    for pattern, weight in config.type_patterns:
        if re.search(pattern, haystack, re.IGNORECASE):
            return weight, pattern
    return config.default_type_weight, None


def stability_score(versions: list[dict], now: datetime, half_life_days: float) -> tuple[float, dict]:
    """Score basert på antall unike redaktører, versjons-volum og siste oppdatering.

    Returnerer (score, stats) der stats inneholder utledede tall.
    """
    if not versions:
        return 0.0, {"version_count": 0, "unique_editors": 0, "latest_at": ""}

    editor_ids = [v.get("authorId") for v in versions if v.get("authorId")]
    unique_editors = len(set(editor_ids))
    version_count = len(versions)
    latest_at = max((v.get("createdAt", "") for v in versions), default="")

    editors_factor = min(1.0, unique_editors / 3.0)
    versions_factor = min(1.0, math.log(version_count + 1) / math.log(10))
    recency_factor = recency_score(latest_at, now, half_life_days)

    score = editors_factor * 0.4 + versions_factor * 0.3 + recency_factor * 0.3
    return score, {
        "version_count": version_count,
        "unique_editors": unique_editors,
        "latest_at": latest_at,
    }


def _level_for(total: float) -> str:
    for threshold, letter in DEFAULT_LEVEL_THRESHOLDS:
        if total >= threshold:
            return letter
    return "F"


def compute_trust(
    title: str,
    blame_lines: list[BlameLine],
    versions: list[dict],
    config: TrustConfig | None = None,
    now: datetime | None = None,
) -> TrustScore:
    """Beregn trust-score for en side.

    `blame_lines` er attributert linjeliste fra blame.compute_blame.
    `versions` er versjons-metadata fra api.get_page_versions.
    """
    config = config or TrustConfig()
    now = now or datetime.now(timezone.utc)

    if blame_lines:
        recency_vals: list[float] = []
        old_line_count = 0
        for bl in blame_lines:
            recency_vals.append(
                recency_score(bl.attribution.created_at, now, config.half_life_days)
            )
            dt = _parse_iso(bl.attribution.created_at)
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt is not None and (now - dt).days > 730:
                old_line_count += 1
        recency = sum(recency_vals) / len(recency_vals)
    else:
        recency = 0.0
        old_line_count = 0

    doc_type, matched_pattern = doc_type_score(title, config)
    stability, stability_stats = stability_score(
        versions, now, config.half_life_days
    )

    total = (
        recency * config.weights["recency"]
        + doc_type * config.weights["doc_type"]
        + stability * config.weights["stability"]
    )
    total = max(0.0, min(1.0, total))

    flags: list[str] = []
    total_lines = len(blame_lines)
    if total_lines:
        if old_line_count / total_lines >= 0.7:
            flags.append(
                f"{old_line_count*100//total_lines}% av linjene er over 2 år gamle"
            )
    if matched_pattern and doc_type < 0.6:
        flags.append(f"Doc-type indikerer lav modenhet (matchet '{matched_pattern}')")

    stats = {
        "line_count": total_lines,
        "version_count": stability_stats["version_count"],
        "unique_editors": stability_stats["unique_editors"],
        "latest_update": stability_stats["latest_at"],
        "matched_type_pattern": matched_pattern,
    }

    return TrustScore(
        total=total,
        level=_level_for(total),
        components=ScoreComponents(
            recency=recency,
            doc_type=doc_type,
            stability=stability,
        ),
        flags=flags,
        stats=stats,
    )


def score_to_dict(score: TrustScore) -> dict:
    """Serialiser TrustScore til JSON-vennlig dict (uten dato-stempel)."""
    return {
        "total": round(score.total, 4),
        "level": score.level,
        "components": {
            "recency": round(score.components.recency, 4),
            "doc_type": round(score.components.doc_type, 4),
            "stability": round(score.components.stability, 4),
        },
        "flags": list(score.flags),
        "stats": dict(score.stats),
    }


def load_trust_cache(docs_dir: Path) -> dict[str, dict]:
    """Last trust-cache fra disk. Returnerer tom dict hvis ingen cache."""
    path = docs_dir / CACHE_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_trust_cache(docs_dir: Path, cache: dict[str, dict]) -> None:
    path = docs_dir / CACHE_FILENAME
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def cache_is_fresh(cached: dict | None, current_version: int) -> bool:
    """Sjekk om cached entry samsvarer med side-versjon i frontmatter."""
    if not cached:
        return False
    return cached.get("version") == current_version
