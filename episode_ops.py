"""CLI for episode_manifest.py -- see that module's docstring for the full
production-contract explanation. This file only parses argv and prints JSON;
all real logic lives in episode_manifest.py.

Commands a Claude-PS5 session may run:
    init            <item_index> <episode_id> <story_thread> <platform> <slide_indices csv> <lock_file>
    prompt-pack     <item_index> <slide_index> <prompt_text_file>
    qa-decide       <item_index> <slide_index> <scores_json_file> [notes]
    status          <item_index>
    next-actions    <item_index>
    mark-ready      <item_index>
    handoff         <item_index> <slide_index> <stage> <reason> <denied_action>

Commands reserved for a human / a distinct generation-or-publish executor --
a Claude-PS5 session must not run these on its own initiative, only when a
human explicitly reports the underlying real-world action already happened:
    record-generated <item_index> <slide_index> <asset_path> [generated_by]
    record-published <item_index> <urls_json_file> [published_by]
    reset-handoff     <item_index> <slide_index> <to_state> <resolution_note>
"""
from __future__ import annotations

import json
import sys

import episode_manifest as em


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]

    if cmd == "init":
        item_index, episode_id, story_thread, platform, slides_csv, lock_file = rest
        slides = [int(x) for x in slides_csv.split(",")]
        out = em.init_manifest(int(item_index), episode_id, story_thread, platform, slides, lock_file)
    elif cmd == "prompt-pack":
        item_index, slide_index, prompt_file = rest
        text = open(prompt_file).read()
        out = em.create_prompt_pack(int(item_index), int(slide_index), text)
    elif cmd == "qa-decide":
        item_index, slide_index, scores_file, *notes = rest
        scores = json.loads(open(scores_file).read())
        out = em.qa_decide(int(item_index), int(slide_index), scores, " ".join(notes))
    elif cmd == "status":
        (item_index,) = rest
        out = em.status(int(item_index))
    elif cmd == "next-actions":
        (item_index,) = rest
        out = em.next_ps5_actions(int(item_index))
    elif cmd == "mark-ready":
        (item_index,) = rest
        out = em.mark_ready_to_publish(int(item_index))
    elif cmd == "handoff":
        item_index, slide_index, stage, reason, denied_action = rest
        out = em.record_handoff(int(item_index), int(slide_index), stage, reason, denied_action)
    elif cmd == "record-generated":
        item_index, slide_index, asset_path, *by = rest
        out = em.record_generated(int(item_index), int(slide_index), asset_path,
                                   by[0] if by else "human_generation_executor")
    elif cmd == "record-published":
        item_index, urls_file, *by = rest
        urls = json.loads(open(urls_file).read())
        out = em.record_published(int(item_index), urls, by[0] if by else "human_publish_executor")
    elif cmd == "reset-handoff":
        item_index, slide_index, to_state, *note = rest
        out = em.reset_from_handoff(int(item_index), int(slide_index), to_state, " ".join(note))
    else:
        print(f"unknown command: {cmd}\n\n{__doc__}")
        return 1

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
