"""Command-line interface for bess-thermal-aux-load."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bess_thermal.pipeline import REPO_ROOT, run_full_study


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bess-thermal",
        description="Coupled electro-thermal and auxiliary-power simulation for a 5 MWh LFP container in Poland.",
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run full 8,760-hour study, verification, plots, and dashboard export")
    p_run.add_argument("--out", type=Path, default=REPO_ROOT, help="Output root directory")

    p_ver = sub.add_parser("verify", help="Run first-law conservation and auxiliary parameter recovery checks")
    p_ver.add_argument("--out", type=Path, default=REPO_ROOT, help="Output root directory")

    p_site = sub.add_parser("site", help="Regenerate docs/img/*.png and docs/data.js")
    p_site.add_argument("--out", type=Path, default=REPO_ROOT, help="Output root directory")

    args = parser.parse_args(argv)
    out_root = getattr(args, "out", REPO_ROOT)
    payload = run_full_study(output_root=out_root)

    if args.command == "verify":
        print(json.dumps(payload["verification"], indent=2))
    else:
        s_da = payload["runs"]["da_100__liquid"]["summary"]
        s_res = payload["runs"]["reserve_035__liquid"]["summary"]
        print(
            f"Completed 8,760-hour simulation: DA 1.0 cyc/d Net PCC RTE = {s_da['rte_pcc_net']*100:.1f}% "
            f"(Jan {s_da['rte_jan_pcc']*100:.1f}%, Jul {s_da['rte_jul_pcc']*100:.1f}%, May {s_da['rte_may_pcc']*100:.1f}%), "
            f"Reserve 0.35 cyc/d Net PCC RTE = {s_res['rte_pcc_net']*100:.1f}%."
        )
    return 0
