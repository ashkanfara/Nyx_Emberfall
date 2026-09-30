#!/usr/bin/env python3
"""Stable CLI to append one measurement to a named experiment and save state.

Fixed-path counterpart to ps05_ops.py for the one write action scheduled
ticks need repeatedly (recording an experiment measurement) so it can be
permission-matched across runs instead of living in a scratchpad script.

    python3 record_measurement.py <experiment_id> '<json measurement dict>'
"""

from __future__ import annotations

import json
import sys

import state as st


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: record_measurement.py <experiment_id> '<json>'"}))
        return 1
    experiment_id, measurement_json = argv
    measurement = json.loads(measurement_json)
    st.update(lambda v: st.record_experiment_measurement(v, experiment_id, measurement))
    print(json.dumps({"ok": True, "experiment_id": experiment_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
