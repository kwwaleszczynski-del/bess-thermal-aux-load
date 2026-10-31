"""End-to-end simulation, verification, sensitivity, figures, dashboard data and README tables."""

from __future__ import annotations

import dataclasses
import json
import re
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

from bess_thermal.config import (
    COOLING_SYSTEMS,
    DUTY_SPECS,
    ChillerSpec,
    ContainerSpec,
    PcsTransformerSpec,
)
from bess_thermal.plots import generate_all_figures
from bess_thermal.sensitivity import run_sensitivity
from bess_thermal.simulation import simulate_container_year, steady_aux_vs_temperature
from bess_thermal.verification import run_verification_suite
from bess_thermal.weather import DATA_FILE, LATITUDE, LONGITUDE, load_weather

REPO_ROOT = Path(__file__).resolve().parents[2]

C, P, H = ContainerSpec(), PcsTransformerSpec(), ChillerSpec()
LQ, AR = COOLING_SYSTEMS["liquid"], COOLING_SYSTEMS["air"]

# (input, value, basis) rows for the README parameter table; values are read from the specs
PARAMETER_ROWS: list[tuple[str, str, str]] = [
    ("Rated power / energy", f"{C.rated_power_kw / 1000:g} MW / {C.rated_energy_kwh / 1000:g} MWh", "generic 20-ft LFP container"),
    ("Pack layout", f"{C.n_series}s x {C.n_parallel}p, 314 Ah LFP cells", "assumption (common 1,331 V nominal string)"),
    ("Effective cell DC resistance at 25 degC", f"{C.cell_r25_mohm:g} mOhm", "assumption: ohmic + polarisation; implied DC RTE in the checks table"),
    ("Resistance vs temperature", "doubles from 25 to 5 degC", f"Arrhenius, E_a/R = {C.ea_over_r_k:.0f} K (assumption)"),
    ("Cell voltage hysteresis (half gap)", f"{C.cell_hysteresis_mv:g} mV", "assumption, LFP charge/discharge OCV gap; to be verified per cell"),
    ("Entropic coefficient", "order 0.1 mV/K, sign changes with SoC", "assumed shape, order of published LFP values; to be verified"),
    ("Cell thermal mass", f"{C.c_cell_mj_per_k:.1f} MJ/K", f"{C.cell_specific_energy_wh_per_kg:g} Wh/kg, c_p {C.cell_cp_j_per_kg_k:g} J/(kg K) (assumption)"),
    ("Enclosure node thermal mass", f"{C.c_enc_mj_per_k:g} MJ/K", "racks, coolant or air, steel shell (assumption)"),
    ("Envelope UA", f"{C.ua_envelope_kw_per_k * 1000:.0f} W/K", f"{C.envelope_area_m2:.1f} m2 (20-ft HC) x U {C.u_envelope_w_per_m2_k:g} W/(m2 K) (assumption)"),
    ("Roof solar gain", "sol-air on the roof only", f"absorptance {C.solar_absorptance:g}, h_out {C.h_out_w_per_m2_k:g} W/(m2 K) (assumption)"),
    ("Cell temperature band", f"{C.t_heat_set_c:g} to {C.t_cool_set_c:g} degC", "heat below, cool above (assumption)"),
    ("Heater", f"{C.heater_capacity_kw:g} kW, efficiency {C.heater_efficiency:g}", "resistive, inside the envelope"),
    ("BMS / controls base load", f"{C.bms_base_load_kw:g} kW, 24/7", "assumption; dissipated inside the container"),
    ("Cell-to-coolant / cell-to-air UA", f"{LQ.ua_int_kw_per_k:g} / {AR.ua_int_kw_per_k:g} kW/K", "assumption (~2.4 / ~0.4 W/K per cell)"),
    ("Evaporator approach (liquid / air)", f"{LQ.evap_approach_k:g} / {AR.evap_approach_k:g} K", "assumption"),
    ("Minimum supply temperature (liquid / air)", f"{LQ.t_supply_min_c:g} / {AR.t_supply_min_c:g} degC", "assumption"),
    ("Cooling capacity (liquid / air)", f"{LQ.cooling_capacity_kw:g} / {AR.cooling_capacity_kw:g} kW", "assumption"),
    ("Pump / fan power while running (liquid / air)", f"{LQ.circulation_kw:g} / {AR.circulation_kw:g} kW", "assumption; runs while cycling or heating/cooling"),
    ("Chiller COP model", f"{H.carnot_fraction:g} x Carnot, condensing at T_amb + {H.cond_approach_k:g} K, max {H.cop_max:g}", "assumption; no free-cooling economiser"),
    ("PCS loss", f"{P.pcs_p0_kw:g} kW + {P.pcs_k1:g} |P| + {P.pcs_k2_per_kw:g} P^2 (kW)", "assumption, typical central-inverter curve; sleep when idle"),
    ("MV transformer 2.5 MVA losses", f"no-load {P.trafo_p0_kw:g} kW, load {P.trafo_pk_kw:g} kW", "EU Ecodesign Tier 2 maximum (Reg. (EU) 548/2014, Annex I, Table I.1); to be verified"),
]


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f} %"


def build_readme_tables(summaries: dict[tuple[str, str], dict], verification: dict) -> str:
    """Markdown tables for the README, generated from the run summaries."""
    lines = [
        "| Duty | Cooling | DC | PCS AC | MV, excl. aux | LV net | **PCC net** | PCC net, lowest / highest month |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (d, c), s in summaries.items():
        lines.append(
            f"| {DUTY_SPECS[d].short_label} | {COOLING_SYSTEMS[c].short_label} | {_pct(s['rte_dc'])} | "
            f"{_pct(s['rte_pcs_ac'])} | {_pct(s['rte_trafo_mv'])} | {_pct(s['rte_lv_net'])} | "
            f"**{_pct(s['rte_pcc_net'])}** | {_pct(s['rte_pcc_month_min'])} ({s['rte_pcc_month_min_name']}) / "
            f"{_pct(s['rte_pcc_month_max'])} ({s['rte_pcc_month_max_name']}) |"
        )
    lines += [
        "",
        "| Duty | Cooling | PCC import [MWh/yr] | Aux total [MWh/yr] | Base load | Pump / fans | Chiller | Heater | Chiller COP (annual) | Cell temp. min-max [degC] |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (d, c), s in summaries.items():
        cop = "-" if s["cop_mean"] is None else f"{s['cop_mean']:.2f}"
        lines.append(
            f"| {DUTY_SPECS[d].short_label} | {COOLING_SYSTEMS[c].short_label} | {s['e_in_pcc_mwh']:.0f} | "
            f"{s['e_aux_mwh']:.1f} | {s['e_bms_mwh']:.1f} | {s['e_circ_mwh']:.1f} | {s['e_chiller_mwh']:.1f} | "
            f"{s['e_heater_mwh']:.2f} | {cop} | {s['t_cell_min_c']:.1f} - {s['t_cell_max_c']:.1f} |"
        )
    v = verification
    integ = v["integrator_vs_exact"]
    conv = v["timestep_convergence"]
    idle = v["idle_heater_steady_state"]
    ir = v["implied_ratings"]
    # the residual is round-off and differs between platforms, so the table states a bound, not the value
    res = v["plant_energy_balance"]["max_residual_mwh"]
    res_txt = "< 1e-9" if res < 1e-9 else f"{res:.1e}"
    lines += [
        "",
        "| Check | Criterion | Result |",
        "|---|---|---|",
        f"| Plant energy balance, {len(v['plant_energy_balance']['runs'])} runs (bookkeeping) | residual < 1e-6 MWh | "
        f"max {res_txt} MWh |",
    ]
    for r in integ:
        lines.append(
            f"| Implicit Euler vs matrix exponential, {r['days']} days free-floating ({r['cooling_code']}) | "
            f"cell node error < 0.1 K | cell {r['max_abs_err_cell_k']:.3f} K, enclosure {r['max_abs_err_enclosure_k']:.3f} K |"
        )
    for r in conv:
        lines.append(
            f"| Sub-step {r['substep_min'][0]} vs {r['substep_min'][1]} min, 1.0 cyc/d ({r['cooling_code']}) | "
            f"< 0.05 pp RTE, < 1 % aux | {r['abs_diff_rte_pp']:.3f} pp, {r['abs_diff_aux_pct']:.2f} % |"
        )
    for r in idle:
        lines.append(
            f"| Idle heater at {r['t_amb_c']:g} degC vs closed form ({r['cooling_code']}) | "
            f"relative error < 1e-3 | {r['heater_sim_kw']:.3f} vs {r['heater_closed_form_kw']:.3f} kW |"
        )
    lines += [
        "",
        "| Implied by the inputs (not fitted) | Value |",
        "|---|---|",
        f"| DC round-trip efficiency at 0.5 C / 0.2 C, 25 degC | {_pct(ir['cell_dc_rte_0p5c_25c'])} / {_pct(ir['cell_dc_rte_0p2c_25c'])} |",
        f"| DC round-trip efficiency at 0.2 C, 12 degC | {_pct(ir['cell_dc_rte_0p2c_12c'])} |",
        "| PCS efficiency at 10 / 25 / 50 / 100 % load | " + " / ".join(_pct(x) for x in ir["pcs_efficiency_10_25_50_100_pct"]) + " |",
        "| Transformer efficiency at 25 / 50 / 100 % load | " + " / ".join(_pct(x) for x in ir["transformer_efficiency_25_50_100_pct"]) + " |",
        f"| Chiller COP at 35 degC ambient: liquid (coolant 18 degC) / air (air 20 degC) | {ir['cop_liquid_coolant18_amb35']:.2f} / {ir['cop_air_air20_amb35']:.2f} |",
        f"| Thermal time constant of the idle container (C_total / UA_env) | {ir['envelope_time_constant_h']:.0f} h |",
    ]
    return "\n".join(lines)


def build_headline(summaries: dict[tuple[str, str], dict]) -> str:
    """First-screen headline: net PCC RTE (liquid) with the peak C-rate of each duty, and what 0.5 C costs."""
    res, da, rated = (summaries[(d, "liquid")] for d in ("reserve_035", "da_100", "rated_100"))
    d_pcs = rated["e_pcs_mwh"] - da["e_pcs_mwh"]
    deltas = (
        f"cells {rated['e_cell_loss_mwh'] - da['e_cell_loss_mwh']:+.1f} MWh/yr, transformer "
        f"{rated['e_trafo_p0_mwh'] + rated['e_trafo_pk_mwh'] - da['e_trafo_p0_mwh'] - da['e_trafo_pk_mwh']:+.1f}, "
        f"auxiliaries {rated['e_aux_mwh'] - da['e_aux_mwh']:+.1f} and PCS {d_pcs:+.1f}"
        + (" (its no-load loss runs for fewer hours)" if d_pcs < 0.0 else "")
    )
    text = (
        f"**Headline (liquid cooling, ERA5 2025):** net RTE at the PCC is **{_pct(da['rte_pcc_net'])}** at 1 cycle/day "
        f"with a peak of {da['max_c_rate']:g} C and **{_pct(res['rte_pcc_net'])}** at 0.35 cycles/day (peak "
        f"{res['max_c_rate']:g} C). Cycling the same 5 MWh a day at the rated {rated['max_c_rate']:g} C (2 h charge, "
        f"2 h discharge) gives **{_pct(rated['rte_pcc_net'])}**, "
        f"{100.0 * (da['rte_pcc_net'] - rated['rte_pcc_net']):.1f} pp less: {deltas}."
    )
    return textwrap.fill(text, width=118, break_long_words=False, break_on_hyphens=False)


def build_parameter_table() -> str:
    lines = ["| Input | Value | Basis |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in PARAMETER_ROWS]
    return "\n".join(lines)


def build_sensitivity_table(sens_df: pd.DataFrame) -> str:
    lines = [
        "| Input (low / base / high) | 0.35 cyc/d: net PCC RTE low / high | 1.0 cyc/d: net PCC RTE low / high |",
        "|---|---|---|",
    ]
    res = sens_df[sens_df["duty_code"] == "reserve_035"].set_index("parameter")
    da = sens_df[sens_df["duty_code"] == "da_100"].set_index("parameter")
    order = da.sort_values("swing_pp", ascending=False).index
    for p in order:
        r, d = res.loc[p], da.loc[p]
        lines.append(
            f"| {p} ({d['low_value']:g} / {d['base_value']:g} / {d['high_value']:g}) | "
            f"{_pct(r['rte_pcc_low'])} / {_pct(r['rte_pcc_high'])} | {_pct(d['rte_pcc_low'])} / {_pct(d['rte_pcc_high'])} |"
        )
    return "\n".join(lines)


def _replace_block(text: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- {name}:start -->\n)(?:.*?\n)?(<!-- {name}:end -->)", re.DOTALL)
    return pattern.sub(lambda m: m.group(1) + content + "\n" + m.group(2), text)


def update_readme(readme: Path, blocks: dict[str, str]) -> None:
    """Replace generated blocks between <!-- name:start --> and <!-- name:end --> markers."""
    text = readme.read_text(encoding="utf-8")
    for name, content in blocks.items():
        text = _replace_block(text, name, content)
    readme.write_text(text, encoding="utf-8", newline="\n")


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def run_full_study(output_root: Path | None = None, weather_file: Path = DATA_FILE) -> dict:
    """Run all duty and cooling configurations, checks, sensitivity, figures, docs/data.js and README tables."""
    root = output_root or REPO_ROOT
    results_dir = root / "results"
    docs_dir = root / "docs"
    img_dir = docs_dir / "img"
    for d in (results_dir, docs_dir, img_dir):
        d.mkdir(parents=True, exist_ok=True)

    weather_df = load_weather(weather_file)

    sim_runs: dict[tuple[str, str], dict] = {}
    monthly_all: list[pd.DataFrame] = []
    for duty_code in DUTY_SPECS:
        for cool_code in COOLING_SYSTEMS:
            res = simulate_container_year(weather_df, duty_code, cool_code)
            sim_runs[(duty_code, cool_code)] = res
            m_df = res["monthly_df"].copy()
            m_df.insert(0, "cooling_code", cool_code)
            m_df.insert(0, "duty_code", duty_code)
            monthly_all.append(m_df)
    summaries = {k: _jsonable(v["summary"]) for k, v in sim_runs.items()}

    pd.DataFrame(list(summaries.values())).to_csv(results_dir / "annual_summary.csv", index=False)
    pd.concat(monthly_all, ignore_index=True).to_csv(results_dir / "monthly_rte_losses.csv", index=False)

    aux_temp_curves = steady_aux_vs_temperature(np.linspace(-15.0, 35.0, 26))
    verification = _jsonable(run_verification_suite(sim_runs, weather_df))
    (results_dir / "verification.json").write_text(json.dumps(verification, indent=2), encoding="utf-8")

    sens_df = run_sensitivity(weather_df, summaries)
    sens_df.to_csv(results_dir / "sensitivity.csv", index=False)

    generate_all_figures(sim_runs, aux_temp_curves, sens_df, img_dir=img_dir)

    s_liq, s_air = summaries[("da_100", "liquid")], summaries[("da_100", "air")]
    aux_saved_mwh = s_air["e_aux_mwh"] - s_liq["e_aux_mwh"]
    parameters = {
        "container": {**dataclasses.asdict(C), "c_cell_mj_per_k": C.c_cell_mj_per_k,
                      "ua_envelope_kw_per_k": C.ua_envelope_kw_per_k, "envelope_area_m2": C.envelope_area_m2},
        "cooling": {k: dataclasses.asdict(v) for k, v in COOLING_SYSTEMS.items()},
        "chiller": dataclasses.asdict(H),
        "pcs_transformer": dataclasses.asdict(P),
    }
    site = {
        "name": C.name,
        "location": f"Central Poland ({LATITUDE} N, {LONGITUDE} E)",
        "weather": f"ERA5 hourly 2 m temperature and GHI, {weather_df['timestamp'].dt.year.iloc[0]}, CET (UTC+1)",
        "t_amb_min_c": round(float(weather_df["t_amb_c"].min()), 1),
        "t_amb_max_c": round(float(weather_df["t_amb_c"].max()), 1),
        "t_amb_mean_c": round(float(weather_df["t_amb_c"].mean()), 2),
        "ghi_annual_kwh_m2": round(float(weather_df["ghi_wm2"].sum() / 1000.0), 1),
    }
    comparison = {
        "aux_liquid_mwh": round(s_liq["e_aux_mwh"], 3),
        "aux_air_mwh": round(s_air["e_aux_mwh"], 3),
        "aux_saved_mwh": round(aux_saved_mwh, 3),
        "aux_saved_pct": round(100.0 * aux_saved_mwh / s_air["e_aux_mwh"], 1),
        "rte_pcc_liquid": s_liq["rte_pcc_net"],
        "rte_pcc_air": s_air["rte_pcc_net"],
    }

    runs_payload = {
        f"{d}__{c}": {
            "summary": summaries[(d, c)],
            "monthly": _jsonable(res["monthly_df"].to_dict(orient="list")),
            "cold_week": res["cold_week"],
            "hot_week": res["hot_week"],
        }
        for (d, c), res in sim_runs.items()
    }
    dashboard_payload = {
        "site": site,
        "parameters": parameters,
        "liquid_vs_air_da100": comparison,
        "runs": runs_payload,
        "aux_vs_temp": aux_temp_curves,
        "verification": verification,
        "sensitivity": sens_df.to_dict(orient="records"),
    }
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    (docs_dir / "data.js").write_text(
        "window.DATA = " + json.dumps(_jsonable(dashboard_payload), ensure_ascii=False) + ";\n", encoding="utf-8"
    )
    (results_dir / "summary.json").write_text(
        json.dumps(
            {"site": site, "parameters": parameters, "liquid_vs_air_da100": comparison,
             "summaries": list(summaries.values())},
            indent=2,
        ),
        encoding="utf-8",
    )

    blocks = {
        "headline": build_headline(summaries),
        "results-tables": build_readme_tables(summaries, verification),
        "parameter-table": build_parameter_table(),
        "sensitivity-table": build_sensitivity_table(sens_df),
    }
    (results_dir / "readme_tables.md").write_text(
        "\n\n".join(blocks.values()) + "\n", encoding="utf-8", newline="\n"
    )
    readme = root / "README.md"
    if readme.exists():
        update_readme(readme, blocks)
    return dashboard_payload
