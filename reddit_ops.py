"""CLI for the Nyx Reddit workstream (nyx_reddit/). Prints JSON, like episode_ops.py.

Reddit channel status stays DEFERRED in channels.py: this workstream prepares
drafts and gates them; it never posts, comments, votes, messages or logs in.
Posting is always a human action outside this code.

Commands a Claude-PS5 session may run:
    variant   <story_lock.json>      build/refresh Reddit-native drafts for one episode
    gate      <draft_id>             evaluate one draft (no side effects besides the stored result)
    gate-all                         evaluate every queued draft
    matrix                           regenerate docs/reddit/*.md (matrix, no-post list, draft queue)
    status                           matrix + queue + halt summary as JSON

Commands reserved for the human who did the real-world step (never run on a
Claude-PS5 session's own initiative):
    record-rule   <community> <field> <values_json> <source_url> <quote> <your_name>
    classify      <community> <status> <your_name> [note]
    account       <age_days> <karma> <profile_discloses:yes|no> <your_name>
    log-comment   <community> <permalink> <summary> <your_name> [removed]
    attach-image  <draft_id> <role> <path> <sfw:yes|no> <identity_qa:pass|fail> <your_name>
    packet        <draft_id>         gate PASS -> writes the copy-paste packet
    record-posted <draft_id> <permalink> <your_name>
    record-outcome <draft_id> <live|removed> <upvote_ratio|-> <substantive_comments|-> <rule_feedback|-> <negative:yes|no> <your_name>
"""
from __future__ import annotations

import json
import sys

from nyx_reddit import gate, ledger, matrix, rules, variant
from nyx_reddit.store import Store


def _yes(v: str) -> bool:
    return v.lower() in ("yes", "y", "true", "1")


def _opt(v: str, cast):
    return None if v in ("-", "") else cast(v)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    store = Store()
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "variant":
            (lock,) = rest
            out = variant.prepare(lock, store)
            matrix.write(store)
        elif cmd == "gate":
            (draft_id,) = rest
            d = store.draft(draft_id)
            out = d["gate"] = gate.evaluate(store, d)
            store.save_draft(d)
        elif cmd == "gate-all":
            out = []
            for d in store.drafts():
                d["gate"] = gate.evaluate(store, d)
                store.save_draft(d)
                out.append({"id": d["id"], "decision": d["gate"]["decision"], "blocks": d["gate"]["blocks"]})
        elif cmd == "matrix":
            out = {"written": matrix.write(store)}
        elif cmd == "status":
            comms = store.communities()["communities"]
            out = {"communities": {k: v.get("status") for k, v in comms.items()},
                   "drafts": [{"id": d["id"], "status": d.get("status"),
                               "gate": (d.get("gate") or {}).get("decision")} for d in store.drafts()],
                   "halt": store.activity().get("halt")}
        elif cmd == "record-rule":
            community, field, values, url, quote, by = rest
            out = rules.record_rule(store, community, field, json.loads(values), url, quote, by)
            matrix.write(store)
        elif cmd == "classify":
            community, status, by, *note = rest
            out = rules.classify(store, community, status, by, " ".join(note))
            matrix.write(store)
        elif cmd == "account":
            age, karma, disclosed, by = rest
            out = ledger.record_account(store, int(age), int(karma), _yes(disclosed), by)
        elif cmd == "log-comment":
            community, url, summary, by, *removed = rest
            out = ledger.log_comment(store, community, url, summary, by, bool(removed and _yes(removed[0])))
        elif cmd == "attach-image":
            draft_id, role, path, sfw, qa, by = rest
            out = ledger.attach_image(store, draft_id, role, path, _yes(sfw), qa, by)["gate"]
            matrix.write(store)
        elif cmd == "packet":
            (draft_id,) = rest
            out = ledger.make_packet(store, draft_id)
            matrix.write(store)
        elif cmd == "record-posted":
            draft_id, url, by = rest
            out = ledger.record_posted(store, draft_id, url, by)
        elif cmd == "record-outcome":
            draft_id, state, ratio, comments, feedback, negative, by = rest
            out = ledger.record_outcome(store, draft_id, state, _opt(ratio, float), _opt(comments, int),
                                        "" if feedback == "-" else feedback, _yes(negative), by)
        else:
            print(__doc__)
            return 1
    except (rules.RuleError, ledger.LedgerError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if isinstance(out, dict) and (out.get("decision") == "BLOCK" or out.get("ok") is False):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
