"""Matplotlib figure generation for README and documentation in docs/img/*.png."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from bess_thermal.config import DUTY_SPECS

BLUE = "#2a78d6"
NAVY = "#1b3f8f"
ORANGE = "#eb6834"
WARM = "#e8a33d"
GREEN = "#1baf7a"
RED = "#c8423b"
GREY = "#898781"
INK = "#52514e"
PURPLE = "#7a52b3"

LOSS_COMPONENTS = [
    ("e_cell_loss_mwh", "Cell losses (I^2 R + hysteresis)", BLUE),
    ("e_pcs_mwh", "PCS conversion", NAVY),
    ("e_trafo_p0_mwh", "Transformer no-load", GREY),
    ("e_trafo_pk_mwh", "Transformer load", WARM),
    ("e_chiller_mwh", "Chiller / DX compressor", GREEN),
    ("e_circ_mwh", "Pump / fans", PURPLE),
    ("e_heater_mwh", "Heater", RED),
    ("e_bms_mwh", "BMS / controls base load", INK),
]


def _style_ax(ax: plt.Axes) -> None:
    ax.set_facecolor("#ffffff")
    for spine in ax.spines.values():
        spine.set_color("#000000")
        spine.set_linewidth(0.8)
    ax.grid(alpha=0.25)
    ax.set_axisbelow(True)


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.patch.set_facecolor("#ffffff")
    fig.tight_layout()
    fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="#ffffff")
    plt.close(fig)


def _week_hours(records: list[dict]) -> np.ndarray:
    return np.arange(len(records)) * 7.0 * 24.0 / len(records)


def generate_all_figures(
    sim_runs: dict[tuple[str, str], dict],
    aux_temp_curves: dict,
    sensitivity_df: pd.DataFrame,
    img_dir: Path,
) -> None:
    """Generate the portfolio figures in docs/img/."""
    img_dir.mkdir(parents=True, exist_ok=True)
    plt.rcdefaults()

    # 1. Monthly and annual RTE across metering boundaries
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.1), gridspec_kw={"width_ratios": [1.55, 1.0]})
    _style_ax(ax1)
    _style_ax(ax2)
    m_liq = sim_runs[("da_100", "liquid")]["monthly_df"]
    m_air = sim_runs[("da_100", "air")]["monthly_df"]
    m_res = sim_runs[("reserve_035", "liquid")]["monthly_df"]
    x_m = np.arange(12)
    series = [
        (m_liq["rte_dc"], "o", "-", GREEN, "DC terminals (1.0 cyc/d, liquid)"),
        (m_liq["rte_pcs_ac"], "s", "-", NAVY, "PCS AC terminals"),
        (m_liq["rte_trafo_mv"], "^", "-", WARM, "MV side, excl. auxiliaries"),
        (m_liq["rte_pcc_net"], "o", "-", BLUE, "Net at PCC, liquid (1.0 cyc/d)"),
        (m_air["rte_pcc_net"], "d", "--", ORANGE, "Net at PCC, air (1.0 cyc/d)"),
        (m_res["rte_pcc_net"], "v", "-.", RED, "Net at PCC, liquid (0.35 cyc/d)"),
    ]
    all_vals = np.concatenate([np.asarray(s[0], dtype=float) for s in series]) * 100.0
    for vals, marker, ls, col, label in series:
        ax1.plot(x_m, vals * 100.0, marker=marker, ms=3.8, lw=1.2, ls=ls, color=col, label=label)
    ax1.set_xticks(x_m, m_liq["month_name"].tolist(), fontsize=8.5)
    ax1.set_ylabel("Round-trip efficiency [%]")
    ax1.set_ylim(np.floor(all_vals.min()) - 3.0, 100.0)
    ax1.set_title("Monthly RTE at each metering boundary (ERA5 2025, Central Poland)", fontsize=9.5)
    ax1.legend(loc="lower left", fontsize=7.3, frameon=True, ncol=2)

    stages = ["DC", "PCS AC", "MV excl.\naux", "LV net", "PCC net"]
    keys = ["rte_dc", "rte_pcs_ac", "rte_trafo_mv", "rte_lv_net", "rte_pcc_net"]
    x_st = np.arange(len(stages))
    w_bar = 0.2
    bars = [
        (("da_100", "liquid"), -1.5 * w_bar, BLUE, "1.0 cyc/d, liquid"),
        (("reserve_035", "liquid"), -0.5 * w_bar, RED, "0.35 cyc/d, liquid"),
        (("heavy_200", "liquid"), 0.5 * w_bar, GREEN, "2.0 cyc/d, liquid"),
        (("rated_100", "liquid"), 1.5 * w_bar, PURPLE, "1.0 cyc/d at 0.5 C, liquid"),
    ]
    bar_vals = []
    for run_key, off, col, label in bars:
        vals = [sim_runs[run_key]["summary"][k] * 100.0 for k in keys]
        bar_vals.extend(vals)
        ax2.bar(x_st + off, vals, width=w_bar, color=col, label=label)
    ax2.set_xticks(x_st, stages, fontsize=8)
    ax2.set_ylabel("Annual RTE [%]")
    ax2.set_ylim(np.floor(min(bar_vals)) - 3.0, 100.0)
    ax2.set_title("Annual RTE by boundary and duty", fontsize=9.5)
    ax2.legend(loc="lower left", fontsize=7.5, frameon=True)
    _save(fig, img_dir / "monthly_rte_waterfall.png")

    # 2. Steady-state auxiliary power and COP against ambient temperature
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(10.0, 3.9), gridspec_kw={"width_ratios": [1.45, 1.0]})
    _style_ax(ax_l)
    _style_ax(ax_r)
    t_grid = np.asarray(aux_temp_curves["t_amb_c"], dtype=float)
    for cool, col, name in (("liquid", BLUE, "Liquid"), ("air", ORANGE, "Forced air")):
        for c, ls, lw in (("0.00", ":", 1.0), ("0.25", "--", 1.3), ("0.50", "-", 1.6)):
            ax_l.plot(t_grid, aux_temp_curves[cool][f"c_{c}"], color=col, lw=lw, ls=ls, label=f"{name}: {c} C")
        cop = np.array([np.nan if v is None else v for v in aux_temp_curves[cool]["cop_c_0.25"]], dtype=float)
        ax_r.plot(t_grid, cop, color=col, lw=1.5, label=f"{name}, cycling at 0.25 C")
    ax_l.set_xlabel("Ambient temperature [degC]")
    ax_l.set_ylabel("Auxiliary power [kW]")
    ax_l.set_title("Steady auxiliary power vs ambient (no sun, continuous cycling)", fontsize=9.5)
    ax_l.legend(loc="upper left", fontsize=7.5, frameon=True, ncol=2)
    ax_r.set_xlabel("Ambient temperature [degC]")
    ax_r.set_ylabel("Chiller COP [-]")
    ax_r.set_title("Effective chiller COP (Carnot fraction, capped)", fontsize=9.5)
    ax_r.legend(loc="upper right", fontsize=8, frameon=True)
    _save(fig, img_dir / "aux_vs_temperature.png")

    # 3. Coldest and hottest week (15-min)
    fig, axes = plt.subplots(3, 2, figsize=(10.6, 6.4), sharex="col")
    for row in axes:
        for ax in row:
            _style_ax(ax)
    for col_i, week_key in enumerate(("cold_week", "hot_week")):
        w_liq = sim_runs[("da_100", "liquid")][week_key]
        w_air = sim_runs[("da_100", "air")][week_key]
        r_liq, r_air = w_liq["records"], w_air["records"]
        t_h = _week_hours(r_liq)
        ax0, ax1_, ax2_ = axes[0, col_i], axes[1, col_i], axes[2, col_i]
        ax0.step(t_h, [r["t_amb_c"] for r in r_liq], where="post", color=GREY, lw=0.9, label="Ambient")
        ax0.step(t_h, [r["t_cell_c"] for r in r_liq], where="post", color=BLUE, lw=1.0, label="Cells, liquid")
        ax0.step(t_h, [r["t_cell_c"] for r in r_air], where="post", color=ORANGE, lw=1.0, ls="--", label="Cells, air")
        ax0.set_ylabel("Temperature [degC]")
        ax0.legend(loc="lower right", fontsize=7.5, frameon=True, ncol=3)
        ax1_.step(t_h, [r["r_int_mohm"] for r in r_liq], where="post", color=NAVY, lw=0.9, label="Pack R_int, liquid")
        ax1_.step(t_h, [r["r_int_mohm"] for r in r_air], where="post", color=ORANGE, lw=0.9, ls="--", label="Pack R_int, air")
        ax1_.set_ylabel("R_int [mOhm]")
        ax1_.legend(loc="upper right", fontsize=7.5, frameon=True)
        ax2_.step(t_h, [r["p_aux_kw"] for r in r_liq], where="post", color=BLUE, lw=0.9, label="Aux, liquid")
        ax2_.step(t_h, [r["p_aux_kw"] for r in r_air], where="post", color=RED, lw=0.9, ls="--", label="Aux, air")
        ax2_.set_ylabel("Aux power [kW]")
        ax2_.set_xlabel(f"Hours from {r_liq[0]['timestamp']} CET")
        ax2_.legend(loc="upper right", fontsize=7.5, frameon=True)
        if week_key == "cold_week":
            ax0.set_title(f"Coldest week (1.0 cyc/d), min {w_liq['t_min_c']} degC at {w_liq['t_min_at']}", fontsize=9)
        else:
            ax0.set_title(f"Hottest week (1.0 cyc/d), max {w_liq['t_max_c']} degC at {w_liq['t_max_at']}", fontsize=9)
    _save(fig, img_dir / "winter_summer_week.png")

    # 4. Annual losses by component and duty
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(10.2, 4.1))
    _style_ax(ax_a)
    _style_ax(ax_b)
    duties = ["reserve_035", "da_100", "heavy_200", "rated_100"]
    duty_labels = ["0.35 cyc/d", "1.0 cyc/d", "2.0 cyc/d", "1.0 cyc/d\nat 0.5 C"]
    x_d = np.arange(len(duties))
    width_d = 0.34
    bottoms = {k: np.zeros(len(duties)) for k in ("la", "aa", "lp", "ap")}
    for key, label, col in LOSS_COMPONENTS:
        v_l = np.array([sim_runs[(d, "liquid")]["summary"][key] for d in duties], dtype=float)
        v_a = np.array([sim_runs[(d, "air")]["summary"][key] for d in duties], dtype=float)
        e_l = np.array([sim_runs[(d, "liquid")]["summary"]["e_in_pcc_mwh"] for d in duties], dtype=float)
        e_a = np.array([sim_runs[(d, "air")]["summary"]["e_in_pcc_mwh"] for d in duties], dtype=float)
        ax_a.bar(x_d - width_d / 2, v_l, width=width_d, bottom=bottoms["la"], color=col, label=label)
        ax_a.bar(x_d + width_d / 2, v_a, width=width_d, bottom=bottoms["aa"], color=col, alpha=0.65)
        ax_b.bar(x_d - width_d / 2, 100 * v_l / e_l, width=width_d, bottom=bottoms["lp"], color=col, label=label)
        ax_b.bar(x_d + width_d / 2, 100 * v_a / e_a, width=width_d, bottom=bottoms["ap"], color=col, alpha=0.65)
        bottoms["la"] += v_l
        bottoms["aa"] += v_a
        bottoms["lp"] += 100 * v_l / e_l
        bottoms["ap"] += 100 * v_a / e_a
    ax_a.set_xticks(x_d, duty_labels, fontsize=8.5)
    ax_a.set_ylabel("Annual losses [MWh/yr]")
    ax_a.set_title("Annual losses by component (left bar liquid, right bar air)", fontsize=9.5)
    ax_a.legend(loc="upper left", fontsize=7.3, frameon=True)
    ax_b.set_xticks(x_d, duty_labels, fontsize=8.5)
    ax_b.set_ylabel("Share of PCC import [%]")
    ax_b.set_ylim(0.0, float(max(bottoms["lp"].max(), bottoms["ap"].max())) * 1.5)
    ax_b.set_title("Losses as share of PCC import (= 100 % - net RTE)", fontsize=9.5)
    ax_b.legend(loc="upper center", fontsize=7.3, frameon=True, ncol=2)
    _save(fig, img_dir / "loss_breakdown_duty.png")

    # 5. Sensitivity tornado (net PCC RTE)
    duties_s = list(dict.fromkeys(sensitivity_df["duty_code"]))
    fig, axes_s = plt.subplots(1, len(duties_s), figsize=(10.6, 4.3), sharey=False)
    axes_s = np.atleast_1d(axes_s)
    for ax, duty_code in zip(axes_s, duties_s):
        _style_ax(ax)
        df = sensitivity_df[sensitivity_df["duty_code"] == duty_code].sort_values("swing_pp")
        base = 100.0 * float(df["rte_pcc_base"].iloc[0])
        y = np.arange(len(df))
        lo = 100.0 * df["rte_pcc_low"].to_numpy() - base
        hi = 100.0 * df["rte_pcc_high"].to_numpy() - base
        ax.barh(y, lo, color=BLUE, height=0.6, label="low value")
        ax.barh(y, hi, color=ORANGE, height=0.6, alpha=0.85, label="high value")
        ax.axvline(0.0, color="#000000", lw=0.8)
        labels = [
            f"{p} ({lv:g} / {hv:g})"
            for p, lv, hv in zip(df["parameter"], df["low_value"], df["high_value"])
        ]
        ax.set_yticks(y, labels, fontsize=7)
        ax.set_xlabel("Change of annual net PCC RTE [pp]")
        ax.set_title(f"{DUTY_SPECS[duty_code].short_label}, liquid: base {base:.1f} %", fontsize=9.5)
        ax.legend(loc="lower right", fontsize=7.5, frameon=True)
    _save(fig, img_dir / "sensitivity_tornado.png")
