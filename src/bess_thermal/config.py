"""Physical specifications for the 2.5 MW / 5.0 MWh LFP container, cooling systems, PCS/transformer, and cycling duties.

Every number here is a stated input. Values marked "assumption" are engineering estimates for a generic
20-ft LFP container, not data from a specific product; the README lists them with their basis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerSpec:
    """20-foot 2.5 MW / 5.0 MWh LFP battery container (generic, assumption-based)."""

    name: str = "20-ft LFP 2.5 MW / 5.0 MWh container"
    latitude_deg: float = 52.2
    longitude_deg: float = 21.0
    rated_power_kw: float = 2500.0
    rated_energy_kwh: float = 5000.0
    # Pack: 416 cells in series x 12 strings of 314 Ah LFP cells (5.02 MWh nominal at 3.2 V, modelled as 5.0 MWh)
    n_series: int = 416
    n_parallel: int = 12
    # Effective cell DC resistance at 25 degC, 50 % SoC (ohmic + polarisation on a 15-min scale + interconnects)
    cell_r25_mohm: float = 0.50
    # Arrhenius activation temperature E_a/R: resistance doubles between 25 degC and 5 degC
    ea_over_r_k: float = 2874.153825
    # Half of the LFP charge/discharge open-circuit voltage gap, applied as a rate-independent overpotential
    cell_hysteresis_mv: float = 10.0
    # Cell thermal mass from gravimetric energy density and specific heat
    cell_specific_energy_wh_per_kg: float = 180.0
    cell_cp_j_per_kg_k: float = 1050.0
    # Racks, coolant or air, and steel shell lumped into the enclosure node
    c_enc_mj_per_k: float = 4.0
    # 20-ft high-cube external dimensions (m) and effective envelope U-value incl. thermal bridges
    length_m: float = 6.058
    width_m: float = 2.438
    height_m: float = 2.896
    u_envelope_w_per_m2_k: float = 1.0
    # Roof sol-air gain: light-coloured roof absorptance and outside surface heat-transfer coefficient
    solar_absorptance: float = 0.35
    h_out_w_per_m2_k: float = 20.0
    # Cell temperature control band: heat below t_heat_set_c, cool above t_cool_set_c
    t_heat_set_c: float = 12.0
    t_cool_set_c: float = 25.0
    # Electric heater in the coolant loop / air stream
    heater_capacity_kw: float = 15.0
    heater_efficiency: float = 1.0
    t_heater_supply_max_c: float = 35.0
    # Continuous BMS, EMS, fire and gas detection, controls (dissipated inside the container)
    bms_base_load_kw: float = 1.0

    @property
    def cell_mass_kg(self) -> float:
        return self.rated_energy_kwh * 1000.0 / self.cell_specific_energy_wh_per_kg

    @property
    def c_cell_mj_per_k(self) -> float:
        return self.cell_mass_kg * self.cell_cp_j_per_kg_k / 1e6

    @property
    def envelope_area_m2(self) -> float:
        lw, lh, wh = self.length_m * self.width_m, self.length_m * self.height_m, self.width_m * self.height_m
        return 2.0 * (lw + lh + wh)

    @property
    def roof_area_m2(self) -> float:
        return self.length_m * self.width_m

    @property
    def ua_envelope_kw_per_k(self) -> float:
        return self.u_envelope_w_per_m2_k * self.envelope_area_m2 / 1000.0

    @property
    def ua_roof_kw_per_k(self) -> float:
        return self.u_envelope_w_per_m2_k * self.roof_area_m2 / 1000.0


@dataclass(frozen=True)
class CoolingSystemSpec:
    """Thermal management architecture: liquid cold plates + chiller, or forced air + DX unit."""

    code: str
    label: str
    short_label: str
    # Conductance between the cell node and the coolant/air node
    ua_int_kw_per_k: float
    # Evaporating temperature below the coolant/air node temperature
    evap_approach_k: float
    # Lowest coolant/air supply temperature the controller allows (condensation, cell gradients)
    t_supply_min_c: float
    cooling_capacity_kw: float
    # Coolant pump or evaporator fans, running while the battery cycles or the HVAC heats/cools
    circulation_kw: float


COOLING_SYSTEMS: dict[str, CoolingSystemSpec] = {
    "liquid": CoolingSystemSpec(
        code="liquid",
        label="Liquid-cooled container (cold plates, glycol chiller, electric heater)",
        short_label="Liquid (cold plates)",
        ua_int_kw_per_k=12.0,
        evap_approach_k=6.0,
        t_supply_min_c=15.0,
        cooling_capacity_kw=60.0,
        circulation_kw=1.5,
    ),
    "air": CoolingSystemSpec(
        code="air",
        label="Forced-air container (DX air conditioner, electric heater)",
        short_label="Forced air (DX)",
        ua_int_kw_per_k=2.0,
        evap_approach_k=10.0,
        t_supply_min_c=12.0,
        cooling_capacity_kw=50.0,
        circulation_kw=2.0,
    ),
}


@dataclass(frozen=True)
class ChillerSpec:
    """Vapour-compression COP model shared by both architectures."""

    # Fraction of the Carnot COP reached by a packaged chiller / DX unit
    carnot_fraction: float = 0.38
    # Condensing temperature above ambient
    cond_approach_k: float = 12.0
    # Upper COP limit at low lift (fans, controls, minimum pressure ratio)
    cop_max: float = 6.0


@dataclass(frozen=True)
class PcsTransformerSpec:
    """Bidirectional PCS loss curve and 0.8/15 kV MV step-up transformer losses."""

    # PCS loss P_loss = p0 + k1 |P| + k2 P^2 while exchanging power; sleep mode when idle
    pcs_rated_kw: float = 2500.0
    pcs_p0_kw: float = 5.0
    pcs_k1: float = 0.008
    pcs_k2_per_kw: float = 2.4e-6
    pcs_standby_kw: float = 0.0
    # 2.5 MVA liquid-immersed transformer at the EU Ecodesign Tier 2 loss limits (Reg. (EU) 548/2014, Annex I)
    trafo_rated_kva: float = 2500.0
    trafo_p0_kw: float = 1.575
    trafo_pk_kw: float = 18.5


@dataclass(frozen=True)
class DutySpec:
    """Operating cycling regime specification."""

    code: str
    label: str
    short_label: str
    cycles_per_day: float

    @property
    def cycles_per_year(self) -> float:
        return self.cycles_per_day * 365.0


DUTY_SPECS: dict[str, DutySpec] = {
    "reserve_035": DutySpec(
        code="reserve_035",
        label="Low-cycling reserve-style duty (0.35 cycles/day)",
        short_label="0.35 cycles/day (reserve)",
        cycles_per_day=0.35,
    ),
    "da_100": DutySpec(
        code="da_100",
        label="Day-ahead arbitrage (1.0 cycle/day)",
        short_label="1.0 cycle/day (day-ahead)",
        cycles_per_day=1.0,
    ),
    "heavy_200": DutySpec(
        code="heavy_200",
        label="Two-cycle arbitrage (2.0 cycles/day)",
        short_label="2.0 cycles/day (two-cycle)",
        cycles_per_day=2.0,
    ),
}
