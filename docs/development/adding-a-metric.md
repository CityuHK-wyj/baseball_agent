# Adding a metric

A metric has two halves: a **semantic definition** and an **execution mapping**. Keep them
in the Metric Registry; do not hard-code the physical column in the Planner.

## 1. Define the metric

```python
from app.models.metrics import MetricDefinition, SourceMapping
```

- `MetricDefinition` — `metric_key`, `display_name`, `description`, `required_data_keys`,
  `unit`. This is what the metric *means*.
- `SourceMapping` — `metric_key`, `source_kind` (`POSTGRES`/`PARQUET`/`WEB`/`FEATURE`),
  `location`, and `computation` (`DIRECT` = the source provides it, `CALCULATED` = the
  Feature Engine computes it).

## 2. Register it

```python
from app.semantic.metric_registry import MetricRegistry

registry = MetricRegistry(
    definitions=(MetricDefinition(metric_key="wrc_plus", display_name="wRC+",
                                  description="Weighted runs created plus",
                                  required_data_keys=("woba", "league_woba")),),
    mappings=(SourceMapping(metric_key="wrc_plus", source_kind="FEATURE",
                            location="feature_engine", computation="CALCULATED"),))
```

Expose it to agents by adding a `MetricRegistrySource` to the `ContextService`.

## 3. If it is CALCULATED, add a computation

```python
from app.features.metrics import FeatureComputation, FeatureEngine

computations = {
    "wrc_plus": FeatureComputation("wrc_plus", "index", lambda rows: compute_wrc_plus(rows)),
}
```

The Feature Engine returns a `FEATURE` artifact with `lineage=(input_artifact_id,)`, so a
derived metric is traceable to its inputs.

## 4. Map to physical schema

Add or update a `SchemaTable` in the `SchemaRegistry` describing the table, columns and
grain. The physical mapping is consumed by the Router / Tool layer, never the Planner.

## 5. Test

- A `MetricRegistry` test that the definition and mapping round-trip and that token search
  finds it.
- A `SourceMappingResolver` test for `DIRECT` / `CALCULATED` / `NO_MAPPING`.
- If `CALCULATED`, a `FeatureEngine` test for the value, lineage and provenance.
- If it needs a floor, add a `SampleAdequacyRule`; if it needs eligibility, add a
  `QualificationRule`. Keep them separate — do not add a single `minimum_sample_size`.

## 6. Document

Record the metric and its source in `docs/blog-implementation-matrix.md` if it satisfies a
blog decision, and note any limitation.
