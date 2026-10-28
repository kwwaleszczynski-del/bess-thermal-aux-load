"""End-to-end simulation, verification, figure generation, and static dashboard data export."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bess_thermal.config import COOLING_SYSTEMS, DUTY_SPECS, ContainerSpec, PcsTransformerSpec
from bess_thermal.physics import chiller_cop, evaluate_steady_aux_vs_temperature
from bess_thermal.plots import generate_all_figures
from bess_thermal.simulation import simulate_container_year
from bess_thermal.verification import run_verification_suite
from bess_thermal.weather import generate_polish_weather_8760

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_full_study(output_root: Path | None = None) -> dict:
    """Execute all 6 coupled electro-thermal container runs, verification suite, figures, and docs/data.js."""
    root = output_root or REPO_ROOT
    results_dir = root / "results"
    docs_dir = root / "docs"
    img_dir = docs_dir / "img"
    results_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    container = ContainerSpec()
    pcs_spec = PcsTransformerSpec()
    weather_df = generate_polish_weather_8760()

    sim_runs: dict[tuple[str, str], dict] = {}
    summary_rows: list[dict] = []
    monthly_all_rows: list[dict] = []

    for duty_code in DUTY_SPECS:
        for cool_code in COOLING_SYSTEMS:
            res = simulate_container_year(
                weather_df=weather_df,
                duty_code=duty_code,
                cooling_code=cool_code,
                container=container,
                pcs_spec=pcs_spec,
            )
            sim_runs[(duty_code, cool_code)] = res
            summary_rows.append(res["summary"])
            m_df = res["monthly_df"].copy()
            m_df.insert(0, "cooling_code", cool_code)
            m_df.insert(0, "duty_code", duty_code)
            monthly_all_rows.extend(m_df.to_dict(orient="records"))

    df_summary = pd.DataFrame(summary_rows)
    df_monthly = pd.DataFrame(monthly_all_rows)
    df_summary.to_csv(results_dir / "annual_summary.csv", index=False)
    df_monthly.to_csv(results_dir / "monthly_rte_losses.csv", index=False)

    t_grid = np.linspace(-15.0, 35.0, 101)
    aux_temp_curves = evaluate_steady_aux_vs_temperature(t_grid, container=container)
    cop_curves = {
        "t_amb_c": [round(float(t), 2) for t in t_grid],
        "liquid": [round(float(chiller_cop(t, COOLING_SYSTEMS["liquid"])), 3) for t in t_grid],
        "air": [round(float(chiller_cop(t, COOLING_SYSTEMS["air"])), 3) for t in t_grid],
        "ptc_heater": [0.98 for _ in t_grid],
    }

    verification = run_verification_suite(sim_runs, weather_df, container=container, pcs_spec=pcs_spec)
    (results_dir / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")

    generate_all_figures(sim_runs, aux_temp_curves, img_dir=img_dir)

    # Build compact data payload for interactive Plotly dashboard (docs/data.js)
    runs_payload: dict[str, dict] = {}
    for (duty_code, cool_code), res in sim_runs.items():
        key = f"{duty_code}__{cool_code}"
        runs_payload[key] = {
            "summary": res["summary"],
            "monthly": res["monthly_df"].to_dict(orient="list"),
            "jan_15m": res["jan_15m"],
            "jul_15m": res["jul_15m"],
        }

    # Liquid vs Air comparison headline metrics at 1.0 cycle/day
    s_liq_100 = sim_runs[("da_100", "liquid")]["summary"]
    s_air_100 = sim_runs[("da_100", "air")]["summary"]
    aux_saved_mwh = round(s_air_100["e_aux_mwh"] - s_liq_100["e_aux_mwh"], 2)
    aux_saved_pct = round(100.0 * aux_saved_mwh / s_air_100["e_aux_mwh"], 1)

    dashboard_payload = {
        "site": {
            "name": container.name,
            "location": "Central Poland (52.2 N, 21.0 E)",
            "rated_power_mw": container.rated_power_kw / 1000.0,
            "rated_energy_mwh": container.rated_energy_kwh / 1000.0,
            "c_cell_mj_per_k": container.c_cell_mj_per_k,
            "c_enc_mj_per_k": container.c_enc_mj_per_k,
            "bms_base_load_kw": container.bms_base_load_kw,
            "trafo_fe_kw": pcs_spec.trafo_p_fe_kw,
            "trafo_cu_rated_kw": pcs_spec.trafo_p_cu_rated_kw,
        },
        "liquid_vs_air_da100": {
            "aux_liquid_mwh": s_liq_100["e_aux_mwh"],
            "aux_air_mwh": s_air_100["e_aux_mwh"],
            "aux_saved_mwh": aux_saved_mwh,
            "aux_saved_pct": aux_saved_pct,
            "spread_liquid_k": s_liq_100["max_temp_spread_k"],
            "spread_air_k": s_air_100["max_temp_spread_k"],
        },
        "runs": runs_payload,
        "aux_vs_temp": aux_temp_curves,
        "cop_vs_temp": cop_curves,
        "verification": verification,
    }

    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    (docs_dir / "data.js").write_text(
        "window.DATA = " + json.dumps(dashboard_payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (results_dir / "summary.json").write_text(
        json.dumps(
            {
                "site": dashboard_payload["site"],
                "liquid_vs_air_da100": dashboard_payload["liquid_vs_air_da100"],
                "summaries": summary_rows,
                "verification": verification,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dashboard_payload
