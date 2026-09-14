# Quickstart

This gets you from a clean checkout to a first answer. It requires no database and no
credentials — the default pipeline uses an offline synthetic source.

## 1. Python

Requires Python 3.11 or newer (developed on 3.14).

```bash
python3 --version
```

## 2. Virtual environment

```bash
cd baseball_agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

## 3. Dependencies

```bash
pip install -r requirements.txt
```

## 4. Environment variables

The offline path needs none. To use live sources or an LLM, copy the template:

```bash
cp .env.example .env
```

Python does **not** load `.env` automatically. Either export variables in your shell or
load the file yourself (for example `set -a; source .env; set +a`). See
[configuration.md](configuration.md).

## 5. First run

```bash
python3 -m app.cli ask "How did Aaron Judge perform at the plate?"
```

Expected shape of the output:

```
=== COMPLETE ===
Objective objective-...: COMPLETE
- [ACCEPTABLE] synthetic-... from synthetic: ACCEPTABLE (1200 rows): ...
Limitations: Optional data key barrel_rate is absent; ...
```

`COMPLETE` means the core Initial Requirements were satisfied. The line under it is the
accepted evidence that supports the answer.

## 6. Persist and resume

```bash
python3 -m app.cli ask "How did Aaron Judge perform?" --persist
python3 -m app.cli inspect --run-id run-1
python3 -m app.cli resume  --run-id run-1
```

`inspect` lists the checkpoints (`PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`,
`PLANNER_TERMINAL`, `FINALIZATION`) and the stored objects. `resume` shows whether the run
is resumable, which artifacts can be reused, and whether planning is terminal.

## 7. Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/secret_scan.py
```

## Next steps

- [running.md](running.md) — every CLI command.
- [examples.md](examples.md) — realistic questions and what the pipeline does with them.
- [configuration.md](configuration.md) — databases, models, storage.
- [troubleshooting.md](troubleshooting.md) — when something goes wrong.
