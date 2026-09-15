"""Live knowledge refresh.

Fetch -> normalize -> validate -> stage -> activate, reusing the ingestion pipeline so a
refresh can never bypass validation. Reference data (teams, ballparks, divisions) is
fetched from the official MLB Stats API; the rulebook edition is verified from the
official PDF. Domains with no stable structured endpoint (glossary, players, community)
are reloaded from committed seed packs and reported as such.
"""

import re
from datetime import date, datetime, timezone

from app.knowledge.fetch import FetchError, fetch, fetch_json
from app.knowledge.ingestion import KnowledgePack
from app.models.knowledge import KnowledgeDiff, KnowledgeItem

STATS_API = "https://statsapi.mlb.com/api/v1"
RULEBOOK_URL = "https://mktg.mlbstatic.com/mlb/official-information/{year}-official-baseball-rules.pdf"
DIVISION_BY_NAME = {
    "American League East": "ALE", "American League Central": "ALC", "American League West": "ALW",
    "National League East": "NLE", "National League Central": "NLC", "National League West": "NLW",
}
_DIV_NAME_BY_ABBR = {v: k for k, v in DIVISION_BY_NAME.items()}


def _now_verified() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: date | datetime | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _existing_by_key(store) -> dict:
    return {item.canonical_key: item for item in store.list_items()}


def _live_teams() -> tuple[dict, ...]:
    payload = fetch_json(f"{STATS_API}/teams?sportId=1")
    return tuple(payload.get("teams", ()))


def build_reference_pack(store, *, teams: tuple[dict, ...] | None = None,
                         as_of: date | None = None) -> KnowledgePack:
    teams = teams if teams is not None else _live_teams()
    if len(teams) != 30:
        raise FetchError(f"expected 30 MLB teams, got {len(teams)}")
    today = as_of or date.today()
    verified = _now_verified()
    existing = _existing_by_key(store)
    items: list[KnowledgeItem] = []

    for team in teams:
        key = team["abbreviation"]
        prior = existing.get(key)
        prior_payload = dict(prior.structured_payload) if prior else {}
        venue = team["venue"]["name"]
        league = team["league"]["name"]
        division = team["division"]["name"]
        candidates = [team.get("teamName", ""), team.get("shortName", ""),
                      f"{team.get('locationName', '')} {team.get('teamName', '')}",
                      prior_payload.get("zh_name", ""), prior_payload.get("zh_nickname", "")]
        aliases = [alias for alias in dict.fromkeys(candidates)
                   if alias and alias != team["name"]]
        payload = dict(prior_payload)
        payload.update({
            "official_name": team["name"], "abbreviation": key, "league": league,
            "division": division, "city": team.get("locationName", ""), "venue": venue,
            "venue_id": team["venue"]["id"], "team_id_mlbam": team["id"],
            "first_year": int(team.get("firstYearOfPlay", prior_payload.get("first_year", 0) or 0)),
            "official_site": f"https://www.mlb.com/{team.get('teamCode', '')}",
            "refreshed_at": today.isoformat(),
        })
        items.append(KnowledgeItem(
            knowledge_id=f"TEAM:{key}", knowledge_type="TEAM", canonical_key=key,
            title=team["name"], aliases=tuple(aliases), summary=(
                f"{team['name']} is a {league} club in the {division}, playing home games at {venue}."),
            structured_payload=payload, entity_refs=(f"TEAM:{key}",),
            tags=("mlb", "team", "division:" + division.lower().replace(" ", "_")),
            source_refs=("mlb_statsapi", "mlb_official"), source_authority="OFFICIAL",
            as_of=today, last_verified_at=verified, freshness_policy="ANNUAL",
            verification_status="VERIFIED", status="ACTIVE"))

        park_prior = existing.get(venue)
        park_payload = dict(park_prior.structured_payload) if park_prior else {}
        park_payload.update({"official_name": venue, "team": f"TEAM:{key}",
                             "city": team.get("locationName", ""), "venue_id": team["venue"]["id"],
                             "refreshed_at": today.isoformat()})
        park_payload.setdefault("common_name", venue)
        items.append(KnowledgeItem(
            knowledge_id=f"PARK:{key}", knowledge_type="BALLPARK",
            canonical_key=park_payload["common_name"], title=venue,
            aliases=tuple(a for a in (park_payload["common_name"],) if a != venue),
            summary=f"{venue} is the current home ballpark of the {team['name']}.",
            structured_payload=park_payload, entity_refs=(f"PARK:{key}", f"TEAM:{key}"),
            tags=("mlb", "ballpark"), source_refs=("mlb_statsapi", "baseball_reference"),
            source_authority="OFFICIAL", as_of=today, last_verified_at=verified,
            freshness_policy="PERIODIC", verification_status="VERIFIED", status="ACTIVE"))

    members: dict[str, list[str]] = {}
    for team in teams:
        members.setdefault(team["division"]["name"], []).append(team["abbreviation"])
    for division_name, abbr in DIVISION_BY_NAME.items():
        items.append(KnowledgeItem(
            knowledge_id=f"DIV:{abbr}", knowledge_type="DIVISION", canonical_key=division_name,
            title=division_name, aliases=(abbr,), language="en",
            summary=f"{division_name} is a division of the "
                    f"{'American' if abbr.startswith('AL') else 'National'} League and contains "
                    f"{len(members.get(division_name, []))} clubs.",
            structured_payload={"abbreviation": abbr,
                                "league": f"LEAGUE:{'AL' if abbr.startswith('AL') else 'NL'}",
                                "teams": sorted(members.get(division_name, []))},
            entity_refs=(f"DIV:{abbr}",), tags=("mlb", "division"),
            source_refs=("mlb_statsapi", "mlb_official"), source_authority="OFFICIAL",
            as_of=today, last_verified_at=verified, freshness_policy="ANNUAL",
            verification_status="VERIFIED", status="ACTIVE"))
    for abbr, name in (("AL", "American League"), ("NL", "National League")):
        divs = [f"DIV:{key}" for key, value in DIVISION_BY_NAME.items() if key.startswith(abbr)]
        items.append(KnowledgeItem(
            knowledge_id=f"LEAGUE:{abbr}", knowledge_type="LEAGUE", canonical_key=name,
            title=name, aliases=(abbr,), summary=f"{name} is one of the two leagues of MLB.",
            structured_payload={"abbreviation": abbr, "divisions": divs},
            entity_refs=(f"LEAGUE:{abbr}",), tags=("mlb", "league"),
            source_refs=("mlb_statsapi", "mlb_official"), source_authority="OFFICIAL",
            as_of=today, last_verified_at=verified, freshness_policy="ANNUAL",
            verification_status="VERIFIED", status="ACTIVE"))

    # Must be part of the reference pack, otherwise a refresh would supersede it.
    items.append(KnowledgeItem(
        knowledge_id="LEAGUE_STRUCTURE:MLB", knowledge_type="LEAGUE_STRUCTURE",
        canonical_key="mlb_league_structure",
        title="Major League Baseball structure (30 teams, 2 leagues, 6 divisions)",
        aliases=("MLB structure", "league and division structure"), language="en",
        summary="MLB has 30 active franchises: 15 in the American League and 15 in the National "
                "League, each split into East, Central and West divisions of five clubs.",
        structured_payload={
            "franchise_count": 30, "league_count": 2, "division_count": 6,
            "leagues": {"American League": ["ALE", "ALC", "ALW"],
                        "National League": ["NLE", "NLC", "NLW"]},
            "teams": sorted(team["abbreviation"] for team in teams),
            "refreshed_at": today.isoformat()},
        entity_refs=("LEAGUE:AL", "LEAGUE:NL"), tags=("mlb", "league_structure"),
        source_refs=("mlb_statsapi", "mlb_official"), source_authority="OFFICIAL",
        as_of=today, last_verified_at=verified, freshness_policy="ANNUAL",
        verification_status="VERIFIED", status="ACTIVE"))

    return KnowledgePack(domain="reference", items=tuple(items),
                         expected_team_keys=tuple(team["abbreviation"] for team in teams))


def refresh_reference(store, *, teams: tuple[dict, ...] | None = None,
                      as_of: date | None = None) -> KnowledgeDiff:
    pack = build_reference_pack(store, teams=teams, as_of=as_of)
    return store_ingest(store, pack)


_RULE_HEADING = re.compile(rb"(?m)^\s*(\d\.\d{2})\s*[\xe2\x80\x94\xe2\x80\x93-]?\s*([A-Z][A-Za-z' ,()\-]{3,60})")


def refresh_rules(store, *, years: tuple[int, ...] | None = None) -> KnowledgeDiff:
    """Verify the current official rulebook edition and rule numbering."""
    if years is None:
        current = date.today().year
        years = (current, current - 1, current - 2)
    last_error: Exception | None = None
    for year in years:
        url = RULEBOOK_URL.format(year=year)
        try:
            data = fetch(url, retries=2)
        except FetchError as error:  # noqa: BLE001 - try the previous edition
            last_error = error
            continue
        if not data.startswith(b"%PDF"):
            last_error = FetchError("response was not a PDF")
            continue
        try:
            from pypdf import PdfReader  # imported lazily; optional at runtime
            import io

            reader = PdfReader(io.BytesIO(data))
            text = b"\n".join((page.extract_text() or "").encode("utf-8", "ignore")
                              for page in reader.pages)
            numbers = sorted({match.group(1).decode() for match in _RULE_HEADING.finditer(text)})
        except Exception as error:  # noqa: BLE001 - extraction failure must not corrupt the store
            last_error = error
            continue
        if "5.09" not in numbers or "9.19" not in numbers:
            last_error = FetchError("rulebook structure did not match the expected sections")
            continue
        existing = store.get_item("RULEBOOK:OBR_2026")
        payload = dict(existing.structured_payload) if existing else {}
        payload.update({"edition": year, "page_count": len(reader.pages),
                        "rule_count": len(numbers), "official_url": url,
                        "last_structure_check": date.today().isoformat(),
                        "sampled_sections": numbers[:5] + numbers[-5:]})
        item = KnowledgeItem(
            knowledge_id="RULEBOOK:OBR_2026", knowledge_type="RULE",
            canonical_key="official_baseball_rules_2026",
            title=f"Official Baseball Rules, {year} Edition",
            aliases=(f"OBR {year}", "MLB rulebook"), language="en",
            summary="The current code of playing rules governing MLB and the professional "
                    "development leagues. Structure verified live from the official PDF.",
            structured_payload=payload, entity_refs=("RULEBOOK:OBR_2026",),
            tags=("obr", "rulebook", "domain:rules"), source_refs=("mlb_official_rules",),
            source_authority="OFFICIAL", as_of=date.today(), last_verified_at=_now_verified(),
            freshness_policy="OFFICIAL_RULES", verification_status="VERIFIED", status="ACTIVE")
        diff = store_ingest(store, KnowledgePack(domain="rules_refresh", items=(item,),
                                                 sources=(), expected_team_keys=()))
        return diff.model_copy(update={"message": f"verified {year} rulebook, {len(numbers)} rule numbers"})
    raise FetchError(f"could not verify any official rulebook edition: {last_error}")


def _load_seed_pack(seed_dir, name: str) -> KnowledgePack:
    from pathlib import Path

    from app.knowledge.ingestion import load_pack

    return load_pack(Path(seed_dir) / f"{name}.json")


def store_ingest(store, pack: KnowledgePack) -> KnowledgeDiff:
    from app.knowledge.service import KnowledgeBase

    return KnowledgeBase(store).ingest(pack)


def refresh_domain(store, domain: str, *, seed_dir, source_dir) -> KnowledgeDiff:
    """Dispatch a refresh. Live where a structured source exists; seed reload otherwise."""
    domain = domain.lower()
    if domain in ("reference", "teams", "ballparks"):
        return refresh_reference(store)
    if domain == "rules":
        return refresh_rules(store)
    if domain in ("glossary", "players", "community", "context"):
        pack = _load_seed_pack(seed_dir, domain)
        diff = store_ingest(store, pack)
        return diff.model_copy(update={
            "message": f"reloaded committed seed pack for {domain!r}; no live API for this domain"})
    if domain == "all":
        from app.knowledge.loader import load_packs

        diffs = []
        for pack in load_packs(seed_dir):
            diffs.append(store_ingest(store, pack))
        return KnowledgeDiff(domain="all", added=tuple(x for d in diffs for x in d.added),
                             updated=tuple(x for d in diffs for x in d.updated),
                             unchanged=tuple(x for d in diffs for x in d.unchanged),
                             message="reloaded all committed seed packs")
    raise ValueError(f"unknown knowledge domain {domain!r}")
