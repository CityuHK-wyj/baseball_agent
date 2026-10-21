"""CLI entry points.

    python3 -m app.cli chat
    python3 -m app.cli dev --trace-level verbose
    python3 -m app.cli ask "2025年季后赛大谷翔平表现如何"
    python3 -m app.cli doctor

`chat` and `dev` use the same production runtime; `dev` only adds observability.
"""

from __future__ import annotations

import argparse
import sys

from app.runtime.engine import Engine, IngressError


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="strict")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _read_line() -> str | None:
    try:
        raw = sys.stdin.buffer.readline()
    except (KeyboardInterrupt, EOFError):
        return None
    if not raw:
        return None
    try:
        return raw.decode("utf-8", "strict").rstrip("\n").rstrip("\r")
    except UnicodeDecodeError as exc:
        print(f"[输入错误] 非法 UTF-8 字节，已拒绝：{exc}", file=sys.stderr)
        return ""


def _print_turn(result, *, dev: bool, trace_level: str) -> None:
    print()
    print(result.answer)
    if dev:
        print()
        print("─" * 60)
        print(f"goal_status={result.projection.goal_status.value}  needs={len(result.needs)}  artifacts={len(result.artifacts)}  claims={len(result.projection.claims)}")
        if result.rejected_needs:
            print(f"rejected needs: {result.rejected_needs}")
        if result.projection.limitations:
            for limitation in result.projection.limitations:
                print(f"  limitation: {limitation}")
        print("─" * 60)
        print(result.trace.render() if result.trace else "(no trace)")
        print("─" * 60)


def _interactive(engine: Engine, *, dev: bool, trace_level: str, session_id: str) -> int:
    banner = "Baseball Agent (dev)" if dev else "Baseball Agent"
    print(f"{banner} — 输入问题开始（:quit 退出，:clear 清空会话，:doctor 检查数据源）")
    last_result = None
    while True:
        try:
            print("\n> ", end="", flush=True)
        except KeyboardInterrupt:
            print()
            break
        line = _read_line()
        if line is None:
            print()
            break
        text = line.strip()
        if not text:
            continue
        if text in {":quit", ":q", "exit", "quit"}:
            break
        if text == ":clear":
            engine.conversation.clear(session_id)
            print("会话已清空。")
            continue
        if text == ":doctor":
            print(engine.doctor())
            continue
        if text == ":needs":
            if last_result is None:
                print("(no turn yet)")
            else:
                for need in last_result.needs:
                    print(f"  {need.id} [{need.criticality.value}] {need.capability} -> {need.status.value} depends_on={need.depends_on} :: {need.objective[:80]}")
            continue
        if text == ":artifacts":
            if last_result is None:
                print("(no turn yet)")
            else:
                for artifact in last_result.artifacts:
                    scope = artifact.actual_scope.model_dump() if artifact.actual_scope else {}
                    print(f"  {artifact.id} {artifact.kind} need={artifact.need_id} exports={[e.type for e in artifact.exports]} scope={scope}")
            continue
        if text == ":status":
            if last_result is None:
                print("(no turn yet)")
            else:
                print(f"goal_status={last_result.projection.goal_status.value} claims={len(last_result.projection.claims)} limitations={last_result.projection.limitations}")
            continue
        try:
            result = engine.ask(session_id, text, trace_level=trace_level)
        except IngressError as exc:
            print(f"[输入错误] {exc}", file=sys.stderr)
            continue
        except KeyboardInterrupt:
            print("\n(已中断)")
            continue
        except Exception as exc:  # noqa: BLE001 - never crash the chat loop
            print(f"[运行时错误] {type(exc).__name__}: {exc}", file=sys.stderr)
            if dev:
                import traceback

                traceback.print_exc()
            continue
        _print_turn(result, dev=dev, trace_level=trace_level)
        last_result = result
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(prog="app.cli", description="Baseball Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("chat", "dev"):
        sub_parser = sub.add_parser(name, help="interactive chat" if name == "chat" else "interactive chat with trace")
        sub_parser.add_argument("--session", default="cli", help="session id")
        if name == "dev":
            sub_parser.add_argument("--trace-level", default="verbose", choices=["off", "summary", "normal", "verbose"])

    ask_parser = sub.add_parser("ask", help="single question")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--session", default="cli")
    ask_parser.add_argument("--trace-level", default="summary", choices=["off", "summary", "normal", "verbose"])
    ask_parser.add_argument("--dev", action="store_true")

    sub.add_parser("doctor", help="check live sources and configuration")

    knowledge_parser = sub.add_parser("knowledge", help="review CandidateKnowledge (never auto-ACTIVE)")
    knowledge_parser.add_argument("action", choices=["candidates", "approve", "reject"])
    knowledge_parser.add_argument("--id", dest="candidate_id", default="")
    knowledge_parser.add_argument("--reason", default="")

    args = parser.parse_args(argv)

    if args.command == "knowledge":
        from app.config import get_config
        from app.knowledge.store import KnowledgeStore

        config = get_config()
        config.ensure_dirs()
        store = KnowledgeStore(
            candidate_path=config.runtime_dir / "candidate_knowledge.jsonl",
            active_path=config.runtime_dir / "knowledge_active.json",
        )
        if args.action == "candidates":
            candidates = store.list_candidates()
            if not candidates:
                print("(no candidate knowledge)")
            for candidate in candidates:
                proposed = candidate.get("proposed", {})
                print(f"{candidate.get('candidate_id')} [{candidate.get('status')}] {proposed.get('type')} {proposed.get('title')} aliases={proposed.get('aliases')} origin={candidate.get('origin')}")
            return 0
        if not args.candidate_id:
            print("--id is required", file=sys.stderr)
            return 2
        if args.action == "approve":
            ok = store.approve_candidate(args.candidate_id)
        else:
            ok = store.reject_candidate(args.candidate_id, reason=args.reason)
        print("ok" if ok else "candidate not found or already reviewed")
        return 0 if ok else 1

    engine = Engine()

    if args.command == "doctor":
        report = engine.doctor()
        for key, value in report.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "ask":
        try:
            result = engine.ask(args.session, args.question, trace_level=args.trace_level)
        except IngressError as exc:
            print(f"[输入错误] {exc}", file=sys.stderr)
            return 2
        _print_turn(result, dev=args.dev, trace_level=args.trace_level)
        return 0
    if args.command == "dev":
        return _interactive(engine, dev=True, trace_level=args.trace_level, session_id=args.session)
    return _interactive(engine, dev=False, trace_level="summary", session_id=args.session)


if __name__ == "__main__":
    raise SystemExit(main())
