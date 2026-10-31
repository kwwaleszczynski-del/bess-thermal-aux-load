"""Property and invariant tests for bess-thermal-aux-load (no network, no pinned result values)."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bess_thermal.cli import main as cli_main
from bess_thermal.config import COOLING_SYSTEMS, DUTY_SPECS, ChillerSpec, ContainerSpec, PcsTransformerSpec
from bess_thermal.duties import build_hourly_duty_profile
from bess_thermal.physics import (
    cell_entropic_coeff_v_per_k,
    cell_heat_generation_kw,
    cell_ocv_v,
    chiller_cop,
    dc_rte_constant_power,
    pack_ocv_v,
    pack_resistance_ohm,
    pcs_efficiency,
    pcs_loss_kw,
    transformer_losses_kw,
)
from bess_thermal.simulation import (
    compute_window_rte,
    run_container,
    simulate_container_year,
    steady_aux_vs_temperature,
)
from bess_thermal.verification import (
    check_idle_heater_steady_state,
    check_integrator_against_exact,
    check_timestep_convergence,
    fit_piecewise_linear_aux_model,
    generate_synthetic_telemetry_for_recovery,
)
from bess_thermal.weather import load_weather

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def weather():
    return load_weather()


@pytest.fixture(scope="module")
def runs(weather):
    return {(d, c): simulate_container_year(weather, d, c) for d in DUTY_SPECS for c in COOLING_SYSTEMS}


def test_weather_file_is_a_complete_hourly_year(weather):
    assert len(weather) == 8760
    steps = weather["timestamp"].diff().dropna().unique()
    assert len(steps) == 1 and steps[0] == pd.Timedelta(hours=1)
    assert weather[["t_amb_c", "ghi_wm2"]].notna().all().all()
    assert set(weather["month"]) == set(range(1, 13))
    assert weather["t_amb_c"].between(-35.0, 42.0).all()
    assert (weather["ghi_wm2"] >= 0.0).all()
    # No sun at midnight CET, and summer is warmer and brighter than winter
    assert (weather.loc[weather["hour"] == 0, "ghi_wm2"] == 0.0).all()
    by_month = weather.groupby("month")[["t_amb_c", "ghi_wm2"]].mean()
    assert by_month.loc[7, "t_amb_c"] > by_month.loc[1, "t_amb_c"] + 10.0
    assert by_month.loc[6, "ghi_wm2"] > 4.0 * by_month.loc[12, "ghi_wm2"]


def test_resistance_arrhenius_and_soc_shape():
    assert pack_resistance_ohm(5.0) / pack_resistance_ohm(25.0) == pytest.approx(2.0, rel=1e-5)
    t = np.linspace(-10.0, 45.0, 56)
    assert np.all(np.diff(pack_resistance_ohm(t)) < 0.0)
    assert pack_resistance_ohm(25.0, 0.05) > pack_resistance_ohm(25.0, 0.5) < pack_resistance_ohm(25.0, 0.95)
    spec = ContainerSpec()
    expected = spec.cell_r25_mohm / 1000.0 * spec.n_series / spec.n_parallel
    assert pack_resistance_ohm(25.0, 0.5) == pytest.approx(expected, rel=1e-12)


def test_ocv_monotonic_and_entropic_sign_change():
    soc = np.linspace(0.01, 0.99, 197)
    assert np.all(np.diff(cell_ocv_v(soc)) > 0.0)
    assert 3.0 < cell_ocv_v(0.05) < cell_ocv_v(0.5) < cell_ocv_v(0.95) < 3.4
    assert pack_ocv_v(0.5) == pytest.approx(ContainerSpec().n_series * cell_ocv_v(0.5))
    du = cell_entropic_coeff_v_per_k(soc)
    assert du.min() < 0.0 < du.max()
    assert np.abs(du).max() < 0.5e-3


def test_heat_generation_bernardi_signs():
    soc = 0.75
    assert cell_entropic_coeff_v_per_k(soc) > 0.0
    q_irr_d, q_rev_d, i_d, _ = cell_heat_generation_kw(1000.0, 20.0, soc)
    q_irr_c, q_rev_c, i_c, _ = cell_heat_generation_kw(-1000.0, 20.0, soc)
    assert i_d == pytest.approx(-i_c)
    # Irreversible heat is direction-independent; reversible heat is endothermic on discharge when dU/dT > 0
    assert q_irr_d == pytest.approx(q_irr_c) and q_irr_d > 0.0
    assert q_rev_d < 0.0 < q_rev_c
    assert q_rev_d == pytest.approx(-q_rev_c)
    # More power and colder cells mean more irreversible heat
    assert cell_heat_generation_kw(2000.0, 20.0, soc)[0] > q_irr_d
    assert cell_heat_generation_kw(1000.0, 5.0, soc)[0] > q_irr_d
    assert cell_heat_generation_kw(0.0, 20.0, soc)[:2] == (0.0, 0.0)


def test_dc_rte_falls_with_c_rate_and_cold():
    rates = [0.05, 0.1, 0.25, 0.5, 1.0]
    vals = [dc_rte_constant_power(c) for c in rates]
    assert all(0.0 < v < 1.0 for v in vals)
    assert all(a > b for a, b in zip(vals, vals[1:]))
    assert dc_rte_constant_power(0.25, 10.0) < dc_rte_constant_power(0.25, 25.0) < dc_rte_constant_power(0.25, 40.0)


def test_chiller_cop_physics():
    chiller = ChillerSpec()
    for cooling in COOLING_SYSTEMS.values():
        t_amb = np.linspace(-20.0, 40.0, 61)
        cop = chiller_cop(20.0, t_amb, cooling, chiller)
        assert np.all(cop > 0.0) and np.all(cop <= chiller.cop_max)
        assert np.all(np.diff(cop) <= 1e-12)
        assert chiller_cop(20.0, 35.0, cooling, chiller) < chiller.cop_max
        assert chiller_cop(25.0, 35.0, cooling, chiller) > chiller_cop(15.0, 35.0, cooling, chiller)
    # Same cold-side temperature: the larger evaporator approach of the air unit costs COP
    assert chiller_cop(20.0, 35.0, COOLING_SYSTEMS["air"]) < chiller_cop(20.0, 35.0, COOLING_SYSTEMS["liquid"])


def test_pcs_and_transformer_loss_curves():
    spec = PcsTransformerSpec()
    assert pcs_loss_kw(0.0, spec) == spec.pcs_standby_kw
    p = np.linspace(100.0, 2500.0, 25)
    assert np.all(np.diff(pcs_loss_kw(p, spec)) > 0.0)
    assert all(0.95 < pcs_efficiency(x, spec) < 1.0 for x in (0.1, 0.25, 0.5, 1.0))
    p0_a, pk_a = transformer_losses_kw(1250.0, spec)
    p0_b, pk_b = transformer_losses_kw(2500.0, spec)
    assert p0_a == p0_b == spec.trafo_p0_kw
    assert pk_b == pytest.approx(4.0 * pk_a) and pk_b == pytest.approx(spec.trafo_pk_kw)


@pytest.mark.parametrize("duty_code", list(DUTY_SPECS))
def test_duty_profiles_charge_neutral(duty_code):
    df = build_hourly_duty_profile(duty_code)
    cap = ContainerSpec().rated_energy_kwh
    assert len(df) == 8760
    daily = df["e_stored_end_kwh"].to_numpy()[23::24]
    assert np.allclose(daily, 0.5 * cap)
    dis_mwh = float(np.sum(np.maximum(df["p_chem_kw"], 0.0)) / 1000.0)
    assert dis_mwh == pytest.approx(DUTY_SPECS[duty_code].cycles_per_day * cap / 1000.0 * 365.0)
    assert df["soc_start"].min() >= 0.0 and df["soc_end"].max() <= 1.0


def test_plant_energy_balance_closes(runs):
    for key, res in runs.items():
        b = res["energy_balance"]
        assert b["residual_mwh"] < 1e-6, key
        assert b["net_import_pcc_mwh"] > 0.0


def test_rte_ordering_across_boundaries(runs):
    for key, res in runs.items():
        s = res["summary"]
        assert 1.0 > s["rte_dc"] > s["rte_pcs_ac"] > s["rte_trafo_mv"] > s["rte_pcc_net"] > 0.0, key
        assert s["rte_pcs_ac"] > s["rte_lv_net"] > s["rte_pcc_net"], key
        m = res["monthly_df"]
        assert s["rte_pcc_month_min"] <= s["rte_pcc_net"] <= s["rte_pcc_month_max"]
        assert m["rte_pcc_net"].min() == s["rte_pcc_month_min"]


def test_aux_components_and_conversion_factors(runs):
    c, h = ContainerSpec(), ChillerSpec()
    for key, res in runs.items():
        s = res["summary"]
        parts = s["e_bms_mwh"] + s["e_circ_mwh"] + s["e_heater_mwh"] + s["e_chiller_mwh"]
        assert s["e_aux_mwh"] == pytest.approx(parts, abs=1e-6), key
        assert s["e_bms_mwh"] == pytest.approx(c.bms_base_load_kw * 8.76, rel=1e-9)
        assert s["e_heater_mwh"] == pytest.approx(s["e_heat_th_mwh"] / c.heater_efficiency, abs=1e-9)
        if s["e_chiller_mwh"] > 0.0:
            assert s["e_cool_th_mwh"] <= h.cop_max * s["e_chiller_mwh"] + 1e-9
            assert 0.0 < s["cop_mean"] <= h.cop_max


def test_inlined_loop_physics_matches_physics_module():
    """Sub-step heat and COP computed inside the solver loop must equal the reference functions in physics.py."""
    spec = ContainerSpec()
    duty = build_hourly_duty_profile("heavy_200", n_hours=48)
    for cool_code, cooling in COOLING_SYSTEMS.items():
        res = run_container(
            np.full(48, 30.0), np.full(48, 300.0), duty["p_chem_kw"].to_numpy(), duty["soc_start"].to_numpy(),
            duty["soc_end"].to_numpy(), cooling, spec, t_init_c=(20.0, 20.0), record_hours=np.ones(48, dtype=bool),
        )
        t_start = 20.0
        n_cop = 0
        for r in res["records"]:
            q_irr, q_rev, _, r_mohm = cell_heat_generation_kw(r["p_chem_kw"], t_start, r["soc"], spec)
            assert r["q_irr_kw"] == pytest.approx(q_irr, rel=1e-9, abs=1e-12)
            assert r["q_rev_kw"] == pytest.approx(q_rev, rel=1e-9, abs=1e-12)
            assert r["r_int_mohm"] == pytest.approx(r_mohm, rel=1e-9)
            if r["q_cool_kw"] > 0.0:
                n_cop += 1
                assert r["cop"] == pytest.approx(chiller_cop(r["t_enc_c"], r["t_amb_c"], cooling), rel=1e-9)
                assert r["p_chiller_kw"] == pytest.approx(r["q_cool_kw"] / r["cop"], rel=1e-9)
            t_start = r["t_cell_c"]
        assert n_cop > 0


def test_cell_temperature_stays_in_control_band(runs):
    c = ContainerSpec()
    for (d, cool), res in runs.items():
        s = res["summary"]
        assert s["t_cell_min_c"] >= c.t_heat_set_c - 1e-6, (d, cool)
        if d == "rated_100":
            continue  # cell heat at 0.5 C exceeds what the cooling can remove (rated C-rate test below)
        tol = 1e-6 if cool == "liquid" else 1.0
        assert s["t_cell_max_c"] <= c.t_cool_set_c + tol, (d, cool)


def test_more_cycling_more_cell_loss_lower_dc_rte(runs):
    duties = ("reserve_035", "da_100", "heavy_200")
    for cool in COOLING_SYSTEMS:
        loss = [runs[(d, cool)]["summary"]["e_cell_loss_mwh"] for d in duties]
        dc = [runs[(d, cool)]["summary"]["rte_dc"] for d in duties]
        assert loss[0] < loss[1] < loss[2]
        assert dc[0] > dc[1] > dc[2]


def test_rated_c_rate_costs_efficiency_at_same_throughput(runs):
    """1 cycle/day at the rated 0.5 C against the same daily throughput at a lower peak C-rate."""
    c = ContainerSpec()
    for cool, cooling in COOLING_SYSTEMS.items():
        da, rated = runs[("da_100", cool)]["summary"], runs[("rated_100", cool)]["summary"]
        assert rated["cycles_per_day"] == da["cycles_per_day"]
        assert rated["max_c_rate"] == pytest.approx(c.rated_power_kw / c.rated_energy_kwh)
        assert da["max_c_rate"] < rated["max_c_rate"]
        assert rated["e_cell_loss_mwh"] > da["e_cell_loss_mwh"] and rated["rte_dc"] < da["rte_dc"]
        assert rated["e_trafo_pk_mwh"] > da["e_trafo_pk_mwh"]
        assert rated["rte_pcc_net"] < da["rte_pcc_net"]
        # Cell heat at 0.5 C above the cooling capacity: the cooling set point cannot be held
        if cell_heat_generation_kw(c.rated_power_kw, c.t_cool_set_c, 0.5, c)[0] > cooling.cooling_capacity_kw:
            assert rated["t_cell_max_c"] > c.t_cool_set_c, cool


def test_air_cooling_needs_more_aux_than_liquid(runs):
    for d in DUTY_SPECS:
        liq, air = runs[(d, "liquid")]["summary"], runs[(d, "air")]["summary"]
        assert air["e_aux_mwh"] > liq["e_aux_mwh"]
        if liq["cop_mean"] and air["cop_mean"]:
            assert air["cop_mean"] <= liq["cop_mean"] + 1e-9


def test_steady_aux_responds_to_temperature_and_load():
    t = np.array([-15.0, 5.0, 15.0, 35.0])
    out = steady_aux_vs_temperature(t, c_rates=(0.0, 0.25), days=4, avg_days=1)
    for cool in COOLING_SYSTEMS:
        idle, cyc = out[cool]["c_0.00"], out[cool]["c_0.25"]
        assert idle[3] > idle[2]
        assert idle[1] == pytest.approx(ContainerSpec().bms_base_load_kw, abs=1e-6)
        assert all(cv >= iv - 1e-9 for cv, iv in zip(cyc, idle))
        assert cyc[3] > cyc[0]


def test_integrator_matches_matrix_exponential():
    for cool in COOLING_SYSTEMS:
        r = check_integrator_against_exact(cooling_code=cool)
        assert r["pass_0p1_k"], r


def test_idle_heater_matches_closed_form():
    for cool in COOLING_SYSTEMS:
        r = check_idle_heater_steady_state(cooling_code=cool)
        assert r["heater_closed_form_kw"] > 0.0
        assert r["pass"], r


def test_timestep_convergence(weather):
    r = check_timestep_convergence(weather, cooling_code="air")
    assert r["pass"], r


def test_estimator_recovers_known_parameters(weather, runs):
    df_tel, truth = generate_synthetic_telemetry_for_recovery(weather, runs[("da_100", "liquid")]["hourly_df"])
    est = fit_piecewise_linear_aux_model(df_tel["t_amb_c"], df_tel["p_pcs_mw"], df_tel["p_aux_meas_kw"])
    for k in ("b0_kw", "h_kw_per_k", "c_kw_per_k", "k_kw_per_mw"):
        assert est[k] == pytest.approx(truth[k], abs=0.05), k
    for k in ("t_h_c", "t_c_c"):
        assert est[k] == pytest.approx(truth[k], abs=0.5), k


def test_soc_corrected_window_rte_formula():
    assert compute_window_rte(100.0, 85.0, 0.0) == pytest.approx(0.85, abs=1e-9)
    assert compute_window_rte(100.0, 80.0, 5.0, eta_oneway=0.92) > 0.80
    assert compute_window_rte(100.0, 90.0, -5.0, eta_oneway=0.92) < 0.90


def test_dashboard_has_no_hard_coded_results():
    html = (REPO / "docs" / "index.html").read_text(encoding="utf-8")
    static = re.sub(r"<script>.*?</script>", "", html, flags=re.DOTALL)
    assert not re.search(r"\d+\.\d+\s*%", static), "percentages must come from data.js"
    assert not re.search(r"\d+\.\d+\s*MWh", static), "energies must come from data.js"


def test_cli_run_reproduces_committed_results(tmp_path: Path):
    assert cli_main(["run", "--out", str(tmp_path)]) == 0
    for name in ("annual_summary.csv", "monthly_rte_losses.csv", "sensitivity.csv", "verification.json"):
        assert (tmp_path / "results" / name).exists()
    for png in (
        "monthly_rte_waterfall.png",
        "aux_vs_temperature.png",
        "winter_summer_week.png",
        "loss_breakdown_duty.png",
        "sensitivity_tornado.png",
    ):
        p = tmp_path / "docs" / "img" / png
        assert p.exists() and p.stat().st_size > 15_000
    assert (tmp_path / "docs" / "data.js").exists()

    # Committed results, README tables and dashboard data must be what the code produces
    for name in ("annual_summary.csv", "monthly_rte_losses.csv", "sensitivity.csv"):
        new = pd.read_csv(tmp_path / "results" / name)
        old = pd.read_csv(REPO / "results" / name)
        assert list(new.columns) == list(old.columns), name
        num = new.select_dtypes("number").columns
        assert np.allclose(new[num].to_numpy(float), old[num].to_numpy(float), rtol=1e-6, atol=1e-6, equal_nan=True), name
        assert new.drop(columns=num).equals(old.drop(columns=num)), name
    tables = (tmp_path / "results" / "readme_tables.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for block in tables.strip().split("\n\n"):
        assert block in readme
