# Analytical population and frozen qualification thresholds

Status: accepted

## Context

The independent v0.1 review (R2/R3) found that the analytics ranking did not preserve the
population a user asked for and did not persist the ranking qualification:

- `launch_speed IS NOT NULL` counted measured fouls as if they were fair batted balls, so
  a contact count was silently presented as a batted-ball leaderboard;
- regular-season, postseason and Spring Training rows coexisted because the loaders
  discarded the upstream `game_type`;
- an explicit "minimum 20 batted balls" disappeared and the adapter substituted its own
  default of 3.

The architecture freezes the flow Natural language → semantic normalization → typed
constraints → requirements → Planner → Router → deterministic mappings → read-only tool →
Artifact → Assessment → Response. Population and qualification are semantic inputs, so
their correction belongs upstream of the adapter; the adapter must not guess or repair
lost intent.

## Decision

- `PopulationConstraint` (kind `POPULATION`) is a typed constraint with
  `game_types` (`REGULAR_SEASON`, `POSTSEASON`, `SPRING_TRAINING`, `EXHIBITION`) and
  `event_population` (`BATTED_BALL`, `MEASURED_CONTACT`, `ALL_PITCHES`). The deterministic
  parser emits it for every analytical query, so the analyzed population is explicit on
  the objective, the requirement descriptor and the artifact. The v0.1 default is regular
  season over balls classified by the provider as in play
  (`description = 'hit_into_play'`). The final re-review corrected the original
  `events IS NOT NULL` implementation: events represents plate-appearance outcomes,
  including strikeouts, walks and truncated foul contacts, and is not a contact filter.
- `QualificationConstraint` (kind `QUALIFICATION`) carries an explicit user threshold.
  The Requirement Decomposer freezes it, together with the documented default
  (`DEFAULT_MIN_BATTED_BALLS = 3`), into `QualificationRule.min_batted_balls`. The adapter
  reads the frozen rule and never substitutes its own default when a value is present.
  Qualification (who may be ranked) stays distinct from `SampleAdequacyRule` (whether the
  sample supports the conclusion) and from `RankingConstraint.aggregation` (the metric).
- Game type is retained at ingestion and backfilled on already-stored data from the
  authoritative MLB StatsAPI `gamePk -> gameType` schedule mapping (every retained row
  already carries `game_pk`). The mapping is additive and non-destructive.
- `ZONE_UPPER_OUTSIDE` replaces the misleading `ZONE_ABOVE_UPPER_EDGE` name for Statcast
  zones 11-12. Those codes are the upper outside quadrants (laterally outside at upper
  height), not a predicate that `plate_z > sz_top`. The numbered-zone approximation, the
  batter-relative `sz_top` predicate and the lateral outside sense are three distinct
  concepts and are named accordingly.

## Consequences

- A user can distinguish regular season from postseason and Spring Training, and fair
  batted balls from measured contact, with the choice visible in the response.
- A source without the required physical fields fails closed (`MISSING_PHYSICAL_FIELDS`)
  rather than silently widening the population.
- The default is owned by the semantic layer (`DEFAULT_MIN_BATTED_BALLS`,
  `DEFAULT_GAME_TYPES`, `DEFAULT_EVENT_POPULATION`), not by the physical adapter.
- Existing installations require the additive `game_type` backfill
  (`python3 -m data_loader.backfill_game_type --parquet --postgres`); without it the
  adapter fails closed instead of returning an implicit population.
- No generalized competition semantics, temporal NLP, or new agents were introduced.

## Alternatives

- Infer game type from the calendar (March = Spring Training): rejected; heuristic, not
  reproducible.
- Keep `launch_speed IS NOT NULL` and relabel it "batted balls": rejected; it silently
  includes fouls.
- Let the adapter default the qualification threshold when a user value is present:
  rejected by R2.
- Rename zones 11-12 to an "above the zone" predicate: rejected; the numbered zones do
  not implement that geometry.

## Re-review correction — 2026-09-17

MEASURED_CONTACT explicitly requires non-null launch_speed independently of the ranking
metric. ALL_PITCHES adds no event filter. Every aggregation still requires a non-null
value of the requested metric; therefore EV rankings and eligibility counts operate on
observed EV values, without imputing zero for missing measurements. BATTED_BALL uses the
provider's hit_into_play classification, retaining sacrifices and fielding outcomes while
excluding terminal strikeouts/walks/HBP and foul contacts. This is an executable event
classification, not an independent reconstruction of fair/foul territory from coordinates.
Frozen QualificationRule takes precedence over every adapter fallback setting. Incomplete
Parquet game-type mappings now fail before any archive replacement. Parser composition
still has release blockers; see the semantic re-review report.
