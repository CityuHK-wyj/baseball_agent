import json

from app.knowledge.store import KnowledgeStore


def _seed(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "glossary.json").write_text(
        json.dumps({"items": [{"id": "G:1", "type": "DEFINITION", "title": "Batting Average", "aliases": ["打击率"], "summary": "H/AB"}]}),
        encoding="utf-8",
    )
    return seed_dir


def _store(tmp_path):
    return KnowledgeStore(
        seed_dir=_seed(tmp_path),
        candidate_path=tmp_path / "candidates.jsonl",
        active_path=tmp_path / "active.json",
    )


def test_seed_is_active():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        store = _store(Path(directory))
        assert store.match_alias("打击率") is not None


def test_candidate_is_not_active_until_approved(tmp_path):
    store = _store(tmp_path)
    candidate_id = store.record_candidate(
        {
            "id": "PLAYER:999",
            "type": "ENTITY_ALIAS",
            "title": "Test Player",
            "aliases": ["测试球员"],
            "summary": "runtime discovery",
            "payload": {"player_id": 999},
        },
        origin="entity_resolution",
        evidence=["q"],
    )
    assert candidate_id
    # Not retrievable as authoritative knowledge yet.
    assert store.match_alias("测试球员") is None
    assert store.list_candidates()[0]["status"] == "CANDIDATE"

    assert store.approve_candidate(candidate_id)
    assert store.match_alias("测试球员") is not None
    assert store.get_candidate(candidate_id)["status"] == "APPROVED"


def test_rejected_candidate_never_activates(tmp_path):
    store = _store(tmp_path)
    candidate_id = store.record_candidate(
        {"id": "PLAYER:1", "type": "ENTITY_ALIAS", "title": "Bad", "aliases": ["坏"]},
        origin="web_research",
        evidence=["x"],
    )
    assert store.reject_candidate(candidate_id, reason="not authoritative")
    assert store.match_alias("坏") is None
    assert store.get_candidate(candidate_id)["status"] == "REJECTED"
