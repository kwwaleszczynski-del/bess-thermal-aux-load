"""Coupled electro-thermal and auxiliary-power simulation of the 5 MWh LFP container (15-min sub-steps)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bess_thermal.config import (
    COOLING_SYSTEMS,
    DUTY_SPECS,
    ChillerSpec,
    ContainerSpec,
    CoolingSystemSpec,
    PcsTransformerSpec,
)
from bess_thermal.duties import build_hourly_duty_profile
from bess_thermal.physics import (
    cell_entropic_coeff_v_per_k,
    pack_ocv_v,
    r25_soc_factor,
)

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

HOURLY_KEYS = (
    "q_irr_kwh", "q_rev_kwh", "q_solar_kwh", "q_int_kwh", "q_wall_kwh", "q_heat_kwh", "q_cool_kwh",
    "p_chem_kwh", "p_dc_kwh", "p_pcs_loss_kwh", "p_pcs_ac_kwh", "p_trafo_p0_kwh", "p_trafo_pk_kwh",
    "p_mv_kwh", "p_bms_kwh", "p_circ_kwh", "p_heater_kwh", "p_chiller_kwh", "p_aux_kwh", "p_lv_net_kwh",
    "p_pcc_kwh", "p_dc_in_kwh", "p_dc_out_kwh", "p_ac_in_kwh", "p_ac_out_kwh", "p_mv_in_kwh", "p_mv_out_kwh",
    "p_lv_in_kwh", "p_lv_out_kwh", "p_pcc_in_kwh", "p_pcc_out_kwh", "heat_h", "cool_h",
)


def compute_window_rte(
    e_in_mwh: float,
    e_out_mwh: float,
    de_stored_mwh: float = 0.0,
    eta_oneway: float | None = None,
) -> float:
    """Compute SoC-corrected round-trip efficiency over an energy window.

    Matches the exact convention of bess-ops-warranty:
      - If dE >= 0: (E_out + eta * dE) / E_in
      - If dE < 0:  E_out / (E_in + |dE| / eta)
    """
    if e_in_mwh <= 1e-9:
        return 0.0
    if abs(de_stored_mwh) < 1e-9:
        return float(e_out_mwh / e_in_mwh)
    raw_rte = max(0.50, min(0.99, e_out_mwh / e_in_mwh))
    eta = eta_oneway if eta_oneway is not None else float(np.sqrt(raw_rte))
    if de_stored_mwh >= 0.0:
        return float((e_out_mwh + eta * de_stored_mwh) / e_in_mwh)
    return float(e_out_mwh / (e_in_mwh + abs(de_stored_mwh) / eta))


def run_container(
    t_amb_h: np.ndarray,
    ghi_h: np.ndarray,
    p_chem_h: np.ndarray,
    soc_start_h: np.ndarray,
    soc_end_h: np.ndarray,
    cooling: CoolingSystemSpec,
    container: ContainerSpec = ContainerSpec(),
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
    chiller: ChillerSpec = ChillerSpec(),
    n_substeps: int = 4,
    t_init_c: tuple[float, float] | None = None,
    record_hours: np.ndarray | None = None,
) -> dict:
    """Integrate the two-node thermal model and the power chain over hourly inputs.

    Within each hour the chemical power, ambient temperature and irradiance are constant; the thermal
    state is advanced with n_substeps implicit-Euler steps. The HVAC holds the cell node inside
    [t_heat_set_c, t_cool_set_c]: when the free-floating step would leave the band it adds or removes
    exactly the heat that returns the cell node to the band edge, limited by capacity and by the
    coolant/air supply-temperature limits.

    Returns hourly energies (kWh per hour), end-of-hour temperatures, the initial and final state, and
    15-min records for hours flagged in record_hours.
    """
    n_hours = len(t_amb_h)
    n_sub = n_substeps
    dt = 1.0 / n_sub

    c1 = container.c_cell_mj_per_k / 3.6
    c2 = container.c_enc_mj_per_k / 3.6
    u_int = cooling.ua_int_kw_per_k
    u_wall = container.ua_envelope_kw_per_k
    a11 = c1 / dt + u_int
    a22 = c2 / dt + u_int + u_wall
    det = a11 * a22 - u_int * u_int
    dtc_dq = u_int / det
    dte_dq = a11 / det

    solar_factor = container.ua_roof_kw_per_k * container.solar_absorptance / container.h_out_w_per_m2_k
    t_heat, t_cool = container.t_heat_set_c, container.t_cool_set_c
    heat_cap = container.heater_capacity_kw * container.heater_efficiency
    eta_heater = container.heater_efficiency
    p_bms = container.bms_base_load_kw
    p_circ_on = cooling.circulation_kw
    ea = container.ea_over_r_k
    v_hyst_pack = container.n_series * container.cell_hysteresis_mv / 1000.0
    n_series = container.n_series
    t_heat_supply_max = container.t_heater_supply_max_c
    t_supply_min, cool_cap = cooling.t_supply_min_c, cooling.cooling_capacity_kw

    # SoC-only quantities on the sub-step grid, vectorised once and kept as Python floats for the loop
    frac = (np.arange(n_sub) + 0.5) / n_sub
    soc_sub = soc_start_h[:, None] + frac[None, :] * (soc_end_h - soc_start_h)[:, None]
    u_ocv_sub = pack_ocv_v(soc_sub, container).tolist()
    r25_sub = (
        container.cell_r25_mohm / 1000.0 * r25_soc_factor(soc_sub) * container.n_series / container.n_parallel
    ).tolist()
    dudt_sub = cell_entropic_coeff_v_per_k(soc_sub).tolist()
    t_amb_l = np.asarray(t_amb_h, dtype=float).tolist()
    ghi_l = np.asarray(ghi_h, dtype=float).tolist()
    p_chem_l = np.asarray(p_chem_h, dtype=float).tolist()

    if t_init_c is None:
        t_cell = t_enc = 0.5 * (t_heat + t_cool)
    else:
        t_cell, t_enc = float(t_init_c[0]), float(t_init_c[1])
    t_cell_0, t_enc_0 = t_cell, t_enc

    n_keys = len(HOURLY_KEYS)
    acc = np.zeros((n_hours, n_keys))
    t_cell_end = np.empty(n_hours)
    t_enc_end = np.empty(n_hours)
    r_mean = np.empty(n_hours)
    records: list[dict] = []
    rec_mask = np.zeros(n_hours, dtype=bool) if record_hours is None else np.asarray(record_hours, dtype=bool)

    pcs_p0, pcs_k1, pcs_k2, pcs_sb = pcs_spec.pcs_p0_kw, pcs_spec.pcs_k1, pcs_spec.pcs_k2_per_kw, pcs_spec.pcs_standby_kw
    tr_p0, tr_pk, tr_sr = pcs_spec.trafo_p0_kw, pcs_spec.trafo_pk_kw, pcs_spec.trafo_rated_kva
    eta_c, cond_ap, cop_max = chiller.carnot_fraction, chiller.cond_approach_k, chiller.cop_max
    evap_ap = cooling.evap_approach_k

    for h in range(n_hours):
        t_amb = t_amb_l[h]
        p_chem = p_chem_l[h]
        q_solar = solar_factor * ghi_l[h]
        active = p_chem != 0.0
        u_row, r_row, d_row = u_ocv_sub[h], r25_sub[h], dudt_sub[h]
        hs = [0.0] * n_keys
        r_sum = 0.0
        for k in range(n_sub):
            # Cell heat at the start-of-step temperature
            current = p_chem * 1000.0 / u_row[k]
            r_ohm = r_row[k] * math.exp(ea * (1.0 / (t_cell + 273.15) - 1.0 / 298.15))
            q_irr = (current * current * r_ohm + abs(current) * v_hyst_pack) / 1000.0
            q_rev = -current * (t_cell + 273.15) * n_series * d_row[k] / 1000.0
            q_cell = q_irr + q_rev
            r_sum += r_ohm

            # Free-floating implicit-Euler step (circulation runs while cycling)
            circ = active
            q_int = p_bms + (p_circ_on if circ else 0.0)
            b1 = c1 / dt * t_cell + q_cell
            b2 = c2 / dt * t_enc + u_wall * t_amb + q_solar + q_int
            tc0 = (b1 * a22 + u_int * b2) / det
            te0 = (a11 * b2 + u_int * b1) / det
            if not circ and (tc0 < t_heat or tc0 > t_cool):
                circ = True
                q_int += p_circ_on
                te0 += p_circ_on * dte_dq
                tc0 += p_circ_on * dtc_dq

            # HVAC heat into the enclosure node (+ heating, - cooling)
            q_hvac = 0.0
            if tc0 < t_heat:
                q_lim = max(0.0, (t_heat_supply_max - te0) / dte_dq)
                q_hvac = min((t_heat - tc0) / dtc_dq, heat_cap, q_lim)
            elif tc0 > t_cool:
                q_lim = min(0.0, (t_supply_min - te0) / dte_dq)
                q_hvac = max((t_cool - tc0) / dtc_dq, -cool_cap, q_lim)
            t_cell = tc0 + q_hvac * dtc_dq
            t_enc = te0 + q_hvac * dte_dq

            q_heat = q_hvac if q_hvac > 0.0 else 0.0
            q_cool = -q_hvac if q_hvac < 0.0 else 0.0
            p_heater = q_heat / eta_heater
            cop = 0.0
            p_chiller = 0.0
            if q_cool > 0.0:
                t_evap = t_enc - evap_ap
                lift = (t_amb + cond_ap) - t_evap
                cop = cop_max if lift <= 0.0 else min(cop_max, eta_c * (t_evap + 273.15) / lift)
                p_chiller = q_cool / cop
            q_wall = u_wall * (t_enc - t_amb)
            p_circ = p_circ_on if circ else 0.0
            p_aux = p_bms + p_circ + p_heater + p_chiller

            # Power chain: chemical -> DC terminals -> PCS AC -> MV side -> PCC (aux supplied at MV)
            p_dc = p_chem - q_irr
            p_abs = abs(p_dc)
            p_pcs_loss = pcs_p0 + pcs_k1 * p_abs + pcs_k2 * p_abs * p_abs if p_abs > 1e-3 else pcs_sb
            p_ac = p_dc - p_pcs_loss
            p_pk = tr_pk * (p_ac / tr_sr) ** 2
            p_mv = p_ac - tr_p0 - p_pk
            p_lv_net = p_ac - p_aux
            p_pcc = p_mv - p_aux

            # Same order as HOURLY_KEYS; import/export is split per sub-step at each metering boundary
            vals = (
                q_irr, q_rev, q_solar, q_int, q_wall, q_heat, q_cool,
                p_chem, p_dc, p_pcs_loss, p_ac, tr_p0, p_pk,
                p_mv, p_bms, p_circ, p_heater, p_chiller, p_aux, p_lv_net,
                p_pcc, max(-p_dc, 0.0), max(p_dc, 0.0), max(-p_ac, 0.0), max(p_ac, 0.0), max(-p_mv, 0.0),
                max(p_mv, 0.0), max(-p_lv_net, 0.0), max(p_lv_net, 0.0), max(-p_pcc, 0.0), max(p_pcc, 0.0),
                1.0 if q_heat > 0.0 else 0.0, 1.0 if q_cool > 0.0 else 0.0,
            )
            hs = [x + v for x, v in zip(hs, vals)]

            if rec_mask[h]:
                records.append(
                    {
                        "hour_index": h,
                        "sub": k,
                        "soc": float(soc_sub[h, k]),
                        "t_amb_c": t_amb,
                        "t_cell_c": t_cell,
                        "t_enc_c": t_enc,
                        "r_int_mohm": r_ohm * 1000.0,
                        "q_irr_kw": q_irr,
                        "q_rev_kw": q_rev,
                        "q_heat_kw": q_heat,
                        "q_cool_kw": q_cool,
                        "cop": cop,
                        "p_heater_kw": p_heater,
                        "p_chiller_kw": p_chiller,
                        "p_circ_kw": p_circ,
                        "p_aux_kw": p_aux,
                        "p_chem_kw": p_chem,
                    }
                )

        acc[h] = hs
        t_cell_end[h] = t_cell
        t_enc_end[h] = t_enc
        r_mean[h] = r_sum / n_sub * 1000.0

    acc *= dt
    return {
        "hourly": {key: acc[:, i] for i, key in enumerate(HOURLY_KEYS)},
        "t_cell_end": t_cell_end,
        "t_enc_end": t_enc_end,
        "r_int_mean_mohm": r_mean,
        "t_init": (t_cell_0, t_enc_0),
        "t_final": (t_cell, t_enc),
        "records": records,
    }


def plant_energy_balance_mwh(run: dict, de_stored_kwh: float, container: ContainerSpec = ContainerSpec()) -> dict:
    """First-law balance over the plant boundary (PCC fence), terms taken from separate code paths.

    Net import at the PCC must equal the change of stored chemical energy, the change of entropic energy
    (-sum Q_rev), the change of sensible heat in both nodes (from the temperature states), the heat leaving
    through the envelope (minus roof solar gain), the heat rejected by the chiller condenser (Q_cool + its
    electricity), the heater loss share, and the PCS and transformer losses. Joule heat, heater heat, pump
    and BMS dissipation do not appear: they must reappear as sensible heat or as heat leaving the container.
    """
    a = run["hourly"]
    c1 = container.c_cell_mj_per_k / 3.6
    c2 = container.c_enc_mj_per_k / 3.6
    (tc0, te0), (tc1, te1) = run["t_init"], run["t_final"]
    d_sensible = c1 * (tc1 - tc0) + c2 * (te1 - te0)
    net_import = float(np.sum(a["p_pcc_in_kwh"]) - np.sum(a["p_pcc_out_kwh"]))
    terms = {
        "d_chemical": de_stored_kwh,
        "d_entropic": -float(np.sum(a["q_rev_kwh"])),
        "d_sensible": d_sensible,
        "envelope_minus_solar": float(np.sum(a["q_wall_kwh"]) - np.sum(a["q_solar_kwh"])),
        "condenser": float(np.sum(a["q_cool_kwh"]) + np.sum(a["p_chiller_kwh"])),
        "heater_loss": float(np.sum(a["p_heater_kwh"]) - np.sum(a["q_heat_kwh"])),
        "pcs": float(np.sum(a["p_pcs_loss_kwh"])),
        "transformer": float(np.sum(a["p_trafo_p0_kwh"]) + np.sum(a["p_trafo_pk_kwh"])),
    }
    rhs = sum(terms.values())
    return {
        "net_import_pcc_mwh": net_import / 1000.0,
        "sum_terms_mwh": rhs / 1000.0,
        "residual_mwh": abs(net_import - rhs) / 1000.0,
        "terms_mwh": {k: v / 1000.0 for k, v in terms.items()},
    }


def _week_mask(t_amb: np.ndarray, use_min: bool) -> tuple[np.ndarray, int]:
    """Seven days around the coldest (or hottest) hour, aligned to midnight and kept inside the year."""
    n_days = len(t_amb) // 24
    idx = int(np.argmin(t_amb) if use_min else np.argmax(t_amb))
    start_day = min(max(idx // 24 - 3, 0), n_days - 7)
    mask = np.zeros(len(t_amb), dtype=bool)
    mask[start_day * 24 : (start_day + 7) * 24] = True
    return mask, idx


def simulate_container_year(
    weather_df: pd.DataFrame,
    duty_code: str = "da_100",
    cooling_code: str = "liquid",
    container: ContainerSpec = ContainerSpec(),
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
    chiller: ChillerSpec = ChillerSpec(),
    n_substeps: int = 4,
    spin_up_days: int = 30,
    cooling: CoolingSystemSpec | None = None,
) -> dict:
    """Run one weather year for a duty and cooling architecture.

    The initial thermal state is the end state of a spin-up over the last spin_up_days of the same year
    (the year is treated as periodic). `cooling` overrides the architecture spec looked up by cooling_code.
    Returns summary metrics, a monthly table, hourly results, the 15-min coldest and hottest weeks, and the
    plant energy balance.
    """
    if cooling_code not in COOLING_SYSTEMS:
        raise ValueError(f"Unknown cooling_code '{cooling_code}'")
    cooling = cooling or COOLING_SYSTEMS[cooling_code]
    n_hours = len(weather_df)
    duty_df = build_hourly_duty_profile(duty_code, container=container, n_hours=n_hours)

    t_amb = weather_df["t_amb_c"].to_numpy(dtype=float)
    ghi = weather_df["ghi_wm2"].to_numpy(dtype=float)
    months = weather_df["month"].to_numpy(dtype=int)
    ts = weather_df["timestamp"]
    p_chem = duty_df["p_chem_kw"].to_numpy(dtype=float)
    soc_s = duty_df["soc_start"].to_numpy(dtype=float)
    soc_e = duty_df["soc_end"].to_numpy(dtype=float)

    spin = slice(n_hours - spin_up_days * 24, n_hours)
    pre = run_container(
        t_amb[spin], ghi[spin], p_chem[spin], soc_s[spin], soc_e[spin], cooling, container, pcs_spec, chiller,
        n_substeps=n_substeps,
    )

    cold_mask, cold_idx = _week_mask(t_amb, use_min=True)
    hot_mask, hot_idx = _week_mask(t_amb, use_min=False)
    run = run_container(
        t_amb, ghi, p_chem, soc_s, soc_e, cooling, container, pcs_spec, chiller,
        n_substeps=n_substeps, t_init_c=pre["t_final"], record_hours=cold_mask | hot_mask,
    )
    a = run["hourly"]
    de_stored_kwh = float(duty_df["e_stored_end_kwh"].iloc[-1] - duty_df["e_stored_start_kwh"].iloc[0])
    balance = plant_energy_balance_mwh(run, de_stored_kwh, container)

    hourly_df = pd.DataFrame(
        {
            "timestamp": ts.to_numpy(),
            "month": months,
            "t_amb_c": t_amb,
            "ghi_wm2": ghi,
            "p_chem_kw": p_chem,
            "soc": duty_df["soc_mid"].to_numpy(dtype=float),
            "t_cell_c": run["t_cell_end"],
            "t_enc_c": run["t_enc_end"],
            "r_int_mohm": run["r_int_mean_mohm"],
            **{k.replace("_kwh", "_kw") if k.startswith("p_") else k: v for k, v in a.items()},
        }
    )

    def mwh(key: str, mask: np.ndarray | slice = slice(None)) -> float:
        return float(np.sum(a[key][mask]) / 1000.0)

    def rte(prefix: str, mask: np.ndarray | slice = slice(None)) -> float:
        return compute_window_rte(mwh(f"p_{prefix}_in_kwh", mask), mwh(f"p_{prefix}_out_kwh", mask))

    def records_for(mask: np.ndarray) -> list[dict]:
        hours = set(np.flatnonzero(mask).tolist())
        out = []
        for r in run["records"]:
            if r["hour_index"] in hours:
                stamp = pd.Timestamp(ts.iloc[r["hour_index"]]) + pd.Timedelta(minutes=60 // n_substeps * r["sub"])
                out.append(
                    {
                        "timestamp": stamp.strftime("%Y-%m-%d %H:%M"),
                        "t_amb_c": round(r["t_amb_c"], 2),
                        "t_cell_c": round(r["t_cell_c"], 2),
                        "t_enc_c": round(r["t_enc_c"], 2),
                        "r_int_mohm": round(r["r_int_mohm"], 3),
                        "p_aux_kw": round(r["p_aux_kw"], 3),
                        "p_heater_kw": round(r["p_heater_kw"], 3),
                        "p_chiller_kw": round(r["p_chiller_kw"], 3),
                        "p_circ_kw": round(r["p_circ_kw"], 3),
                        "cop": round(r["cop"], 3),
                        "p_chem_kw": round(r["p_chem_kw"], 1),
                        "soc_pct": round(r["soc"] * 100.0, 1),
                    }
                )
        return out

    monthly_rows: list[dict] = []
    for m in range(1, 13):
        mk = months == m
        q_cool_m, e_ch_m = mwh("q_cool_kwh", mk), mwh("p_chiller_kwh", mk)
        monthly_rows.append(
            {
                "month": m,
                "month_name": MONTH_NAMES[m - 1],
                "t_amb_mean_c": round(float(np.mean(t_amb[mk])), 2),
                "t_cell_mean_c": round(float(np.mean(run["t_cell_end"][mk])), 2),
                "r_int_mean_mohm": round(float(np.mean(run["r_int_mean_mohm"][mk])), 3),
                "rte_dc": round(rte("dc", mk), 4),
                "rte_pcs_ac": round(rte("ac", mk), 4),
                "rte_trafo_mv": round(rte("mv", mk), 4),
                "rte_lv_net": round(rte("lv", mk), 4),
                "rte_pcc_net": round(rte("pcc", mk), 4),
                "e_cell_loss_mwh": round(mwh("q_irr_kwh", mk), 3),
                "e_pcs_mwh": round(mwh("p_pcs_loss_kwh", mk), 3),
                "e_trafo_p0_mwh": round(mwh("p_trafo_p0_kwh", mk), 3),
                "e_trafo_pk_mwh": round(mwh("p_trafo_pk_kwh", mk), 3),
                "e_heater_mwh": round(mwh("p_heater_kwh", mk), 3),
                "e_chiller_mwh": round(e_ch_m, 3),
                "e_circ_mwh": round(mwh("p_circ_kwh", mk), 3),
                "e_bms_mwh": round(mwh("p_bms_kwh", mk), 3),
                "e_aux_mwh": round(mwh("p_aux_kwh", mk), 3),
                "cop_mean": round(q_cool_m / e_ch_m, 3) if e_ch_m > 0 else None,
            }
        )
    monthly_df = pd.DataFrame(monthly_rows)
    pcc_by_month = monthly_df.set_index("month")["rte_pcc_net"]

    e_cool, e_chiller = mwh("q_cool_kwh"), mwh("p_chiller_kwh")
    duty = DUTY_SPECS[duty_code]
    summary = {
        "duty_code": duty_code,
        "duty_label": duty.label,
        "cooling_code": cooling_code,
        "cooling_label": cooling.label,
        "cycles_per_day": duty.cycles_per_day,
        "cycles_per_year": duty.cycles_per_year,
        "max_c_rate": round(float(np.max(np.abs(p_chem)) / container.rated_energy_kwh), 3),
        "rte_dc": round(rte("dc"), 4),
        "rte_pcs_ac": round(rte("ac"), 4),
        "rte_trafo_mv": round(rte("mv"), 4),
        "rte_lv_net": round(rte("lv"), 4),
        "rte_pcc_net": round(rte("pcc"), 4),
        "rte_pcc_month_min": round(float(pcc_by_month.min()), 4),
        "rte_pcc_month_min_name": MONTH_NAMES[int(pcc_by_month.idxmin()) - 1],
        "rte_pcc_month_max": round(float(pcc_by_month.max()), 4),
        "rte_pcc_month_max_name": MONTH_NAMES[int(pcc_by_month.idxmax()) - 1],
        "months_below_85_pct": int((pcc_by_month < 0.85).sum()),
        "t_cell_min_c": round(float(np.min(run["t_cell_end"])), 2),
        "t_cell_max_c": round(float(np.max(run["t_cell_end"])), 2),
        "t_cell_mean_c": round(float(np.mean(run["t_cell_end"])), 2),
        "hours_heating": round(float(np.sum(a["heat_h"])), 1),
        "hours_cooling": round(float(np.sum(a["cool_h"])), 1),
        "cop_mean": round(e_cool / e_chiller, 3) if e_chiller > 0 else None,
        "e_in_pcc_mwh": round(mwh("p_pcc_in_kwh"), 6),
        "e_out_pcc_mwh": round(mwh("p_pcc_out_kwh"), 6),
        "de_stored_mwh": round(de_stored_kwh / 1000.0, 6),
        "e_cell_loss_mwh": round(mwh("q_irr_kwh"), 6),
        "e_entropic_mwh": round(mwh("q_rev_kwh"), 6),
        "e_pcs_mwh": round(mwh("p_pcs_loss_kwh"), 6),
        "e_trafo_p0_mwh": round(mwh("p_trafo_p0_kwh"), 6),
        "e_trafo_pk_mwh": round(mwh("p_trafo_pk_kwh"), 6),
        "e_heat_th_mwh": round(mwh("q_heat_kwh"), 6),
        "e_cool_th_mwh": round(e_cool, 6),
        "e_heater_mwh": round(mwh("p_heater_kwh"), 6),
        "e_chiller_mwh": round(e_chiller, 6),
        "e_circ_mwh": round(mwh("p_circ_kwh"), 6),
        "e_bms_mwh": round(mwh("p_bms_kwh"), 6),
        "e_aux_mwh": round(mwh("p_aux_kwh"), 6),
        "e_fixed_mwh": round(mwh("p_trafo_p0_kwh") + mwh("p_bms_kwh"), 6),
        "plant_balance_residual_mwh": balance["residual_mwh"],
    }

    return {
        "summary": summary,
        "monthly_df": monthly_df,
        "hourly_df": hourly_df,
        "cold_week": {
            "t_min_c": round(float(t_amb[cold_idx]), 1),
            "t_min_at": pd.Timestamp(ts.iloc[cold_idx]).strftime("%Y-%m-%d %H:%M"),
            "records": records_for(cold_mask),
        },
        "hot_week": {
            "t_max_c": round(float(t_amb[hot_idx]), 1),
            "t_max_at": pd.Timestamp(ts.iloc[hot_idx]).strftime("%Y-%m-%d %H:%M"),
            "records": records_for(hot_mask),
        },
        "energy_balance": balance,
    }


def steady_aux_vs_temperature(
    t_grid_c: np.ndarray,
    c_rates: tuple[float, ...] = (0.0, 0.25, 0.50),
    container: ContainerSpec = ContainerSpec(),
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
    chiller: ChillerSpec = ChillerSpec(),
    days: int = 6,
    avg_days: int = 2,
) -> dict:
    """Mean auxiliary power and chiller COP at constant ambient temperature, no sun, and continuous cycling.

    Cycling alternates one hour of discharge and one hour of charge at c_rate (SoC between 0.75 - c and 0.75).
    The model starts at the band edge it settles to and runs `days`; the last `avg_days` are averaged.
    """
    n_h = days * 24
    tail = slice(n_h - avg_days * 24, n_h)
    out: dict = {"t_amb_c": [round(float(t), 2) for t in t_grid_c]}
    for cool_code, cooling in COOLING_SYSTEMS.items():
        out[cool_code] = {}
        for c in c_rates:
            p = c * container.rated_energy_kwh
            p_chem = np.where(np.arange(n_h) % 2 == 0, p, -p)
            e = 0.75 * container.rated_energy_kwh - np.concatenate([[0.0], np.cumsum(p_chem)])
            soc = e / container.rated_energy_kwh
            aux, cop = [], []
            for t in t_grid_c:
                t_free = t + container.bms_base_load_kw / container.ua_envelope_kw_per_k
                t0 = container.t_cool_set_c if c > 0 else min(max(t_free, container.t_heat_set_c), container.t_cool_set_c)
                res = run_container(
                    np.full(n_h, float(t)), np.zeros(n_h), p_chem, soc[:-1], soc[1:], cooling, container,
                    pcs_spec, chiller, t_init_c=(t0, t0),
                )
                a = res["hourly"]
                aux.append(round(float(np.mean(a["p_aux_kwh"][tail])), 3))
                e_ch = float(np.sum(a["p_chiller_kwh"][tail]))
                cop.append(round(float(np.sum(a["q_cool_kwh"][tail]) / e_ch), 3) if e_ch > 0 else None)
            out[cool_code][f"c_{c:.2f}"] = aux
            out[cool_code][f"cop_c_{c:.2f}"] = cop
    return out
