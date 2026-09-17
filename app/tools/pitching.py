"""Live pitching-statistics capability (Baseball Reference via pybaseball).

Complements the batting and Statcast tools so open-ended questions about pitchers
("why is he so strong this year?") have real evidence to reason over.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app.models.agent_runtime import EvidenceItem
from app.tools.batting import BattingStatsUnavailable, _normalize


@dataclass(frozen=True)
class PitchingLine:
    name: str
    player_id: str
    team: str
    wins: int
    losses: int
    era: float | None
    whip: float | None
    strikeouts: int
    walks: int
    innings: float | None
    so9: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "player_id": self.player_id, "team": self.team,
                "W": self.wins, "L": self.losses, "ERA": _round(self.era),
                "WHIP": _round(self.whip), "SO": self.strikeouts, "BB": self.walks,
                "IP": self.innings, "SO9": _round(self.so9)}


def _round(value: Any, digits: int = 3) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:
            return None
        return round(number, digits)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        number = float(value)
        return 0 if number != number else int(number)
    except (TypeError, ValueError):
        return 0


class PitchingStatsClient:
    def __init__(self) -> None:
        self._cache: dict[int, tuple[PitchingLine, ...]] = {}
        self._lock = Lock()

    def season(self, year: int) -> tuple[PitchingLine, ...]:
        with self._lock:
            if year in self._cache:
                return self._cache[year]
        try:
            from pybaseball import pitching_stats_bref
            frame = pitching_stats_bref(year)
        except Exception as error:  # noqa: BLE001
            raise BattingStatsUnavailable(
                f"pitching stats unavailable for {year}: {type(error).__name__}") from None
        lines = tuple(PitchingLine(
            name=str(row.get("Name", "")).strip(), player_id=str(row.get("mlbID", "") or ""),
            team=str(row.get("Tm", "") or ""), wins=_to_int(row.get("W")),
            losses=_to_int(row.get("L")), era=row.get("ERA"), whip=row.get("WHIP"),
            strikeouts=_to_int(row.get("SO")), walks=_to_int(row.get("BB")),
            innings=_round(row.get("IP"), 1), so9=_round(row.get("SO9"), 1))
            for _, row in frame.iterrows() if str(row.get("Name", "")).strip())
        with self._lock:
            self._cache[year] = lines
        return lines


class PitchingStatsTool:
    name = "pitching-stats"

    def __init__(self, client: PitchingStatsClient | None = None) -> None:
        self.client = client or PitchingStatsClient()

    def season_evidence(self, year: int, *, names: tuple[str, ...] = (),
                        metric: str = "ERA", limit: int = 10) -> EvidenceItem:
        lines = self.client.season(year)
        caveats: list[str] = []
        if names:
            # Reuse batting name matching against an equivalent view.
            wanted = tuple(_normalize(name) for name in names)
            lines = tuple(line for line in lines
                          if any(w and (w in _normalize(line.name) or _normalize(line.name) in w)
                                 for w in wanted))
        key = {"ERA": "era", "WHIP": "whip", "SO": "strikeouts", "BB": "walks",
               "SO9": "so9", "W": "wins", "IP": "innings"}.get(metric.upper(), "era")
        ranked = sorted([line for line in lines if getattr(line, key, None) is not None],
                        key=lambda item: getattr(item, key),
                        reverse=(metric.upper() not in ("ERA", "WHIP", "BB")))[:limit]
        rows = [line.to_dict() for line in ranked]
        text = "\n".join(["Name | Team | W | L | ERA | WHIP | SO | BB | IP | SO9",
                          *[f"{r['name']} | {r['team']} | {r['W']} | {r['L']} | {r['ERA']} | "
                            f"{r['WHIP']} | {r['SO']} | {r['BB']} | {r['IP']} | {r['SO9']}"
                            for r in rows]])
        return EvidenceItem(
            kind="PITCHING_STATS", summary=f"Pitching lines ({year} season) — {metric}",
            source="baseball-reference", reference=f"baseball-reference:pitching:{year}",
            text=text, data={"rows": rows, "metric": metric, "caveats": caveats,
                             "domain": "pitching"},
            retrieved_at=datetime.now(timezone.utc))
