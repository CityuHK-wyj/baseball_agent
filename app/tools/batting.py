"""Live batting-statistics capability.

Provides season and date-range batting lines (PA, AVG, OBP, SLG, OPS, HR, BB, SO and
derived BB%/K%) from Baseball Reference through pybaseball. This is what lets the agent
answer "who has been better lately?" with real rate stats instead of only Statcast
quality-of-contact.

Metrics are only those the source actually provides; nothing is invented. Results carry
provenance and a ``source`` label so the response composer can attribute them.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from threading import Lock
from typing import Any

from app.models.agent_runtime import EvidenceItem

# BRef "Tm" values are city names; these are the only cities shared by two clubs.
_AMBIGUOUS_CITIES = {"Los Angeles", "Chicago", "New York"}

METRIC_COLUMNS: tuple[str, ...] = ("PA", "HR", "BB", "SO", "BA", "OBP", "SLG", "OPS")


@dataclass(frozen=True)
class BattingLine:
    name: str
    player_id: str
    team: str
    plate_appearances: int
    home_runs: int
    walks: int
    strikeouts: int
    avg: float | None
    obp: float | None
    slg: float | None
    ops: float | None

    def to_dict(self) -> dict[str, Any]:
        pa = self.plate_appearances or 0
        return {
            "name": self.name, "player_id": self.player_id, "team": self.team,
            "PA": self.plate_appearances, "HR": self.home_runs, "BB": self.walks,
            "SO": self.strikeouts,
            "AVG": _round(self.avg), "OBP": _round(self.obp), "SLG": _round(self.slg),
            "OPS": _round(self.ops),
            "BB%": _round(self.walks / pa, 3) if pa else None,
            "K%": _round(self.strikeouts / pa, 3) if pa else None,
        }


def _round(value: Any, digits: int = 3) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:  # NaN
            return None
        return round(number, digits)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        number = float(value)
        if number != number:
            return 0
        return int(number)
    except (TypeError, ValueError):
        return 0


class BattingStatsClient:
    """Cached pybaseball adapter. Network failures raise ``BattingStatsUnavailable``."""

    def __init__(self, today: date | None = None) -> None:
        self._today = today or date.today()
        self._season_cache: dict[int, tuple[BattingLine, ...]] = {}
        self._range_cache: dict[tuple[str, str], tuple[BattingLine, ...]] = {}
        self._lock = Lock()

    def season(self, year: int) -> tuple[BattingLine, ...]:
        with self._lock:
            if year in self._season_cache:
                return self._season_cache[year]
        try:
            from pybaseball import batting_stats_bref
            frame = batting_stats_bref(year)
        except Exception as error:  # noqa: BLE001 - normalize network/source errors
            raise BattingStatsUnavailable(f"batting stats unavailable for {year}: "
                                          f"{type(error).__name__}") from None
        lines = self._frame_to_lines(frame)
        with self._lock:
            self._season_cache[year] = lines
        return lines

    def date_range(self, start: str, end: str) -> tuple[BattingLine, ...]:
        key = (start, end)
        with self._lock:
            if key in self._range_cache:
                return self._range_cache[key]
        try:
            from pybaseball import batting_stats_range
            frame = batting_stats_range(start, end)
        except Exception as error:  # noqa: BLE001
            raise BattingStatsUnavailable(
                f"batting stats unavailable for {start}..{end}: {type(error).__name__}") from None
        lines = self._frame_to_lines(frame)
        with self._lock:
            self._range_cache[key] = lines
        return lines

    @staticmethod
    def _frame_to_lines(frame) -> tuple[BattingLine, ...]:
        lines: list[BattingLine] = []
        for _, row in frame.iterrows():
            name = str(row.get("Name", "")).strip()
            if not name:
                continue
            lines.append(BattingLine(
                name=name,
                player_id=str(row.get("mlbID", "") or ""),
                team=str(row.get("Tm", "") or ""),
                plate_appearances=_to_int(row.get("PA")),
                home_runs=_to_int(row.get("HR")),
                walks=_to_int(row.get("BB")),
                strikeouts=_to_int(row.get("SO")),
                avg=row.get("BA"), obp=row.get("OBP"), slg=row.get("SLG"),
                ops=row.get("OPS")))
        return tuple(lines)


class BattingStatsUnavailable(RuntimeError):
    pass


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def select_players(lines: tuple[BattingLine, ...], names: tuple[str, ...]
                   ) -> tuple[BattingLine, ...]:
    """Match player names loosely (accent/case/punctuation insensitive)."""
    wanted = [_normalize(name) for name in names if name.strip()]
    selected: list[BattingLine] = []
    for line in lines:
        haystack = _normalize(line.name)
        if any(needle and (needle in haystack or haystack in needle) for needle in wanted):
            selected.append(line)
    return tuple(sorted({(line.player_id or line.name): line for line in selected}.values(),
                        key=lambda item: item.name))


def select_team(lines: tuple[BattingLine, ...], team: str) -> tuple[BattingLine, ...]:
    """Filter by BRef city, derived from the data itself (no hardcoded team map)."""
    needle = _normalize(team)
    if not needle:
        return ()
    cities = {_normalize(line.team) for line in lines if line.team}
    matches = [city for city in cities if city and (city in needle or needle in city)]
    if not matches:
        words = set(needle.split())
        matches = [city for city in cities
                   if any(word and word in city.split() for word in words)]
    if not matches:
        return ()
    city = max(matches, key=len)
    return tuple(line for line in lines if _normalize(line.team) == city)


def batting_evidence(lines: tuple[BattingLine, ...], *, title: str, source_label: str,
                     reference: str = "", metric: str = "OPS", limit: int = 10,
                     caveats: tuple[str, ...] = ()) -> EvidenceItem:
    ranked = sorted([line for line in lines if getattr(line, _attr(metric), None) is not None],
                    key=lambda item: getattr(item, _attr(metric)), reverse=True)[:limit]
    rows = [line.to_dict() for line in ranked]
    summary = title
    if caveats:
        summary = title + " (" + "; ".join(caveats) + ")"
    return EvidenceItem(
        kind="BATTING_STATS", summary=summary, source=source_label, reference=reference,
        text=_render_rows(rows, metric), data={"rows": rows, "metric": metric,
                                               "caveats": list(caveats)},
        retrieved_at=datetime.now(timezone.utc))


def _attr(metric: str) -> str:
    return {"OPS": "ops", "AVG": "avg", "OBP": "obp", "SLG": "slg", "HR": "home_runs",
            "BB": "walks", "SO": "strikeouts", "PA": "plate_appearances"}.get(
        metric.upper(), "ops")


def _render_rows(rows: list[dict[str, Any]], metric: str) -> str:
    header = "Name | Team | PA | HR | AVG | OBP | SLG | OPS"
    body = [f"{r['name']} | {r['team']} | {r['PA']} | {r['HR']} | {r['AVG']} | "
            f"{r['OBP']} | {r['SLG']} | {r['OPS']}" for r in rows]
    return "\n".join([header, *body])


class BattingStatsTool:
    """Planner-facing tool name for routing/trace purposes."""

    name = "batting-stats"

    def __init__(self, client: BattingStatsClient | None = None) -> None:
        self.client = client or BattingStatsClient()

    def season_evidence(self, year: int, *, names: tuple[str, ...] = (),
                        team: str | None = None, metric: str = "OPS",
                        limit: int = 10) -> EvidenceItem:
        lines = self.client.season(year)
        return self._evidence(lines, year=year, names=names, team=team, metric=metric,
                              limit=limit)

    def range_evidence(self, start: str, end: str, *, names: tuple[str, ...] = (),
                       metric: str = "OPS", limit: int = 10) -> EvidenceItem:
        lines = self.client.date_range(start, end)
        return self._evidence(lines, year=None, names=names, team=None, metric=metric,
                              limit=limit, start=start, end=end)

    def _evidence(self, lines: tuple[BattingLine, ...], *, year: int | None,
                  names: tuple[str, ...], team: str | None, metric: str, limit: int,
                  start: str | None = None, end: str | None = None) -> EvidenceItem:
        caveats: list[str] = []
        if names:
            lines = select_players(lines, names)
        if team:
            lines = select_team(lines, team)
            if any(_normalize(city) in _normalize(team) for city in _AMBIGUOUS_CITIES):
                caveats.append("team filtered by city, which is shared by two clubs")
        window = f"{year} season" if year else f"{start}..{end}"
        reference = f"baseball-reference:{year if year else f'{start}..{end}'}"
        title = f"Batting lines ({window}) — ranked by {metric}"
        return batting_evidence(lines, title=title, source_label="baseball-reference",
                                reference=reference, metric=metric, limit=limit,
                                caveats=tuple(caveats))
