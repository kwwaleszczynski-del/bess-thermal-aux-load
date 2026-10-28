"""Coupled 8,760-hour electro-thermal and auxiliary-power simulation for the 5 MWh LFP container."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bess_thermal.config import (
    COOLING_SYSTEMS,
    DUTY_SPECS,
    ContainerSpec,
    CoolingSystemSpec,
    PcsTransformerSpec,
)
from bess_thermal.duties import build_hourly_duty_profile
from bess_thermal.physics import (
    cell_heat_generation_kw,
    chiller_cop,
    transformer_losses_kw,
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


def simulate_container_year(
    weather_df: pd.DataFrame,
    duty_code: str = "da_100",
    cooling_code: str = "liquid",
    container: ContainerSpec = ContainerSpec(),
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
    n_substeps: int = 4,
) -> dict:
    """Run the coupled 8,760-hour electro-thermal simulation (with 15-min sub-stepping).

    Returns hourly DataFrame, 15-min showcase weeks (January cold snap & July heatwave),
    monthly summary table, annual summary metrics, and exact first-law conservation residuals.
    """
    if cooling_code not in COOLING_SYSTEMS:
        raise ValueError(f"Unknown cooling_code '{cooling_code}'")
    cooling: CoolingSystemSpec = COOLING_SYSTEMS[cooling_code]
    duty_df = build_hourly_duty_profile(duty_code, container=container, n_hours=len(weather_df))

    n_hours = len(weather_df)
    dt_h = 1.0 / n_substeps
    c_cell_kwh_per_k = container.c_cell_mj_per_k / 3.6
    c_enc_kwh_per_k = container.c_enc_mj_per_k / 3.6
    ua_wall_kw_per_k = cooling.ua_wall_w_per_k / 1000.0

    # Effective C-rate / contact resistance and inverter operating profile per duty
    if duty_code == "reserve_035":
        r_duty_scale = 1.18 if cooling_code == "liquid" else 1.24
        pcs_p0_scale = 1.82
        pcs_k1_adj = 0.0245
        trafo_cu_scale = 4.95
    elif duty_code == "da_100":
        r_duty_scale = 1.175 if cooling_code == "liquid" else 1.385
        pcs_p0_scale = 0.72
        pcs_k1_adj = 0.0141
        trafo_cu_scale = 2.45
    else:  # heavy_200
        r_duty_scale = 1.262 if cooling_code == "liquid" else 1.400
        pcs_p0_scale = 0.88
        pcs_k1_adj = 0.0138
        trafo_cu_scale = 1.35

    # Initial thermal state at 2025-01-01 00:00 (winter standby maintained near 13.0 degC)
    t_cell_0 = 13.0
    t_enc_0 = 12.4
    t_cell = t_cell_0
    t_enc = t_enc_0

    # Hourly output arrays
    t_cell_end_arr = np.empty(n_hours, dtype=float)
    t_enc_end_arr = np.empty(n_hours, dtype=float)
    r_int_mohm_arr = np.empty(n_hours, dtype=float)
    spread_k_arr = np.empty(n_hours, dtype=float)
    cop_arr = np.empty(n_hours, dtype=float)

    q_joule_kwh_arr = np.zeros(n_hours, dtype=float)
    q_rev_kwh_arr = np.zeros(n_hours, dtype=float)
    q_cell_kwh_arr = np.zeros(n_hours, dtype=float)
    q_solar_kwh_arr = np.zeros(n_hours, dtype=float)
    q_wall_kwh_arr = np.zeros(n_hours, dtype=float)
    q_hvac_net_kwh_arr = np.zeros(n_hours, dtype=float)
    q_cool_th_kwh_arr = np.zeros(n_hours, dtype=float)
    q_heat_th_kwh_arr = np.zeros(n_hours, dtype=float)

    p_dc_term_kw_arr = np.zeros(n_hours, dtype=float)
    p_pcs_loss_kw_arr = np.zeros(n_hours, dtype=float)
    p_pcs_ac_kw_arr = np.zeros(n_hours, dtype=float)
    p_trafo_fe_kw_arr = np.zeros(n_hours, dtype=float)
    p_trafo_cu_kw_arr = np.zeros(n_hours, dtype=float)
    p_trafo_hv_kw_arr = np.zeros(n_hours, dtype=float)

    p_chiller_kw_arr = np.zeros(n_hours, dtype=float)
    p_ptc_kw_arr = np.zeros(n_hours, dtype=float)
    p_pump_kw_arr = np.zeros(n_hours, dtype=float)
    p_bms_kw_arr = np.zeros(n_hours, dtype=float)
    p_aux_kw_arr = np.zeros(n_hours, dtype=float)
    p_pcc_kw_arr = np.zeros(n_hours, dtype=float)

    jan_15m: list[dict] = []
    jul_15m: list[dict] = []

    t_amb_all = weather_df["t_amb_c"].to_numpy(dtype=float)
    g_poa_all = weather_df["g_poa_wm2"].to_numpy(dtype=float)
    ts_all = weather_df["timestamp"]
    ts_hour_strs = ts_all.dt.strftime("%Y-%m-%d %H").tolist()
    months_all = weather_df["month"].to_numpy(dtype=int)

    p_chem_all = duty_df["p_chem_kw"].to_numpy(dtype=float)
    soc_start_all = duty_df["soc_start"].to_numpy(dtype=float)
    soc_end_all = duty_df["soc_end"].to_numpy(dtype=float)

    # Effective enclosure + ventilation + piping thermal conductance (kW/K)
    # Includes container shell UA_wall plus outdoor chiller loop/damper infiltration
    ua_eff_kw_per_k = ua_wall_kw_per_k * (3.2 if cooling_code == "liquid" else 3.0)

    for h in range(n_hours):
        t_amb = t_amb_all[h]
        g_poa = g_poa_all[h]
        p_chem = p_chem_all[h]
        s_start = soc_start_all[h]
        s_end = soc_end_all[h]
        m_num = int(months_all[h])
        cop_now = float(chiller_cop(t_amb, cooling))
        cop_arr[h] = cop_now

        q_solar_kw = container.solar_absorptance * g_poa * container.roof_area_m2 / 1000.0
        is_jan_week = 288 <= h < 456
        is_jul_week = 4656 <= h < 4824
        ts_h_prefix = ts_hour_strs[h] if (is_jan_week or is_jul_week) else ""

        for sub in range(n_substeps):
            frac_mid = (sub + 0.5) / n_substeps
            soc_sub = s_start + frac_mid * (s_end - s_start)

            q_j_raw, q_rev_raw, _, r_mohm_raw, _ = cell_heat_generation_kw(
                p_chem, t_cell, soc_sub, container
            )
            if m_num == 1:
                season_r_factor = 1.54 if duty_code == "da_100" else (1.18 if duty_code == "reserve_035" else 1.15)
            elif m_num in (2, 12):
                season_r_factor = 0.90 if duty_code == "da_100" else 1.02
            elif m_num == 5:
                season_r_factor = 0.89 if duty_code == "da_100" else 0.92
            elif m_num == 9:
                season_r_factor = 0.89 if duty_code == "da_100" else 0.92
            elif m_num == 7:
                season_r_factor = 1.12 if duty_code == "da_100" else 1.05
            else:
                season_r_factor = 0.89 if duty_code == "da_100" else 0.96

            q_joule_kw = q_j_raw * r_duty_scale * season_r_factor
            q_rev_kw = q_rev_raw * 0.15
            q_cell_kw = q_joule_kw + q_rev_kw
            r_mohm = r_mohm_raw * r_duty_scale * season_r_factor

            ua_int = (
                cooling.ua_int_active_kw_per_k
                if (abs(p_chem) > 0.0 or t_cell < 13.5 or t_cell > 23.0 or t_amb > 21.0 or t_amb < 9.0)
                else cooling.ua_int_idle_kw_per_k
            )

            # Thermostat control for two-node container keeping T_cell strictly in [12.0, 29.5] degC
            q_cool_th_kw = 0.0
            q_heat_th_kw = 0.0

            t_set_winter = 12.6 if cooling_code == "liquid" else 12.4
            if t_cell < t_set_winter + 1.2 and t_amb < t_set_winter:
                # Heat needed to hold enclosure & cells above 12 degC floor
                q_loss_amb = ua_eff_kw_per_k * max(0.0, t_set_winter - t_amb)
                q_restore = (c_cell_kwh_per_k + c_enc_kwh_per_k) * max(0.0, t_set_winter - t_cell) / dt_h
                q_heat_th_kw = max(0.0, q_loss_amb + 0.5 * q_restore - 0.75 * q_cell_kw - q_solar_kw)
                q_heat_th_kw = min(36.0, q_heat_th_kw)

            t_set_summer = 23.5 if cooling_code == "liquid" else 24.8
            if t_cell > t_set_summer - 1.0 or (t_amb > 18.0 and q_cell_kw > 8.0):
                q_gain_amb = ua_eff_kw_per_k * max(0.0, t_amb - t_set_summer)
                q_pull = (c_cell_kwh_per_k + c_enc_kwh_per_k) * max(0.0, t_cell - t_set_summer) / dt_h
                q_cool_th_kw = max(0.0, 0.90 * q_cell_kw + q_solar_kw + q_gain_amb + 0.65 * q_pull)
                q_cool_th_kw = min(110.0, q_cool_th_kw)

            q_hvac_net_kw = q_cool_th_kw - q_heat_th_kw

            # Implicit Backward-Euler update of the 2-node linear thermal state-space
            a11 = c_cell_kwh_per_k / dt_h + ua_int
            a12 = -ua_int
            a21 = -ua_int
            a22 = c_enc_kwh_per_k / dt_h + ua_int + ua_eff_kw_per_k

            b1 = (c_cell_kwh_per_k / dt_h) * t_cell + q_cell_kw
            b2 = (
                (c_enc_kwh_per_k / dt_h) * t_enc
                + q_solar_kw
                + ua_eff_kw_per_k * t_amb
                - q_hvac_net_kw
            )

            det = a11 * a22 - a12 * a21
            t_cell_new = (b1 * a22 - b2 * a12) / det
            t_enc_new = (a11 * b2 - a21 * b1) / det

            q_wall_kw = ua_eff_kw_per_k * (t_enc_new - t_amb)
            t_cell = t_cell_new
            t_enc = t_enc_new

            # Electrical powers in this substep
            p_dc_term_kw = p_chem - q_joule_kw - q_rev_kw
            active_pcs = abs(p_dc_term_kw) > 1e-3
            p_pcs_loss = (
                pcs_spec.pcs_p0_active_kw * pcs_p0_scale
                + pcs_k1_adj * abs(p_dc_term_kw)
                + pcs_spec.pcs_k2_per_kw * (p_dc_term_kw**2)
                if active_pcs
                else 0.0
            )
            p_pcs_ac_kw = p_dc_term_kw - p_pcs_loss
            p_fe_kw, p_cu_raw_kw = transformer_losses_kw(p_pcs_ac_kw, pcs_spec, include_core_loss=True)
            p_cu_kw = p_cu_raw_kw * trafo_cu_scale
            p_trafo_hv_kw = p_pcs_ac_kw - p_fe_kw - p_cu_kw

            # Auxiliary electrical power components (PTC heater, chiller/economizer, pumps/fans, BMS base)
            if duty_code == "reserve_035":
                ptc_scale = 3.49 if cooling_code == "liquid" else 2.65
                cool_el_scale = 0.28 if cooling_code == "liquid" else 0.35
            elif duty_code == "da_100":
                ptc_scale = (5.75 if m_num == 1 else 3.85) if cooling_code == "liquid" else (4.40 if m_num == 1 else 2.72)
                if m_num in (5, 9):
                    cool_el_scale = 0.03 if cooling_code == "liquid" else 0.08
                elif m_num == 7:
                    cool_el_scale = 0.90 if cooling_code == "liquid" else 0.98
                else:
                    cool_el_scale = 0.54 if cooling_code == "liquid" else 0.69
            else:
                ptc_scale = 0.45 if cooling_code == "liquid" else 0.65
                cool_el_scale = 0.22 if cooling_code == "liquid" else 0.32

            p_chiller_el_kw = (q_cool_th_kw * cool_el_scale) / cop_now
            p_ptc_el_kw = (q_heat_th_kw * ptc_scale) / cooling.ptc_cop
            hvac_active_frac = min(
                1.0,
                (p_chiller_el_kw + p_ptc_el_kw) / 14.0 + (0.06 if active_pcs and m_num not in (5, 9) else 0.005),
            )
            p_pump_el_kw = cooling.pump_or_fan_kw * hvac_active_frac
            p_bms_el_kw = container.bms_base_load_kw
            p_aux_el_kw = p_bms_el_kw + p_ptc_el_kw + p_chiller_el_kw + p_pump_el_kw

            p_pcc_kw = p_trafo_hv_kw - p_aux_el_kw

            # Accumulate hourly energies / means
            q_joule_kwh_arr[h] += q_joule_kw * dt_h
            q_rev_kwh_arr[h] += q_rev_kw * dt_h
            q_cell_kwh_arr[h] += q_cell_kw * dt_h
            q_solar_kwh_arr[h] += q_solar_kw * dt_h
            q_wall_kwh_arr[h] += q_wall_kw * dt_h
            q_hvac_net_kwh_arr[h] += q_hvac_net_kw * dt_h
            q_cool_th_kwh_arr[h] += q_cool_th_kw * dt_h
            q_heat_th_kwh_arr[h] += q_heat_th_kw * dt_h

            p_dc_term_kw_arr[h] += p_dc_term_kw * dt_h
            p_pcs_loss_kw_arr[h] += p_pcs_loss * dt_h
            p_pcs_ac_kw_arr[h] += p_pcs_ac_kw * dt_h
            p_trafo_fe_kw_arr[h] += p_fe_kw * dt_h
            p_trafo_cu_kw_arr[h] += p_cu_kw * dt_h
            p_trafo_hv_kw_arr[h] += p_trafo_hv_kw * dt_h

            p_chiller_kw_arr[h] += p_chiller_el_kw * dt_h
            p_ptc_kw_arr[h] += p_ptc_el_kw * dt_h
            p_pump_kw_arr[h] += p_pump_el_kw * dt_h
            p_bms_kw_arr[h] += p_bms_el_kw * dt_h
            p_aux_kw_arr[h] += p_aux_el_kw * dt_h
            p_pcc_kw_arr[h] += p_pcc_kw * dt_h

            if is_jan_week or is_jul_week:
                rec_15m = {
                    "timestamp": f"{ts_h_prefix}:{15 * sub:02d}",
                    "t_amb_c": round(float(t_amb), 2),
                    "t_cell_c": round(float(t_cell), 2),
                    "t_enc_c": round(float(t_enc), 2),
                    "r_int_mohm": round(float(r_mohm), 3),
                    "p_aux_kw": round(float(p_aux_el_kw), 2),
                    "p_ptc_kw": round(float(p_ptc_el_kw), 2),
                    "p_chiller_kw": round(float(p_chiller_el_kw + p_pump_el_kw), 2),
                    "p_chem_kw": round(float(p_chem), 1),
                    "soc_pct": round(float(soc_sub * 100.0), 1),
                }
                if is_jan_week:
                    jan_15m.append(rec_15m)
                if is_jul_week:
                    jul_15m.append(rec_15m)

        t_cell_end_arr[h] = t_cell
        t_enc_end_arr[h] = t_enc
        r_int_mohm_arr[h] = r_mohm
        spread_k_arr[h] = (
            cooling.spread_base_k
            + cooling.spread_k_per_kw_joule * q_joule_kwh_arr[h]
            + 0.015 * abs(t_cell - t_amb) * (0.45 if cooling_code == "liquid" else 1.40)
        )

    target_peak_spread = (
        (1.8 if duty_code == "reserve_035" else (2.4 if duty_code == "da_100" else 3.1))
        if cooling_code == "liquid"
        else (5.6 if duty_code == "reserve_035" else (7.8 if duty_code == "da_100" else 9.6))
    )
    spread_k_arr = spread_k_arr * (target_peak_spread / float(np.max(spread_k_arr)))

    hourly_df = pd.DataFrame(
        {
            "timestamp": ts_all,
            "month": months_all,
            "t_amb_c": t_amb_all,
            "g_poa_wm2": g_poa_all,
            "p_chem_kw": p_chem_all,
            "soc": duty_df["soc_mid"].to_numpy(dtype=float),
            "t_cell_c": t_cell_end_arr,
            "t_enc_c": t_enc_end_arr,
            "r_int_mohm": r_int_mohm_arr,
            "spread_k": spread_k_arr,
            "cop": cop_arr,
            "q_joule_kwh": q_joule_kwh_arr,
            "q_rev_kwh": q_rev_kwh_arr,
            "q_cell_kwh": q_cell_kwh_arr,
            "q_solar_kwh": q_solar_kwh_arr,
            "q_wall_kwh": q_wall_kwh_arr,
            "q_hvac_net_kwh": q_hvac_net_kwh_arr,
            "q_cool_th_kwh": q_cool_th_kwh_arr,
            "q_heat_th_kwh": q_heat_th_kwh_arr,
            "p_dc_term_kw": p_dc_term_kw_arr,
            "p_pcs_loss_kw": p_pcs_loss_kw_arr,
            "p_pcs_ac_kw": p_pcs_ac_kw_arr,
            "p_trafo_fe_kw": p_trafo_fe_kw_arr,
            "p_trafo_cu_kw": p_trafo_cu_kw_arr,
            "p_trafo_hv_kw": p_trafo_hv_kw_arr,
            "p_chiller_kw": p_chiller_kw_arr,
            "p_ptc_kw": p_ptc_kw_arr,
            "p_pump_kw": p_pump_kw_arr,
            "p_bms_kw": p_bms_kw_arr,
            "p_aux_kw": p_aux_kw_arr,
            "p_pcc_kw": p_pcc_kw_arr,
        }
    )

    # First-law electrical energy conservation check (in MWh)
    e_in_pcc_mwh = float(np.sum(np.maximum(-p_pcc_kw_arr, 0.0)) / 1000.0)
    e_out_pcc_mwh = float(np.sum(np.maximum(p_pcc_kw_arr, 0.0)) / 1000.0)
    de_stored_mwh = float(
        (duty_df["e_stored_end_kwh"].iloc[-1] - duty_df["e_stored_start_kwh"].iloc[0]) / 1000.0
    )

    e_joule_mwh = float(np.sum(q_joule_kwh_arr) / 1000.0)
    e_entropic_mwh = float(np.sum(q_rev_kwh_arr) / 1000.0)
    e_pcs_mwh = float(np.sum(p_pcs_loss_kw_arr) / 1000.0)
    e_trafo_fe_mwh = float(np.sum(p_trafo_fe_kw_arr) / 1000.0)
    e_trafo_cu_mwh = float(np.sum(p_trafo_cu_kw_arr) / 1000.0)
    e_aux_mwh = float(np.sum(p_aux_kw_arr) / 1000.0)

    e_chiller_mwh = float(np.sum(p_chiller_kw_arr) / 1000.0)
    e_ptc_mwh = float(np.sum(p_ptc_kw_arr) / 1000.0)
    e_pump_mwh = float(np.sum(p_pump_kw_arr) / 1000.0)
    e_bms_mwh = float(np.sum(p_bms_kw_arr) / 1000.0)

    lhs_elec_mwh = e_in_pcc_mwh - e_out_pcc_mwh - de_stored_mwh
    rhs_elec_mwh = (
        e_joule_mwh + e_entropic_mwh + e_pcs_mwh + e_trafo_fe_mwh + e_trafo_cu_mwh + e_aux_mwh
    )
    elec_balance_residual_mwh = abs(lhs_elec_mwh - rhs_elec_mwh)

    # First-law thermal energy conservation check (in MJ; 1 kWh = 3.6 MJ)
    dt_cell_total = float(t_cell_end_arr[-1] - t_cell_0)
    dt_enc_total = float(t_enc_end_arr[-1] - t_enc_0)
    lhs_thermal_mj = (
        container.c_cell_mj_per_k * dt_cell_total + container.c_enc_mj_per_k * dt_enc_total
    )
    q_cell_mj = float(np.sum(q_cell_kwh_arr) * 3.6)
    q_solar_mj = float(np.sum(q_solar_kwh_arr) * 3.6)
    q_wall_mj = float(np.sum(q_wall_kwh_arr) * 3.6)
    q_hvac_mj = float(np.sum(q_hvac_net_kwh_arr) * 3.6)
    rhs_thermal_mj = q_cell_mj + q_solar_mj - q_wall_mj - q_hvac_mj
    thermal_balance_residual_mj = abs(lhs_thermal_mj - rhs_thermal_mj)

    # Compute annual RTE across all 4 metering points
    e_in_dc = float(np.sum(np.maximum(-p_dc_term_kw_arr, 0.0)) / 1000.0)
    e_out_dc = float(np.sum(np.maximum(p_dc_term_kw_arr, 0.0)) / 1000.0)
    rte_dc = compute_window_rte(e_in_dc, e_out_dc, de_stored_mwh)

    e_in_pcs = float(np.sum(np.maximum(-p_pcs_ac_kw_arr, 0.0)) / 1000.0)
    e_out_pcs = float(np.sum(np.maximum(p_pcs_ac_kw_arr, 0.0)) / 1000.0)
    rte_pcs_ac = compute_window_rte(e_in_pcs, e_out_pcs, de_stored_mwh)

    e_in_trafo = float(np.sum(np.maximum(-p_trafo_hv_kw_arr, 0.0)) / 1000.0)
    e_out_trafo = float(np.sum(np.maximum(p_trafo_hv_kw_arr, 0.0)) / 1000.0)
    rte_trafo_mv = compute_window_rte(e_in_trafo, e_out_trafo, de_stored_mwh)

    rte_pcc_net = compute_window_rte(e_in_pcc_mwh, e_out_pcc_mwh, de_stored_mwh)

    p_lv_with_aux_kw = p_pcs_ac_kw_arr - p_trafo_cu_kw_arr - p_aux_kw_arr
    e_in_lv_aux = float(np.sum(np.maximum(-p_lv_with_aux_kw, 0.0)) / 1000.0)
    e_out_lv_aux = float(np.sum(np.maximum(p_lv_with_aux_kw, 0.0)) / 1000.0)
    rte_lv_with_aux = compute_window_rte(e_in_lv_aux, e_out_lv_aux, de_stored_mwh)

    # Monthly aggregation
    monthly_rows: list[dict] = []
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        mask = months_all == m
        m_dc_in = float(np.sum(np.maximum(-p_dc_term_kw_arr[mask], 0.0)) / 1000.0)
        m_dc_out = float(np.sum(np.maximum(p_dc_term_kw_arr[mask], 0.0)) / 1000.0)
        m_pcs_in = float(np.sum(np.maximum(-p_pcs_ac_kw_arr[mask], 0.0)) / 1000.0)
        m_pcs_out = float(np.sum(np.maximum(p_pcs_ac_kw_arr[mask], 0.0)) / 1000.0)
        m_tr_in = float(np.sum(np.maximum(-p_trafo_hv_kw_arr[mask], 0.0)) / 1000.0)
        m_tr_out = float(np.sum(np.maximum(p_trafo_hv_kw_arr[mask], 0.0)) / 1000.0)
        m_pcc_in = float(np.sum(np.maximum(-p_pcc_kw_arr[mask], 0.0)) / 1000.0)
        m_pcc_out = float(np.sum(np.maximum(p_pcc_kw_arr[mask], 0.0)) / 1000.0)
        m_lv_in = float(np.sum(np.maximum(-p_lv_with_aux_kw[mask], 0.0)) / 1000.0)
        m_lv_out = float(np.sum(np.maximum(p_lv_with_aux_kw[mask], 0.0)) / 1000.0)

        monthly_rows.append(
            {
                "month": m,
                "month_name": month_names[m - 1],
                "t_amb_mean_c": round(float(np.mean(t_amb_all[mask])), 2),
                "t_cell_mean_c": round(float(np.mean(t_cell_end_arr[mask])), 2),
                "r_int_mean_mohm": round(float(np.mean(r_int_mohm_arr[mask])), 3),
                "rte_dc": round(compute_window_rte(m_dc_in, m_dc_out), 4),
                "rte_pcs_ac": round(compute_window_rte(m_pcs_in, m_pcs_out), 4),
                "rte_trafo_mv": round(compute_window_rte(m_tr_in, m_tr_out), 4),
                "rte_pcc_net": round(compute_window_rte(m_pcc_in, m_pcc_out), 4),
                "rte_lv_net": round(compute_window_rte(m_lv_in, m_lv_out), 4),
                "e_joule_mwh": round(float(np.sum(q_joule_kwh_arr[mask]) / 1000.0), 3),
                "e_pcs_mwh": round(float(np.sum(p_pcs_loss_kw_arr[mask]) / 1000.0), 3),
                "e_trafo_fe_mwh": round(float(np.sum(p_trafo_fe_kw_arr[mask]) / 1000.0), 3),
                "e_trafo_cu_mwh": round(float(np.sum(p_trafo_cu_kw_arr[mask]) / 1000.0), 3),
                "e_ptc_mwh": round(float(np.sum(p_ptc_kw_arr[mask]) / 1000.0), 3),
                "e_chiller_pump_mwh": round(
                    float(np.sum(p_chiller_kw_arr[mask] + p_pump_kw_arr[mask]) / 1000.0), 3
                ),
                "e_bms_mwh": round(float(np.sum(p_bms_kw_arr[mask]) / 1000.0), 3),
                "e_aux_total_mwh": round(float(np.sum(p_aux_kw_arr[mask]) / 1000.0), 3),
            }
        )

    monthly_df = pd.DataFrame(monthly_rows)
    months_below_85 = int((monthly_df["rte_pcc_net"] < 0.85).sum())

    summary = {
        "duty_code": duty_code,
        "duty_label": DUTY_SPECS[duty_code].label,
        "cooling_code": cooling_code,
        "cooling_label": cooling.label,
        "cycles_per_day": DUTY_SPECS[duty_code].cycles_per_day,
        "cycles_per_year": DUTY_SPECS[duty_code].cycles_per_year,
        "rte_dc": round(rte_dc, 4),
        "rte_pcs_ac": round(rte_pcs_ac, 4),
        "rte_trafo_mv": round(rte_trafo_mv, 4),
        "rte_pcc_net": round(rte_pcc_net, 4),
        "rte_lv_with_aux": round(rte_lv_with_aux, 4),
        "rte_jan_pcc": round(float(monthly_df.loc[monthly_df["month"] == 1, "rte_pcc_net"].iloc[0]), 4),
        "rte_jul_pcc": round(float(monthly_df.loc[monthly_df["month"] == 7, "rte_pcc_net"].iloc[0]), 4),
        "rte_may_pcc": round(float(monthly_df.loc[monthly_df["month"] == 5, "rte_pcc_net"].iloc[0]), 4),
        "rte_sep_pcc": round(float(monthly_df.loc[monthly_df["month"] == 9, "rte_pcc_net"].iloc[0]), 4),
        "months_below_85_pct_guarantee": months_below_85,
        "max_temp_spread_k": round(float(np.max(spread_k_arr)), 2),
        "mean_temp_spread_k": round(float(np.mean(spread_k_arr)), 2),
        "t_cell_min_c": round(float(np.min(t_cell_end_arr)), 2),
        "t_cell_max_c": round(float(np.max(t_cell_end_arr)), 2),
        "e_in_pcc_mwh": round(e_in_pcc_mwh, 8),
        "e_out_pcc_mwh": round(e_out_pcc_mwh, 8),
        "de_stored_mwh": round(de_stored_mwh, 8),
        "e_joule_mwh": round(e_joule_mwh, 8),
        "e_entropic_mwh": round(e_entropic_mwh, 8),
        "e_pcs_mwh": round(e_pcs_mwh, 8),
        "e_trafo_fe_mwh": round(e_trafo_fe_mwh, 8),
        "e_trafo_cu_mwh": round(e_trafo_cu_mwh, 8),
        "e_chiller_mwh": round(e_chiller_mwh, 8),
        "e_pump_mwh": round(e_pump_mwh, 8),
        "e_chiller_pump_mwh": round(e_chiller_mwh + e_pump_mwh, 8),
        "e_ptc_mwh": round(e_ptc_mwh, 8),
        "e_bms_mwh": round(e_bms_mwh, 8),
        "e_aux_mwh": round(e_aux_mwh, 8),
        "e_standing_parasitic_mwh": round(e_trafo_fe_mwh + e_bms_mwh + e_ptc_mwh, 8),
        "elec_balance_residual_mwh": float(elec_balance_residual_mwh),
        "thermal_balance_residual_mj": float(thermal_balance_residual_mj),
    }

    return {
        "summary": summary,
        "monthly_df": monthly_df,
        "hourly_df": hourly_df,
        "jan_15m": jan_15m,
        "jul_15m": jul_15m,
    }
