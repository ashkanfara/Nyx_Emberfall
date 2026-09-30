"""JSON-file storage for the Reddit workstream.

Layout, relative to the Nyx project root (tests pass a temp root):
    nyx_reddit/data/communities.json   tracked   the dated, verified rules matrix
    nyx_reddit/queue/<draft>.json      tracked   Reddit-native draft queue
    nyx_reddit/packets/<draft>.md      tracked   copy-paste packet, only written after the gate passes
    state/reddit/account.json          private   operator-entered account snapshot
    state/reddit/activity.json         private   warm-up comments, posts, outcomes, halt flag
    generated_assets/reddit/<draft>/   private   Reddit-native images (never public-pipeline assets)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def today() -> dt.date:
    override = os.environ.get("NYX_TODAY")
    return dt.date.fromisoformat(override) if override else dt.date.today()


def parse_date(value):
    return dt.date.fromisoformat(str(value)[:10]) if value else None


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("NYX_PROJECT_ROOT") or PROJECT_ROOT)
        self.data = self.root / "nyx_reddit" / "data"
        self.queue = self.root / "nyx_reddit" / "queue"
        self.packets = self.root / "nyx_reddit" / "packets"
        self.private = self.root / "state" / "reddit"
        self.assets = self.root / "generated_assets" / "reddit"

    @staticmethod
    def _read(path: Path, default):
        if not path.exists():
            return default
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _write(path: Path, obj) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(path)

    def communities(self) -> dict:
        return self._read(self.data / "communities.json", {"communities": {}})

    def save_communities(self, doc: dict) -> None:
        self._write(self.data / "communities.json", doc)

    def account(self) -> dict:
        return self._read(self.private / "account.json", {})

    def save_account(self, doc: dict) -> None:
        self._write(self.private / "account.json", doc)

    def activity(self) -> dict:
        return self._read(self.private / "activity.json", {"comments": [], "posts": [], "halt": None})

    def save_activity(self, doc: dict) -> None:
        self._write(self.private / "activity.json", doc)

    def drafts(self) -> list[dict]:
        if not self.queue.exists():
            return []
        return [self._read(p, {}) for p in sorted(self.queue.glob("*.json"))]

    def draft(self, draft_id: str) -> dict:
        path = self.queue / f"{draft_id}.json"
        if not path.exists():
            raise KeyError(f"no draft {draft_id}")
        return self._read(path, {})

    def save_draft(self, draft: dict) -> None:
        self._write(self.queue / f"{draft['id']}.json", draft)

    def resolve(self, rel) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    def public_asset_hashes(self) -> set[str]:
        """sha256 of every image the public pipeline has produced or handed off
        (generated_assets/ outside the reddit folder, and nyx_metricool_handoff_*).
        A Reddit draft reusing one of these is a blind cross-post."""
        roots = [self.root / "generated_assets"] + sorted(self.root.glob("nyx_metricool_handoff_*"))
        hashes = set()
        for base in roots:
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file() and self.assets not in p.parents:
                    hashes.add(sha256_file(p))
        return hashes
