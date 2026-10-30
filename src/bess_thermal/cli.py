"""Command-line interface for bess-thermal-aux-load.

Usage:
    python -m bess_thermal run       # full study: results/, docs/img/*.png, docs/data.js, README tables
    python -m bess_thermal verify    # same run, prints the verification JSON
    python -m bess_thermal fetch     # re-download the ERA5 weather year into data/ (network, ~5 Open-Meteo units)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bess_thermal.pipeline import REPO_ROOT, run_full_study
from bess_thermal.weather import DATA_FILE, fetch_era5_weather


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bess-thermal",
        description="Coupled electro-thermal and auxiliary-power simulation for a 5 MWh LFP container in Poland.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the study, checks, sensitivity, plots, dashboard data and README tables")
    p_run.add_argument("--out", type=Path, default=REPO_ROOT, help="Output root directory")

    p_ver = sub.add_parser("verify", help="Run the study and print the verification results")
    p_ver.add_argument("--out", type=Path, default=REPO_ROOT, help="Output root directory")

    p_fetch = sub.add_parser("fetch", help="Download the ERA5 weather year from the Open-Meteo archive")
    p_fetch.add_argument("--path", type=Path, default=DATA_FILE, help="CSV file to write")

    args = parser.parse_args(argv)
    if args.command == "fetch":
        print(f"Wrote {fetch_era5_weather(args.path)}")
        return 0

    payload = run_full_study(output_root=args.out)
    if args.command == "verify":
        print(json.dumps(payload["verification"], indent=2))
    else:
        s_da = payload["runs"]["da_100__liquid"]["summary"]
        s_res = payload["runs"]["reserve_035__liquid"]["summary"]
        print(
            f"Net PCC RTE, liquid: 1.0 cyc/d {s_da['rte_pcc_net'] * 100:.1f} % "
            f"(months {s_da['rte_pcc_month_min'] * 100:.1f}-{s_da['rte_pcc_month_max'] * 100:.1f} %), "
            f"0.35 cyc/d {s_res['rte_pcc_net'] * 100:.1f} %."
        )
    return 0
