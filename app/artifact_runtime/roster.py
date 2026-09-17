"""Authoritative team roster resolution.

Team population must never be inferred by city-name heuristics: two clubs share a city.
This provider resolves a team to its authoritative MLB identity, then exports a
reusable ``PLAYER_ID_SET`` / ``TEAM_ROSTER`` that local analytics consumes. It is a
normal capability, so a roster can also come from Web evidence, Shared Knowledge, a
transaction artifact or another artifact without any code change.
"""

from __future__ import annotations

import re
from typing import Callable

_STATSAPI = "https://statsapi.mlb.com/api/v1"


class RosterUnavailable(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


class MLBTeamRosterProvider:
    """Live MLB StatsAPI team + active-roster lookup."""

    def __init__(self, transport=None, timeout: float = 12.0) -> None:
        self._timeout = timeout
        self._transport = transport

    def _get(self, url: str, params: dict) -> dict:
        if self._transport is not None:
            return self._transport(url, params)
        import requests
        response = requests.get(url, params=params,
                                headers={"User-Agent": "baseball-agent/0.3",
                                         "Accept": "application/json"}, timeout=self._timeout)
        if response.status_code != 200:
            raise RosterUnavailable(f"statsapi returned {response.status_code}")
        return response.json()

    def _teams(self) -> tuple[dict, ...]:
        payload = self._get(f"{_STATSAPI}/teams", {"sportId": 1})
        return tuple(payload.get("teams", ()))

    @staticmethod
    def _match(team: str, teams: tuple[dict, ...]) -> dict:
        needle = _norm(team)
        if not needle:
            raise RosterUnavailable("no team name was provided")
        matches: list[dict] = []
        for entry in teams:
            full = _norm(f"{entry.get('locationName', '')} {entry.get('teamName', '')}")
            candidates = {
                _norm(entry.get("name", "")), _norm(entry.get("teamName", "")),
                _norm(entry.get("abbreviation", "")), full,
                _norm(entry.get("franchiseName", "")),
            }
            candidates.discard("")
            if needle in candidates or any(c and (c == needle) for c in candidates):
                matches.append(entry)
            elif needle and any(needle == part for part in _norm(entry.get("name", "")).split()
                                if len(part) > 3) and needle not in ("new york", "chicago",
                                                                     "los angeles"):
                # Distinctive single-token match (for example "yankees"), never a shared
                # city name.
                matches.append(entry)
        unique = {entry.get("id"): entry for entry in matches}
        if not unique:
            raise RosterUnavailable(f"no MLB team matches {team!r}")
        if len(unique) > 1:
            names = ", ".join(sorted(str(item.get('name')) for item in unique.values()))
            raise RosterUnavailable(f"team {team!r} is ambiguous: {names}")
        return next(iter(unique.values()))

    def __call__(self, team: str) -> tuple[dict, ...]:
        entry = self._match(team, self._teams())
        team_id = entry.get("id")
        payload = self._get(f"{_STATSAPI}/teams/{team_id}/roster", {"rosterType": "active"})
        roster = []
        for player in payload.get("roster", ()):
            person = player.get("person", {})
            if person.get("id") is None:
                continue
            roster.append({"player_id": str(person["id"]),
                           "name": person.get("fullName", ""),
                           "team_id": str(team_id),
                           "team_name": entry.get("name", ""),
                           "position": (player.get("position") or {}).get("abbreviation", "")})
        if not roster:
            raise RosterUnavailable(f"roster for {team!r} is empty")
        return tuple(roster)


def static_roster_provider(rosters: dict[str, tuple[dict, ...]]
                           ) -> Callable[[str], tuple[dict, ...]]:
    """Deterministic provider for tests and offline composition."""

    normalized = {_norm(team): value for team, value in rosters.items()}

    def provider(team: str) -> tuple[dict, ...]:
        key = _norm(team)
        if key in normalized:
            return normalized[key]
        matches = [value for name, value in normalized.items()
                   if key and (key in name or name in key)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise RosterUnavailable(f"no roster for {team!r}")
        raise RosterUnavailable(f"roster lookup for {team!r} is ambiguous")

    return provider
