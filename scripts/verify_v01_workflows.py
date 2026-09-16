"""Reproduce documented CLI workflows in an isolated temporary runtime directory."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge.loader import seed_store
from app.knowledge.store import SqliteKnowledgeStore
from app.models.knowledge import KnowledgeItem


def main():
    with tempfile.TemporaryDirectory(prefix="baseball-v01-") as directory:
        root = Path(directory)
        env = dict(os.environ, KNOWLEDGE_STORE_PATH=str(root / "knowledge.db"),
                   OPERATIONAL_STORE_PATH=str(root / "operational.db"),
                   ARTIFACT_STORAGE_PATH=str(root / "artifacts"))
        store = SqliteKnowledgeStore(root / "knowledge.db")
        seed_store(store, ROOT / "knowledge/sources", ROOT / "knowledge/seed")
        for suffix in ("A", "B"):
            store.upsert_item(KnowledgeItem(knowledge_id=f"FIXTURE:{suffix}",
                canonical_key=f"LOCAL:fixture-{suffix}", knowledge_type="PLAYER_PROFILE",
                title=f"Fixture Player {suffix}", aliases=("FixturePlayer",),
                summary="Explicit test fixture, not a real MLB player."))
        store.close()

        def cli(*arguments):
            completed = subprocess.run([sys.executable, "-m", "app.cli", *arguments],
                cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=30)
            return completed.stdout

        for query in ("DFA是什么意思？", "道奇属于哪个分区？", "qualified hitter 是什么？"):
            result = json.loads(cli("ask", query, "--persist", "--json"))
            assert result["objective_statuses"] == ["COMPLETE"], result
        waiting = json.loads(cli("ask", "FixturePlayer最近表现怎么样？", "--persist", "--demo", "--json"))
        assert waiting["needs_clarification"], waiting
        request = waiting["clarifications"][0]
        run_id = waiting["run_ids"][0]
        done = json.loads(cli("answer", "--run-id", run_id, "--request-id", request["clarification_id"],
                             "--choice", request["options"][0]["option_id"], "--demo", "--json"))
        assert done["objective_statuses"] == ["COMPLETE"], done
        assert done["run_ids"] == [run_id]
        for command in ("resume", "inspect", "metrics"):
            cli(command, "--run-id", run_id)
        print("PASS: 3 knowledge questions; persisted CLI clarification/answer; resume, inspect, metrics")
        print("Analytics response in clarification scenario is explicitly SYNTHETIC.")


if __name__ == "__main__":
    main()
