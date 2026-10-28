"""First-law energy/thermal conservation checks and empirical piecewise-linear auxiliary model parameter recovery."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from bess_thermal.config import ContainerSpec, PcsTransformerSpec


def generate_synthetic_telemetry_for_recovery(
    weather_df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    b0_kw_true: float = 5.0,
    h_kw_per_k_true: float = 0.85,
    t_h_c_true: float = 12.0,
    c_kw_per_k_true: float = 0.62,
    t_c_c_true: float = 18.0,
    k_kw_per_mw_true: float = 1.40,
    noise_std_kw: float = 0.04,
    seed: int = 101,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Generate 8,760-hour container auxiliary telemetry from known piecewise-linear parameters
    plus realistic sensor noise, paired with the physical simulation's power and ambient temperature.

    Model:
      P_aux(t) = b_0 + h * max(T_h - T_amb(t), 0) + c * max(T_amb(t) - T_c, 0) + k * |P_pcs(t)|
    """
    rng = np.random.default_rng(seed)
    t_amb = weather_df["t_amb_c"].to_numpy(dtype=float)
    p_pcs_mw = np.abs(hourly_df["p_pcs_ac_kw"].to_numpy(dtype=float)) / 1000.0

    p_aux_clean_kw = (
        b0_kw_true
        + h_kw_per_k_true * np.maximum(t_h_c_true - t_amb, 0.0)
        + c_kw_per_k_true * np.maximum(t_amb - t_c_c_true, 0.0)
        + k_kw_per_mw_true * p_pcs_mw
    )
    noise = rng.normal(0.0, noise_std_kw, size=len(t_amb))
    p_aux_meas_kw = np.maximum(0.5, p_aux_clean_kw + noise)

    df_tel = pd.DataFrame(
        {
            "t_amb_c": t_amb,
            "p_pcs_mw": p_pcs_mw,
            "p_aux_meas_kw": p_aux_meas_kw,
            "p_aux_clean_kw": p_aux_clean_kw,
            "p_aux_physical_kw": hourly_df["p_aux_kw"].to_numpy(dtype=float),
        }
    )
    truth = {
        "b0_kw": b0_kw_true,
        "h_kw_per_k": h_kw_per_k_true,
        "t_h_c": t_h_c_true,
        "c_kw_per_k": c_kw_per_k_true,
        "t_c_c": t_c_c_true,
        "k_kw_per_mw": k_kw_per_mw_true,
    }
    return df_tel, truth


def fit_piecewise_linear_aux_model(
    t_amb_c: np.ndarray,
    p_pcs_mw: np.ndarray,
    p_aux_kw: np.ndarray,
    t_h_candidates: np.ndarray | None = None,
    t_c_candidates: np.ndarray | None = None,
) -> dict[str, float]:
    """Fit P_aux = b_0 + h * max(T_h - T, 0) + c * max(T - T_c, 0) + k * |P_pcs|
    by two-dimensional breakpoint grid search and non-negative least squares (NNLS).
    """
    t_amb = np.asarray(t_amb_c, dtype=float)
    p_abs = np.abs(np.asarray(p_pcs_mw, dtype=float))
    y = np.asarray(p_aux_kw, dtype=float)

    if t_h_candidates is None:
        t_h_candidates = np.round(np.arange(8.0, 15.5, 0.5), 2)
    if t_c_candidates is None:
        t_c_candidates = np.round(np.arange(15.5, 24.5, 0.5), 2)

    best_sse = float("inf")
    best_params: dict[str, float] = {}
    ones = np.ones_like(t_amb)

    for th in t_h_candidates:
        x_heat = np.maximum(th - t_amb, 0.0)
        for tc in t_c_candidates:
            if tc <= th + 1.0:
                continue
            x_cool = np.maximum(t_amb - tc, 0.0)
            design = np.column_stack([ones, x_heat, x_cool, p_abs])
            coeffs, _ = nnls(design, y)
            pred = design @ coeffs
            sse = float(np.sum((y - pred) ** 2))
            if sse < best_sse:
                best_sse = sse
                ss_tot = float(np.sum((y - np.mean(y)) ** 2))
                r2 = 1.0 - sse / max(ss_tot, 1e-12)
                rmse = float(np.sqrt(sse / len(y)))
                best_params = {
                    "b0_kw": round(float(coeffs[0]), 4),
                    "h_kw_per_k": round(float(coeffs[1]), 4),
                    "t_h_c": float(th),
                    "c_kw_per_k": round(float(coeffs[2]), 4),
                    "t_c_c": float(tc),
                    "k_kw_per_mw": round(float(coeffs[3]), 4),
                    "r2": round(r2, 5),
                    "rmse_kw": round(rmse, 4),
                }
    return best_params


def run_verification_suite(
    sim_runs: dict[tuple[str, str], dict],
    weather_df: pd.DataFrame,
    container: ContainerSpec = ContainerSpec(),
    pcs_spec: PcsTransformerSpec = PcsTransformerSpec(),
) -> dict:
    """Verify first-law electrical & thermal conservation across all 6 runs and test auxiliary parameter recovery."""
    conservation_records: list[dict] = []
    max_elec_err = 0.0
    max_therm_err = 0.0

    for (duty_code, cool_code), res in sim_runs.items():
        s = res["summary"]
        e_err = float(s["elec_balance_residual_mwh"])
        t_err = float(s["thermal_balance_residual_mj"])
        max_elec_err = max(max_elec_err, e_err)
        max_therm_err = max(max_therm_err, t_err)
        conservation_records.append(
            {
                "duty_code": duty_code,
                "cooling_code": cool_code,
                "e_in_pcc_mwh": s["e_in_pcc_mwh"],
                "e_out_pcc_mwh": s["e_out_pcc_mwh"],
                "de_stored_mwh": s["de_stored_mwh"],
                "sum_losses_mwh": round(
                    s["e_joule_mwh"]
                    + s["e_entropic_mwh"]
                    + s["e_pcs_mwh"]
                    + s["e_trafo_fe_mwh"]
                    + s["e_trafo_cu_mwh"]
                    + s["e_aux_mwh"],
                    6,
                ),
                "elec_residual_mwh": e_err,
                "thermal_residual_mj": t_err,
                "elec_pass_1e6_mwh": bool(e_err < 1e-6),
                "thermal_pass_1e6_mj": bool(t_err < 1e-6),
            }
        )

    # 1. Exact recovery test on synthetic piecewise-linear telemetry with known ground truth
    da_liq_df = sim_runs[("da_100", "liquid")]["hourly_df"]
    df_tel, truth_pw = generate_synthetic_telemetry_for_recovery(weather_df, da_liq_df)
    est_pw = fit_piecewise_linear_aux_model(
        df_tel["t_amb_c"].to_numpy(),
        df_tel["p_pcs_mw"].to_numpy(),
        df_tel["p_aux_meas_kw"].to_numpy(),
    )

    # 2. Fit empirical piecewise-linear surrogate directly to physical liquid-cooled and air-cooled telemetry
    fit_physical_liquid = fit_piecewise_linear_aux_model(
        da_liq_df["t_amb_c"].to_numpy(),
        np.abs(da_liq_df["p_pcs_ac_kw"].to_numpy()) / 1000.0,
        da_liq_df["p_aux_kw"].to_numpy(),
    )
    da_air_df = sim_runs[("da_100", "air")]["hourly_df"]
    fit_physical_air = fit_piecewise_linear_aux_model(
        da_air_df["t_amb_c"].to_numpy(),
        np.abs(da_air_df["p_pcs_ac_kw"].to_numpy()) / 1000.0,
        da_air_df["p_aux_kw"].to_numpy(),
    )

    return {
        "all_elec_conservation_pass": bool(max_elec_err < 1e-6),
        "all_thermal_conservation_pass": bool(max_therm_err < 1e-6),
        "max_elec_residual_mwh": float(max_elec_err),
        "max_thermal_residual_mj": float(max_therm_err),
        "conservation_runs": conservation_records,
        "parameter_recovery": {
            "truth": truth_pw,
            "estimate": est_pw,
            "abs_errors": {
                "b0_kw": round(abs(est_pw["b0_kw"] - truth_pw["b0_kw"]), 4),
                "h_kw_per_k": round(abs(est_pw["h_kw_per_k"] - truth_pw["h_kw_per_k"]), 4),
                "t_h_c": round(abs(est_pw["t_h_c"] - truth_pw["t_h_c"]), 4),
                "c_kw_per_k": round(abs(est_pw["c_kw_per_k"] - truth_pw["c_kw_per_k"]), 4),
                "t_c_c": round(abs(est_pw["t_c_c"] - truth_pw["t_c_c"]), 4),
                "k_kw_per_mw": round(abs(est_pw["k_kw_per_mw"] - truth_pw["k_kw_per_mw"]), 4),
            },
        },
        "physical_telemetry_surrogate_fits": {
            "liquid_da_100": {
                "physical_known": {
                    "bms_plus_idle_pump_kw": round(container.bms_base_load_kw + 0.05 * 1.8, 2),
                    "t_heat_threshold_c": container.t_min_charge_c,
                    "wall_ua_kw_per_k": 0.115,
                    "cop_20c": 3.40,
                },
                "piecewise_fit": fit_physical_liquid,
            },
            "air_da_100": {
                "physical_known": {
                    "bms_plus_idle_fan_kw": round(container.bms_base_load_kw + 0.05 * 2.9, 2),
                    "t_heat_threshold_c": container.t_min_charge_c,
                    "wall_ua_kw_per_k": 0.195,
                    "cop_20c": 2.55,
                },
                "piecewise_fit": fit_physical_air,
            },
        },
    }
