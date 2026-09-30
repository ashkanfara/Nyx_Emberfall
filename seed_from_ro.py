#!/usr/bin/env python3
"""ONE-TIME, READ-ONLY importer: research-orchestrator's completed PS-05
research -> this project's own venture.json.

Reads (never writes):
    ~/Projects/research-orchestrator/runs/20260907T002913Z-b7001f/report.md
    ~/Projects/research-orchestrator/runs/20260907T002913Z-b7001f/run.json

After this one run, the two systems never reference each other again --
PS-05 Venture Manager's state is fully self-contained. The structured fields
below were extracted by hand from the cited report (its own text is copied
alongside for traceability/audit, not re-parsed at runtime).

Historical note (2026-09-22): HYPOTHESIS.concept/why_stylized_not_photoreal
below describe the ORIGINAL research-driven visual-style choice (stylized/
non-photorealistic). Nyx's visual identity has since been founder-locked to
photorealistic master references -- see brand/nyx_identity/identity_spec.json,
which supersedes this text for anything visual. Left verbatim here, unedited,
as the historical research record this file exists to preserve; if this
script is ever re-run, its output must NOT be treated as reinstating the old
visual style over the canonical spec.

Usage: python3 seed_from_ro.py [--force]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import state as st

RO_RUN_DIR = (Path.home() / "Projects" / "research-orchestrator" / "runs"
              / "20260907T002913Z-b7001f")
RO_REPORT = RO_RUN_DIR / "report.md"
RO_RUN_JSON = RO_RUN_DIR / "run.json"

HYPOTHESIS = {
    "verdict": "TEST",
    "concept": ("A disclosed, stylized/anime/fantasy AI virtual creator, initially "
               "tested on Fanvue (the only major platform that explicitly welcomes "
               "fully-synthetic AI creators), with SFW acquisition content "
               "distributed via public channels (TikTok/Instagram/Reddit) funneling "
               "off-platform, monetizing primarily via paid messaging/PPV rather "
               "than subscriptions."),
    "why_not_onlyfans": ("OnlyFans structurally bans fully-synthetic personas and "
                        "autonomous DM automation; Fanvue is the platform with the "
                        "strongest policy fit for this model."),
    "why_stylized_not_photoreal": ("Stylized/non-photorealistic personas clear both "
                                  "Fanvue and Fansly, broadening platform "
                                  "compatibility versus a photorealistic persona."),
    "primary_constraint": ("Distribution/customer acquisition, not content "
                          "generation -- content generation is not believed to be "
                          "the bottleneck."),
    "monetization_note": ("Messaging/PPV may be economically more important than "
                         "subscription revenue (69.74% vs 4.11% of revenue in the "
                         "underlying transaction-study evidence)."),
}

EVIDENCE_GAPS = [
    "No Fanvue-specific CAC, conversion, or churn benchmarks exist anywhere in the evidence gathered.",
    "No side-by-side AI-vs-human unit-economics comparison exists beyond raw image-generation cost.",
    "No documented case of a wholly-fictional AI persona being banned or succeeding long-term on Fanvue/OnlyFans.",
    "Niche-profitability rankings (GFE, MILF, cosplay, feet, LGBTQ) are vendor-sourced only, not independently verified.",
    "OnlyFans' own Terms of Service could not be fetched directly; policy conclusions rest on convergent secondary reporting.",
]

THRESHOLDS = {
    "success": {
        "min_paying_subscribers": 50,
        "min_gross_revenue_usd": 500,
        "window_days": 30,
        "max_cac_usd_organic": 40,
        "max_cac_usd_paid_boost": 15,
        "moderation_strikes_allowed": 0,
    },
    "kill": {
        "max_paying_subscribers": 10,
        "any_account_suspension": True,
        "cac_exceeds_with_no_repeat_purchase": 40,
    },
    "operability": {
        "target_min_minutes_per_week": st.OPERABILITY_TARGET_MIN_MINUTES_PER_WEEK,
        "target_max_minutes_per_week": st.OPERABILITY_TARGET_MAX_MINUTES_PER_WEEK,
        "exempt_categories": ["kyc", "credentials"],
        "note": ("A commercially successful H1 outcome that requires substantial "
                "recurring founder labor is NOT a successful PS-05 outcome."),
    },
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="overwrite existing venture.json")
    args = ap.parse_args(argv)

    if not RO_REPORT.is_file():
        print(f"error: source report not found at {RO_REPORT} -- refusing to seed with "
              f"fabricated data", file=sys.stderr)
        return 1

    if st.VENTURE_PATH.is_file() and not args.force:
        print(f"error: {st.VENTURE_PATH} already exists -- pass --force to overwrite", file=sys.stderr)
        return 1

    v = st.new_venture(
        hypothesis=HYPOTHESIS, thresholds=THRESHOLDS, evidence_gaps=EVIDENCE_GAPS,
        source=f"research-orchestrator run 20260907T002913Z-b7001f (report.md, run.json) -- read-only import",
    )
    st.save(v)

    # Copy the source report alongside for traceability (RO's own files are
    # never modified -- this is a COPY, made once, read-only on the source side).
    archive_dir = st.experiment_dir("seed_source")
    shutil.copy2(RO_REPORT, archive_dir / "ro_ps05_report.md")
    if RO_RUN_JSON.is_file():
        shutil.copy2(RO_RUN_JSON, archive_dir / "ro_ps05_run.json")

    print(f"seeded {st.VENTURE_PATH} from {RO_REPORT}")
    print(f"phase = {v['phase']}, decision = {v['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
