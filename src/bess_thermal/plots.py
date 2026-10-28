"""Matplotlib figure generation for README and documentation in docs/img/*.png."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


BLUE = "#2a78d6"
NAVY = "#1b3f8f"
ORANGE = "#eb6834"
WARM = "#e8a33d"
GREEN = "#1baf7a"
RED = "#c8423b"
GREY = "#898781"
INK = "#52514e"
PURPLE = "#7a52b3"


def _style_ax(ax: plt.Axes) -> None:
    ax.set_facecolor("#ffffff")
    for spine in ax.spines.values():
        spine.set_visible(True)
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


def generate_all_figures(
    sim_runs: dict[tuple[str, str], dict],
    aux_temp_curves: dict,
    img_dir: Path,
) -> None:
    """Generate the 4 portfolio figures in docs/img/."""
    img_dir.mkdir(parents=True, exist_ok=True)
    plt.rcdefaults()

    # 1. docs/img/monthly_rte_waterfall.png
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.1), gridspec_kw={"width_ratios": [1.55, 1.0]})
    _style_ax(ax1)
    _style_ax(ax2)

    m_da_liq = sim_runs[("da_100", "liquid")]["monthly_df"]
    m_da_air = sim_runs[("da_100", "air")]["monthly_df"]
    m_res_liq = sim_runs[("reserve_035", "liquid")]["monthly_df"]
    x_m = np.arange(12)
    m_labels = m_da_liq["month_name"].tolist()

    ax1.plot(x_m, m_da_liq["rte_dc"] * 100.0, marker="o", ms=3.8, lw=1.2, color=GREEN, label="DC cell terminals (1.0 cyc/d)")
    ax1.plot(x_m, m_da_liq["rte_pcs_ac"] * 100.0, marker="s", ms=3.6, lw=1.1, color=NAVY, label="PCS AC terminals (1.0 cyc/d)")
    ax1.plot(x_m, m_da_liq["rte_trafo_mv"] * 100.0, marker="^", ms=3.8, lw=1.1, color=WARM, label="MV trafo 15 kV excl. aux (1.0 cyc/d)")
    ax1.plot(x_m, m_da_liq["rte_pcc_net"] * 100.0, marker="o", ms=4.2, lw=1.5, color=BLUE, label="Net 15 kV PCC, liquid (1.0 cyc/d)")
    ax1.plot(x_m, m_da_air["rte_pcc_net"] * 100.0, marker="d", ms=3.6, lw=1.1, ls="--", color=ORANGE, label="Net 15 kV PCC, air (1.0 cyc/d)")
    ax1.plot(x_m, m_res_liq["rte_pcc_net"] * 100.0, marker="v", ms=3.8, lw=1.2, ls="-.", color=RED, label="Net 15 kV PCC, liquid (0.35 cyc/d)")
    ax1.axhline(85.0, color=INK, ls=":", lw=0.9, label="Illustrative 85 % RTE guarantee")

    ax1.set_xticks(x_m, m_labels, fontsize=8.5)
    ax1.set_ylabel("Round-trip efficiency [%]")
    ax1.set_ylim(66.0, 99.5)
    ax1.set_title("Monthly RTE across metering boundaries (Poland, 5 MWh)", fontsize=9.5)
    ax1.legend(loc="lower left", fontsize=7.5, frameon=True, ncol=2)

    s_da_liq = sim_runs[("da_100", "liquid")]["summary"]
    s_da_air = sim_runs[("da_100", "air")]["summary"]
    s_res_liq = sim_runs[("reserve_035", "liquid")]["summary"]
    stages = ["DC cells", "PCS AC", "MV trafo\n(excl. aux)", "Net PCC\n(liquid)", "Net PCC\n(air)"]
    vals_100 = [
        s_da_liq["rte_dc"] * 100.0,
        s_da_liq["rte_pcs_ac"] * 100.0,
        s_da_liq["rte_trafo_mv"] * 100.0,
        s_da_liq["rte_pcc_net"] * 100.0,
        s_da_air["rte_pcc_net"] * 100.0,
    ]
    vals_035 = [
        s_res_liq["rte_dc"] * 100.0,
        s_res_liq["rte_pcs_ac"] * 100.0,
        s_res_liq["rte_trafo_mv"] * 100.0,
        s_res_liq["rte_pcc_net"] * 100.0,
        sim_runs[("reserve_035", "air")]["summary"]["rte_pcc_net"] * 100.0,
    ]
    x_st = np.arange(len(stages))
    w_bar = 0.34
    ax2.bar(x_st - w_bar / 2, vals_100, width=w_bar, color=BLUE, label="1.0 cycle/day (DA)")
    ax2.bar(x_st + w_bar / 2, vals_035, width=w_bar, color=RED, label="0.35 cycles/day (Reserve)")
    ax2.axhline(85.0, color=INK, ls=":", lw=0.9)
    ax2.set_xticks(x_st, stages, fontsize=8)
    ax2.set_ylabel("Annual RTE [%]")
    ax2.set_ylim(65.0, 99.5)
    ax2.set_title("Annual RTE waterfall by duty", fontsize=9.5)
    ax2.legend(loc="lower left", fontsize=8, frameon=True)
    _save(fig, img_dir / "monthly_rte_waterfall.png")

    # 2. docs/img/aux_vs_temperature.png
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(10.0, 3.9), gridspec_kw={"width_ratios": [1.45, 1.0]})
    _style_ax(ax_l)
    _style_ax(ax_r)

    t_grid = np.asarray(aux_temp_curves["t_amb_c"], dtype=float)
    ax_l.plot(t_grid, aux_temp_curves["liquid"]["c_0.00"], color=BLUE, lw=1.0, ls=":", label="Liquid: idle (0.0 C)")
    ax_l.plot(t_grid, aux_temp_curves["liquid"]["c_0.25"], color=BLUE, lw=1.3, ls="--", label="Liquid: 0.25 C (1.25 MW)")
    ax_l.plot(t_grid, aux_temp_curves["liquid"]["c_0.50"], color=NAVY, lw=1.5, label="Liquid: 0.50 C (2.50 MW)")
    ax_l.plot(t_grid, aux_temp_curves["air"]["c_0.00"], color=ORANGE, lw=1.0, ls=":", label="Forced air: idle (0.0 C)")
    ax_l.plot(t_grid, aux_temp_curves["air"]["c_0.25"], color=ORANGE, lw=1.3, ls="--", label="Forced air: 0.25 C (1.25 MW)")
    ax_l.plot(t_grid, aux_temp_curves["air"]["c_0.50"], color=RED, lw=1.5, label="Forced air: 0.50 C (2.50 MW)")
    ax_l.axvline(12.0, color=GREY, ls="--", lw=0.8)
    ax_l.set_xlabel("Ambient temperature T_amb [degC]")
    ax_l.set_ylabel("Auxiliary load P_aux [kW]")
    ax_l.set_xlim(-15.0, 35.0)
    ax_l.set_ylim(0.0, 44.0)
    ax_l.set_title("Container auxiliary power vs ambient temperature and C-rate", fontsize=9.5)
    ax_l.legend(loc="upper center", fontsize=7.5, frameon=True, ncol=2)

    from bess_thermal.config import COOLING_SYSTEMS
    from bess_thermal.physics import chiller_cop

    cop_liq = [float(chiller_cop(t, COOLING_SYSTEMS["liquid"])) for t in t_grid]
    cop_air = [float(chiller_cop(t, COOLING_SYSTEMS["air"])) for t in t_grid]
    ax_r.plot(t_grid, cop_liq, color=BLUE, lw=1.5, label="Glycol chiller COP (liquid)")
    ax_r.plot(t_grid, cop_air, color=ORANGE, lw=1.5, ls="--", label="Rooftop DX unit COP (air)")
    ax_r.axhline(0.98, color=RED, lw=1.1, ls=":", label="Winter PTC heater COP (0.98)")
    ax_r.set_xlabel("Ambient temperature T_amb [degC]")
    ax_r.set_ylabel("Thermal COP [-]")
    ax_r.set_xlim(-15.0, 35.0)
    ax_r.set_ylim(0.5, 4.6)
    ax_r.set_title("Chiller and PTC heater COP vs ambient", fontsize=9.5)
    ax_r.legend(loc="upper right", fontsize=8, frameon=True)
    _save(fig, img_dir / "aux_vs_temperature.png")

    # 3. docs/img/winter_summer_week.png
    fig, axes = plt.subplots(3, 2, figsize=(10.6, 6.4), sharex="col")
    for row in axes:
        for ax in row:
            _style_ax(ax)

    jan_liq = sim_runs[("da_100", "liquid")]["jan_15m"]
    jan_air = sim_runs[("da_100", "air")]["jan_15m"]
    jul_liq = sim_runs[("da_100", "liquid")]["jul_15m"]
    jul_air = sim_runs[("da_100", "air")]["jul_15m"]

    t_hours = np.arange(len(jan_liq)) * 0.25

    # Column 0: January cold snap week
    axes[0, 0].step(t_hours, [r["t_amb_c"] for r in jan_liq], where="post", color=GREY, lw=0.9, label="Ambient T_amb")
    axes[0, 0].step(t_hours, [r["t_cell_c"] for r in jan_liq], where="post", color=BLUE, lw=0.9, label="Cell T_cell (liquid)")
    axes[0, 0].step(t_hours, [r["t_cell_c"] for r in jan_air], where="post", color=ORANGE, lw=0.9, ls="--", label="Cell T_cell (air)")
    axes[0, 0].axhline(12.0, color=RED, ls=":", lw=0.8, label="12 degC plating limit")
    axes[0, 0].set_ylabel("Temperature [degC]")
    axes[0, 0].set_ylim(-15.0, 31.0)
    axes[0, 0].set_title("January cold snap week (13-19 Jan, min -12.2 degC)", fontsize=9.5)
    axes[0, 0].legend(loc="lower right", fontsize=7.5, frameon=True, ncol=2)

    axes[1, 0].step(t_hours, [r["r_int_mohm"] for r in jan_liq], where="post", color=NAVY, lw=0.9, label="Pack R_int (liquid)")
    axes[1, 0].step(t_hours, [r["r_int_mohm"] for r in jan_air], where="post", color=ORANGE, lw=0.9, ls="--", label="Pack R_int (air)")
    axes[1, 0].set_ylabel("R_int [mOhm]")
    axes[1, 0].set_ylim(20.0, 68.0)
    axes[1, 0].legend(loc="lower right", fontsize=8, frameon=True)

    axes[2, 0].step(t_hours, [r["p_aux_kw"] for r in jan_liq], where="post", color=BLUE, lw=0.9, label="P_aux total (liquid)")
    axes[2, 0].step(t_hours, [r["p_aux_kw"] for r in jan_air], where="post", color=RED, lw=0.9, ls="--", label="P_aux total (air)")
    axes[2, 0].set_xlabel("Hours from 2025-01-13 00:00 [h]")
    axes[2, 0].set_ylabel("Aux power P_aux [kW]")
    axes[2, 0].set_ylim(0.0, 68.0)
    axes[2, 0].legend(loc="upper right", fontsize=8, frameon=True)

    # Column 1: July heatwave week
    axes[0, 1].step(t_hours, [r["t_amb_c"] for r in jul_liq], where="post", color=GREY, lw=0.9, label="Ambient T_amb")
    axes[0, 1].step(t_hours, [r["t_cell_c"] for r in jul_liq], where="post", color=BLUE, lw=0.9, label="Cell T_cell (liquid)")
    axes[0, 1].step(t_hours, [r["t_cell_c"] for r in jul_air], where="post", color=ORANGE, lw=0.9, ls="--", label="Cell T_cell (air)")
    axes[0, 1].set_ylabel("Temperature [degC]")
    axes[0, 1].set_ylim(10.0, 37.0)
    axes[0, 1].set_title("July heatwave week (14-20 Jul, max +33.2 degC)", fontsize=9.5)
    axes[0, 1].legend(loc="lower right", fontsize=7.5, frameon=True, ncol=2)

    axes[1, 1].step(t_hours, [r["r_int_mohm"] for r in jul_liq], where="post", color=NAVY, lw=0.9, label="Pack R_int (liquid)")
    axes[1, 1].step(t_hours, [r["r_int_mohm"] for r in jul_air], where="post", color=ORANGE, lw=0.9, ls="--", label="Pack R_int (air)")
    axes[1, 1].set_ylabel("R_int [mOhm]")
    axes[1, 1].set_ylim(20.0, 44.0)
    axes[1, 1].legend(loc="upper right", fontsize=8, frameon=True)

    axes[2, 1].step(t_hours, [r["p_aux_kw"] for r in jul_liq], where="post", color=BLUE, lw=0.9, label="P_aux total (liquid)")
    axes[2, 1].step(t_hours, [r["p_aux_kw"] for r in jul_air], where="post", color=RED, lw=0.9, ls="--", label="P_aux total (air)")
    axes[2, 1].set_xlabel("Hours from 2025-07-14 00:00 [h]")
    axes[2, 1].set_ylabel("Aux power P_aux [kW]")
    axes[2, 1].set_ylim(0.0, 44.0)
    axes[2, 1].legend(loc="upper right", fontsize=8, frameon=True)
    _save(fig, img_dir / "winter_summer_week.png")

    # 4. docs/img/loss_breakdown_duty.png
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(10.2, 4.1))
    _style_ax(ax_a)
    _style_ax(ax_b)

    duties = ["reserve_035", "da_100", "heavy_200"]
    duty_labels = ["0.35 cyc/day\n(128 cyc/yr)", "1.0 cyc/day\n(365 cyc/yr)", "2.0 cyc/day\n(730 cyc/yr)"]
    x_d = np.arange(len(duties))
    width_d = 0.34

    loss_components = [
        ("e_joule_mwh", "Cell Joule I^2 R", BLUE),
        ("e_pcs_mwh", "PCS switching + cond.", NAVY),
        ("e_trafo_fe_mwh", "MV trafo core P_fe (24/7)", GREY),
        ("e_trafo_cu_mwh", "MV trafo copper P_cu", WARM),
        ("e_chiller_pump_mwh", "Chiller / DX + pumps", GREEN),
        ("e_ptc_mwh", "Winter PTC heater", RED),
        ("e_bms_mwh", "BMS / controls base load", PURPLE),
    ]

    bottom_liq = np.zeros(len(duties))
    bottom_air = np.zeros(len(duties))
    for key, label, col in loss_components:
        vals_l = np.array([sim_runs[(d, "liquid")]["summary"][key] for d in duties], dtype=float)
        vals_a = np.array([sim_runs[(d, "air")]["summary"][key] for d in duties], dtype=float)
        ax_a.bar(x_d - width_d / 2, vals_l, width=width_d, bottom=bottom_liq, color=col, label=label)
        ax_a.bar(x_d + width_d / 2, vals_a, width=width_d, bottom=bottom_air, color=col, alpha=0.72)
        bottom_liq += vals_l
        bottom_air += vals_a

    ax_a.set_xticks(x_d, duty_labels, fontsize=8.5)
    ax_a.set_ylabel("Annual energy loss [MWh / year]")
    ax_a.set_ylim(0.0, max(float(np.max(bottom_air)) * 1.32, 720.0))
    ax_a.set_title("Annual MWh losses by component (left: liquid, right: air)", fontsize=9.5)
    ax_a.legend(loc="upper left", fontsize=7.5, frameon=True)

    # Panel B: Specific loss per MWh of net PCC energy discharged (% of E_in)
    bottom_pct_liq = np.zeros(len(duties))
    bottom_pct_air = np.zeros(len(duties))
    for key, label, col in loss_components:
        pct_l = np.array(
            [
                100.0 * sim_runs[(d, "liquid")]["summary"][key] / sim_runs[(d, "liquid")]["summary"]["e_in_pcc_mwh"]
                for d in duties
            ],
            dtype=float,
        )
        pct_a = np.array(
            [
                100.0 * sim_runs[(d, "air")]["summary"][key] / sim_runs[(d, "air")]["summary"]["e_in_pcc_mwh"]
                for d in duties
            ],
            dtype=float,
        )
        ax_b.bar(x_d - width_d / 2, pct_l, width=width_d, bottom=bottom_pct_liq, color=col, label=label)
        ax_b.bar(x_d + width_d / 2, pct_a, width=width_d, bottom=bottom_pct_air, color=col, alpha=0.72)
        bottom_pct_liq += pct_l
        bottom_pct_air += pct_a

    ax_b.set_xticks(x_d, duty_labels, fontsize=8.5)
    ax_b.set_ylabel("Share of PCC energy input E_in [%]")
    ax_b.set_ylim(0.0, 36.0)
    ax_b.set_title("Loss share of gross PCC input (100% - Net RTE)", fontsize=9.5)
    ax_b.legend(loc="upper right", fontsize=7.5, frameon=True)
    _save(fig, img_dir / "loss_breakdown_duty.png")
