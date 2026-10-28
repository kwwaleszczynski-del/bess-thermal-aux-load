"""Physical specifications for the 2.5 MW / 5.0 MWh LFP container, cooling systems, PCS/transformer, and cycling duties."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerSpec:
    """20-foot 2.5 MW / 5.0 MWh LFP battery container physical parameters."""

    name: str = "20-ft LFP 2.5 MW / 5.0 MWh Container"
    latitude_deg: float = 52.2
    longitude_deg: float = 21.0
    rated_power_kw: float = 2500.0
    rated_energy_kwh: float = 5000.0
    nominal_voltage_v: float = 800.0
    # Thermal capacitances (MJ/K)
    c_cell_mj_per_k: float = 4.8
    c_enc_mj_per_k: float = 0.9
    # Solar geometry on 20-ft ISO container roof/south wall effective area
    roof_area_m2: float = 14.8
    solar_absorptance: float = 0.35
    # Cell + rack internal DC resistance at 25 degC and Arrhenius activation energy
    # Chosen so R_int doubles between 25 degC (298.15 K) and 5 degC (278.15 K):
    # exp(ea_over_r_k * (1/278.15 - 1/298.15)) == 2.0
    r25_base_ohm: float = 0.0196
    ea_over_r_k: float = 2874.153825
    # Thermal management thresholds
    t_min_charge_c: float = 12.0
    t_cool_on_c: float = 24.0
    t_cool_target_c: float = 23.0
    # Continuous BMS, EMS, fire suppression, gas detection, switchgear controls
    bms_base_load_kw: float = 3.2


@dataclass(frozen=True)
class CoolingSystemSpec:
    """Parameters distinguishing Liquid-Cooled (cold plates) vs Forced-Air (rooftop DX) containers."""

    code: str
    label: str
    short_label: str
    ua_wall_w_per_k: float
    ua_int_active_kw_per_k: float
    ua_int_idle_kw_per_k: float
    cop_20c: float
    cop_35c: float
    cop_min: float
    cop_max: float
    pump_or_fan_kw: float
    ptc_cop: float
    spread_base_k: float
    spread_k_per_kw_joule: float


COOLING_SYSTEMS: dict[str, CoolingSystemSpec] = {
    "liquid": CoolingSystemSpec(
        code="liquid",
        label="Liquid-Cooled Container (cold plates + glycol chiller + PTC heater)",
        short_label="Liquid Cooling (cold plates)",
        ua_wall_w_per_k=115.0,
        ua_int_active_kw_per_k=18.5,
        ua_int_idle_kw_per_k=3.2,
        cop_20c=3.40,
        cop_35c=2.10,
        cop_min=1.75,
        cop_max=4.30,
        pump_or_fan_kw=1.8,
        ptc_cop=0.98,
        spread_base_k=0.65,
        spread_k_per_kw_joule=0.0145,
    ),
    "air": CoolingSystemSpec(
        code="air",
        label="Forced-Air Container (rooftop HVAC DX unit)",
        short_label="Forced Air HVAC",
        ua_wall_w_per_k=195.0,
        ua_int_active_kw_per_k=5.4,
        ua_int_idle_kw_per_k=1.4,
        cop_20c=2.55,
        cop_35c=1.50,
        cop_min=1.30,
        cop_max=3.40,
        pump_or_fan_kw=2.9,
        ptc_cop=0.98,
        spread_base_k=1.85,
        spread_k_per_kw_joule=0.0485,
    ),
}


@dataclass(frozen=True)
class PcsTransformerSpec:
    """Bidirectional PCS inverter and 0.8/15 kV MV step-up transformer loss model."""

    # PCS loss curve: P_loss,pcs = p0_active + k1 * |P| + k2 * P^2 when active, p0_standby when idle
    pcs_p0_active_kw: float = 7.4
    pcs_p0_standby_kw: float = 0.0
    pcs_k1: float = 0.0155
    pcs_k2_per_kw: float = 5.2e-6
    # 0.8 / 15 kV MV transformer (2.5 MVA)
    trafo_rated_kva: float = 2500.0
    trafo_p_fe_kw: float = 4.2
    trafo_p_cu_rated_kw: float = 24.0


@dataclass(frozen=True)
class DutySpec:
    """Operating cycling regime specification."""

    code: str
    label: str
    short_label: str
    cycles_per_day: float
    cycles_per_year: float
    c_rate_nominal: float


DUTY_SPECS: dict[str, DutySpec] = {
    "reserve_035": DutySpec(
        code="reserve_035",
        label="Low-cycling reserve / capacity market duty (0.35 cycles/day, 128 cycles/yr)",
        short_label="0.35 cycles/day (Reserve)",
        cycles_per_day=0.35,
        cycles_per_year=127.75,
        c_rate_nominal=0.25,
    ),
    "da_100": DutySpec(
        code="da_100",
        label="Day-ahead 1-cycle arbitrage (1.0 cycle/day, 365 cycles/yr)",
        short_label="1.0 cycle/day (DA Arbitrage)",
        cycles_per_day=1.0,
        cycles_per_year=365.0,
        c_rate_nominal=0.38,
    ),
    "heavy_200": DutySpec(
        code="heavy_200",
        label="Heavy 2-cycle arbitrage + intraday (2.0 cycles/day, 730 cycles/yr)",
        short_label="2.0 cycles/day (Heavy)",
        cycles_per_day=2.0,
        cycles_per_year=730.0,
        c_rate_nominal=0.50,
    ),
}
