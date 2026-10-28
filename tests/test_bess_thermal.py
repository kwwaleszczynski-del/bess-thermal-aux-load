"""Comprehensive test suite for bess-thermal-aux-load."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bess_thermal.cli import main as cli_main
from bess_thermal.config import COOLING_SYSTEMS, DUTY_SPECS, ContainerSpec, PcsTransformerSpec
from bess_thermal.duties import build_hourly_duty_profile
from bess_thermal.physics import (
    cell_heat_generation_kw,
    cell_internal_resistance_ohm,
    chiller_cop,
    entropic_coeff_v_per_k,
    evaluate_steady_aux_vs_temperature,
    ocv_pack_voltage_v,
    pcs_loss_kw,
    transformer_losses_kw,
)
from bess_thermal.simulation import compute_window_rte, simulate_container_year
from bess_thermal.verification import (
    fit_piecewise_linear_aux_model,
    generate_synthetic_telemetry_for_recovery,
    run_verification_suite,
)
from bess_thermal.weather import generate_polish_weather_8760


@pytest.fixture(scope="module")
def weather_8760():
    return generate_polish_weather_8760()


@pytest.fixture(scope="module")
def all_sim_runs(weather_8760):
    runs = {}
    for d in DUTY_SPECS:
        for c in COOLING_SYSTEMS:
            runs[(d, c)] = simulate_container_year(weather_8760, duty_code=d, cooling_code=c)
    return runs


def test_weather_profile_shape_and_extremes(weather_8760):
    assert len(weather_8760) == 8760
    assert set(weather_8760["month"].unique()) == set(range(1, 13))
    # January cold snap reaches below -11.5 degC and July heatwave exceeds +32.5 degC
    jan_min = float(weather_8760.loc[weather_8760["month"] == 1, "t_amb_c"].min())
    jul_max = float(weather_8760.loc[weather_8760["month"] == 7, "t_amb_c"].max())
    assert -15.0 <= jan_min <= -11.5
    assert 32.5 <= jul_max <= 36.0
    assert float(weather_8760["g_poa_wm2"].min()) == 0.0
    assert float(weather_8760["g_poa_wm2"].max()) > 750.0


def test_arrhenius_internal_resistance_doubles_from_25c_to_5c():
    spec = ContainerSpec()
    r_25 = cell_internal_resistance_ohm(25.0, 0.50, spec)
    r_5 = cell_internal_resistance_ohm(5.0, 0.50, spec)
    r_15 = cell_internal_resistance_ohm(15.0, 0.50, spec)
    assert r_5 / r_25 == pytest.approx(2.0, rel=1e-5)
    assert r_25 < r_15 < r_5


def test_internal_resistance_soc_dependence():
    r_mid = cell_internal_resistance_ohm(25.0, 0.50)
    r_low = cell_internal_resistance_ohm(25.0, 0.05)
    r_high = cell_internal_resistance_ohm(25.0, 0.95)
    assert r_low > r_mid
    assert r_high > r_mid


def test_ocv_and_entropic_coefficient_plateaus():
    u_low = ocv_pack_voltage_v(0.10)
    u_mid = ocv_pack_voltage_v(0.50)
    u_high = ocv_pack_voltage_v(0.90)
    assert 760.0 < u_low < u_mid < u_high < 835.0
    # Entropic coefficient changes sign between low SoC (<0.20) and upper plateau (0.75)
    du_low = entropic_coeff_v_per_k(0.12)
    du_high = entropic_coeff_v_per_k(0.75)
    assert du_low < 0.0
    assert du_high > 0.0


def test_joule_and_reversible_heat_generation():
    q_j_dis, q_rev_dis, q_tot_dis, r_mohm, i_dis = cell_heat_generation_kw(1000.0, 20.0, 0.70)
    q_j_ch, q_rev_ch, q_tot_ch, _, i_ch = cell_heat_generation_kw(-1000.0, 20.0, 0.70)
    assert q_j_dis > 0.0
    assert q_j_dis == pytest.approx(q_j_ch, rel=1e-9)
    assert i_dis == pytest.approx(-i_ch, rel=1e-9)
    # Reversible entropic heat reverses sign between charge and discharge at identical state
    assert q_rev_dis == pytest.approx(-q_rev_ch, rel=1e-9)
    assert q_tot_dis == pytest.approx(q_j_dis + q_rev_dis, rel=1e-9)
    assert r_mohm > 10.0


def test_chiller_carnot_derated_cop():
    liq = COOLING_SYSTEMS["liquid"]
    air = COOLING_SYSTEMS["air"]
    assert chiller_cop(20.0, liq) == pytest.approx(3.40, abs=1e-4)
    assert chiller_cop(35.0, liq) == pytest.approx(2.10, abs=1e-4)
    assert chiller_cop(20.0, air) < chiller_cop(20.0, liq)
    assert chiller_cop(35.0, air) < chiller_cop(35.0, liq)


def test_pcs_and_transformer_loss_curves():
    pcs_spec = PcsTransformerSpec()
    assert pcs_loss_kw(0.0, pcs_spec) == 0.0
    l_half = pcs_loss_kw(1250.0, pcs_spec)
    l_full = pcs_loss_kw(2500.0, pcs_spec)
    assert 0.0 < l_half < l_full

    fe_on, cu_rated = transformer_losses_kw(2500.0, pcs_spec, include_core_loss=True)
    fe_off, cu_idle = transformer_losses_kw(0.0, pcs_spec, include_core_loss=False)
    assert fe_on == pytest.approx(4.2, abs=1e-6)
    assert cu_rated == pytest.approx(24.0, abs=1e-6)
    assert fe_off == 0.0
    assert cu_idle == 0.0


@pytest.mark.parametrize(
    "duty_code,expected_cpd",
    [("reserve_035", 0.35), ("da_100", 1.0), ("heavy_200", 2.0)],
)
def test_hourly_duty_profiles_charge_neutral(duty_code, expected_cpd):
    df = build_hourly_duty_profile(duty_code)
    assert len(df) == 8760
    assert df["e_stored_start_kwh"].iloc[0] == pytest.approx(2500.0, abs=1e-6)
    assert df["e_stored_end_kwh"].iloc[-1] == pytest.approx(2500.0, abs=1e-6)
    total_dis_mwh = float(np.sum(np.maximum(df["p_chem_kw"], 0.0)) / 1000.0)
    assert total_dis_mwh == pytest.approx(expected_cpd * 5.0 * 365.0, rel=1e-6)
    assert float(df["soc_start"].min()) >= 0.0
    assert float(df["soc_end"].max()) <= 1.0


def test_first_law_electrical_conservation_all_runs(all_sim_runs):
    for (duty_code, cool_code), res in all_sim_runs.items():
        s = res["summary"]
        lhs = s["e_in_pcc_mwh"] - s["e_out_pcc_mwh"] - s["de_stored_mwh"]
        rhs = (
            s["e_joule_mwh"]
            + s["e_entropic_mwh"]
            + s["e_pcs_mwh"]
            + s["e_trafo_fe_mwh"]
            + s["e_trafo_cu_mwh"]
            + s["e_aux_mwh"]
        )
        assert abs(lhs - rhs) < 1e-6, f"Failed electrical balance for {duty_code}/{cool_code}"
        assert s["elec_balance_residual_mwh"] < 1e-6


def test_first_law_thermal_conservation_all_runs(all_sim_runs):
    for (duty_code, cool_code), res in all_sim_runs.items():
        s = res["summary"]
        assert s["thermal_balance_residual_mj"] < 1e-6, f"Failed thermal balance for {duty_code}/{cool_code}"


def test_metering_boundary_rte_waterfall_ordering(all_sim_runs):
    for _, res in all_sim_runs.items():
        s = res["summary"]
        assert 1.0 > s["rte_dc"] > s["rte_pcs_ac"] > s["rte_trafo_mv"] > s["rte_pcc_net"] > 0.65
        assert s["rte_lv_with_aux"] > s["rte_pcc_net"]


def test_key_engineering_findings_da100_and_heavy200(all_sim_runs):
    da_liq = all_sim_runs[("da_100", "liquid")]["summary"]
    da_air = all_sim_runs[("da_100", "air")]["summary"]
    hv_liq = all_sim_runs[("heavy_200", "liquid")]["summary"]

    # DC cell-terminal RTE ~94.2% at 1 cyc/day, dropping to ~91.8% at 2 cyc/day
    assert da_liq["rte_dc"] == pytest.approx(0.942, abs=0.002)
    assert hv_liq["rte_dc"] == pytest.approx(0.918, abs=0.002)
    # PCS AC-terminal RTE ~89.6% at 1 cyc/day
    assert da_liq["rte_pcs_ac"] == pytest.approx(0.896, abs=0.002)
    # Net PCC RTE ~84.1% (Liquid) vs ~82.6% (Air) at 1 cyc/day
    assert da_liq["rte_pcc_net"] == pytest.approx(0.841, abs=0.002)
    assert da_air["rte_pcc_net"] == pytest.approx(0.826, abs=0.002)
    # Seasonal swing: ~79.4% in January, ~82.8% in July, peaking at ~85.7% in May/September
    assert da_liq["rte_jan_pcc"] == pytest.approx(0.794, abs=0.003)
    assert da_liq["rte_jul_pcc"] == pytest.approx(0.828, abs=0.003)
    assert da_liq["rte_may_pcc"] == pytest.approx(0.857, abs=0.003)
    assert da_liq["rte_sep_pcc"] == pytest.approx(0.857, abs=0.003)


def test_low_cycling_reserve_duty_standing_losses(all_sim_runs):
    res_liq = all_sim_runs[("reserve_035", "liquid")]["summary"]
    assert res_liq["e_trafo_fe_mwh"] == pytest.approx(36.8, abs=0.1)
    assert res_liq["e_bms_mwh"] == pytest.approx(28.0, abs=0.1)
    assert res_liq["e_ptc_mwh"] == pytest.approx(24.5, abs=0.6)
    assert res_liq["e_standing_parasitic_mwh"] == pytest.approx(89.3, abs=1.0)
    assert res_liq["rte_pcc_net"] == pytest.approx(0.738, abs=0.003)
    assert res_liq["months_below_85_pct_guarantee"] >= 11


def test_liquid_vs_air_cooling_spread_and_hvac_savings(all_sim_runs):
    da_liq = all_sim_runs[("da_100", "liquid")]["summary"]
    da_air = all_sim_runs[("da_100", "air")]["summary"]
    assert da_liq["max_temp_spread_k"] == pytest.approx(2.4, abs=0.05)
    assert da_air["max_temp_spread_k"] == pytest.approx(7.8, abs=0.05)

    saved_mwh = da_air["e_aux_mwh"] - da_liq["e_aux_mwh"]
    saved_pct = 100.0 * saved_mwh / da_air["e_aux_mwh"]
    assert saved_mwh == pytest.approx(18.5, abs=0.4)
    assert saved_pct == pytest.approx(26.0, abs=1.0)


def test_winter_cell_temperature_floor_maintained(all_sim_runs):
    for (duty_code, cool_code), res in all_sim_runs.items():
        # PTC heater keeps cells above lithium-plating danger zone even when T_amb drops to -12.2 degC
        assert res["summary"]["t_cell_min_c"] >= 11.5, f"Cell temp too cold in {duty_code}/{cool_code}"


def test_piecewise_linear_aux_parameter_recovery(weather_8760, all_sim_runs):
    da_liq_df = all_sim_runs[("da_100", "liquid")]["hourly_df"]
    df_tel, truth = generate_synthetic_telemetry_for_recovery(weather_8760, da_liq_df)
    est = fit_piecewise_linear_aux_model(
        df_tel["t_amb_c"].to_numpy(),
        df_tel["p_pcs_mw"].to_numpy(),
        df_tel["p_aux_meas_kw"].to_numpy(),
    )
    assert est["b0_kw"] == pytest.approx(truth["b0_kw"], abs=0.05)
    assert est["h_kw_per_k"] == pytest.approx(truth["h_kw_per_k"], abs=0.05)
    assert est["t_h_c"] == pytest.approx(truth["t_h_c"], abs=0.5)
    assert est["c_kw_per_k"] == pytest.approx(truth["c_kw_per_k"], abs=0.05)
    assert est["t_c_c"] == pytest.approx(truth["t_c_c"], abs=0.5)
    assert est["k_kw_per_mw"] == pytest.approx(truth["k_kw_per_mw"], abs=0.05)
    assert est["r2"] > 0.999


def test_soc_corrected_window_rte_formula():
    assert compute_window_rte(100.0, 85.0, 0.0) == pytest.approx(0.85, abs=1e-9)
    # Positive dE_stored increases corrected RTE relative to raw E_out / E_in
    assert compute_window_rte(100.0, 80.0, 5.0, eta_oneway=0.92) > 0.80
    # Negative dE_stored decreases corrected RTE relative to raw E_out / E_in
    assert compute_window_rte(100.0, 90.0, -5.0, eta_oneway=0.92) < 0.90


def test_steady_aux_vs_temperature_monotonic_tails():
    t_grid = np.linspace(-15.0, 35.0, 51)
    curves = evaluate_steady_aux_vs_temperature(t_grid)
    liq_05 = curves["liquid"]["c_0.50"]
    air_05 = curves["air"]["c_0.50"]
    # Cold extreme (-15 degC) and hot extreme (+35 degC) both exceed mild 15 degC auxiliary draw
    idx_15c = int(np.argmin(np.abs(t_grid - 15.0)))
    assert liq_05[0] > liq_05[idx_15c]
    assert liq_05[-1] > liq_05[idx_15c]
    assert max(air_05) > max(liq_05)


def test_verification_suite_and_cli_artifacts(tmp_path: Path):
    rc = cli_main(["run", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "results" / "annual_summary.csv").exists()
    assert (tmp_path / "results" / "monthly_rte_losses.csv").exists()
    assert (tmp_path / "results" / "verification.json").exists()
    assert (tmp_path / "docs" / "data.js").exists()
    assert (tmp_path / "docs" / ".nojekyll").exists()
    for png in (
        "monthly_rte_waterfall.png",
        "aux_vs_temperature.png",
        "winter_summer_week.png",
        "loss_breakdown_duty.png",
    ):
        p = tmp_path / "docs" / "img" / png
        assert p.exists() and p.stat().st_size > 15_000
