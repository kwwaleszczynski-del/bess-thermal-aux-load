| Duty | Cooling | DC | PCS AC | MV, excl. aux | LV net | **PCC net** | PCC net, lowest / highest month |
|---|---|---|---|---|---|---|---|
| 0.35 cycles/day (reserve) | Liquid (cold plates) | 98.5 % | 94.6 % | 92.4 % | 92.0 % | **89.8 %** | 89.0 % (Jul) / 90.4 % (Feb) |
| 0.35 cycles/day (reserve) | Forced air (DX) | 98.5 % | 94.6 % | 92.4 % | 91.8 % | **89.7 %** | 89.0 % (Jul) / 90.2 % (Feb) |
| 1.0 cycle/day (day-ahead) | Liquid (cold plates) | 97.9 % | 94.7 % | 93.6 % | 93.5 % | **92.4 %** | 92.2 % (Jul) / 92.5 % (Feb) |
| 1.0 cycle/day (day-ahead) | Forced air (DX) | 97.9 % | 94.7 % | 93.6 % | 93.3 % | **92.2 %** | 92.0 % (Jul) / 92.3 % (Feb) |
| 2.0 cycles/day (two-cycle) | Liquid (cold plates) | 97.3 % | 94.4 % | 93.4 % | 93.4 % | **92.5 %** | 92.4 % (Jul) / 92.5 % (Feb) |
| 2.0 cycles/day (two-cycle) | Forced air (DX) | 97.3 % | 94.4 % | 93.4 % | 93.2 % | **92.3 %** | 92.1 % (Jul) / 92.4 % (Feb) |

| Duty | Cooling | PCC import [MWh/yr] | Aux total [MWh/yr] | Base load | Pump / fans | Chiller | Heater | Chiller COP (annual) | Cell temp. min-max [degC] |
|---|---|---|---|---|---|---|---|---|---|
| 0.35 cycles/day (reserve) | Liquid (cold plates) | 683 | 18.4 | 8.8 | 7.0 | 2.6 | 0.00 | 5.93 | 23.3 - 25.0 |
| 0.35 cycles/day (reserve) | Forced air (DX) | 684 | 19.3 | 8.8 | 7.7 | 2.9 | 0.00 | 5.63 | 23.2 - 25.0 |
| 1.0 cycle/day (day-ahead) | Liquid (cold plates) | 1907 | 24.6 | 8.8 | 8.1 | 7.7 | 0.00 | 5.94 | 24.0 - 25.0 |
| 1.0 cycle/day (day-ahead) | Forced air (DX) | 1909 | 27.5 | 8.8 | 9.6 | 9.2 | 0.00 | 5.33 | 23.8 - 25.0 |
| 2.0 cycles/day (two-cycle) | Liquid (cold plates) | 3800 | 38.0 | 8.8 | 11.0 | 18.3 | 0.00 | 5.95 | 24.5 - 25.0 |
| 2.0 cycles/day (two-cycle) | Forced air (DX) | 3806 | 46.5 | 8.8 | 14.6 | 23.2 | 0.00 | 5.01 | 23.8 - 25.3 |

| Check | Criterion | Result |
|---|---|---|
| Plant energy balance, 6 runs (bookkeeping) | residual < 1e-6 MWh | max 5.8e-13 MWh |
| Implicit Euler vs matrix exponential, 10 days free-floating (liquid) | cell node error < 0.1 K | cell 0.004 K, enclosure 0.031 K |
| Implicit Euler vs matrix exponential, 10 days free-floating (air) | cell node error < 0.1 K | cell 0.071 K, enclosure 0.532 K |
| Sub-step 15 vs 5 min, 1.0 cyc/d (liquid) | < 0.05 pp RTE, < 1 % aux | 0.000 pp, 0.27 % |
| Sub-step 15 vs 5 min, 1.0 cyc/d (air) | < 0.05 pp RTE, < 1 % aux | 0.000 pp, 0.01 % |
| Idle heater at -30 degC vs closed form (liquid) | relative error < 1e-3 | 0.807 vs 0.807 kW |
| Idle heater at -30 degC vs closed form (air) | relative error < 1e-3 | 0.307 vs 0.307 kW |

| Implied by the inputs (not fitted) | Value |
|---|---|
| DC round-trip efficiency at 0.5 C / 0.2 C, 25 degC | 94.6 % / 97.5 % |
| DC round-trip efficiency at 0.2 C, 12 degC | 96.4 % |
| PCS efficiency at 10 / 25 / 50 / 100 % load | 97.1 % / 98.2 % / 98.5 % / 98.4 % |
| Transformer efficiency at 25 / 50 / 100 % load | 99.6 % / 99.5 % / 99.2 % |
| Chiller COP at 35 degC ambient: liquid (coolant 18 degC) / air (air 20 degC) | 3.10 / 2.91 |
| Thermal time constant of the idle container (C_total / UA_env) | 117 h |

| Input | Value | Basis |
|---|---|---|
| Rated power / energy | 2.5 MW / 5 MWh | generic 20-ft LFP container |
| Pack layout | 416s x 12p, 314 Ah LFP cells | assumption (common 1,331 V nominal string) |
| Effective cell DC resistance at 25 degC | 0.5 mOhm | assumption: ohmic + polarisation; implied DC RTE in the checks table |
| Resistance vs temperature | doubles from 25 to 5 degC | Arrhenius, E_a/R = 2874 K (assumption) |
| Cell voltage hysteresis (half gap) | 10 mV | assumption, LFP charge/discharge OCV gap; to be verified per cell |
| Entropic coefficient | order 0.1 mV/K, sign changes with SoC | assumed shape, order of published LFP values; to be verified |
| Cell thermal mass | 29.2 MJ/K | 180 Wh/kg, c_p 1050 J/(kg K) (assumption) |
| Enclosure node thermal mass | 4 MJ/K | racks, coolant or air, steel shell (assumption) |
| Envelope UA | 79 W/K | 78.7 m2 (20-ft HC) x U 1 W/(m2 K) (assumption) |
| Roof solar gain | sol-air on the roof only | absorptance 0.35, h_out 20 W/(m2 K) (assumption) |
| Cell temperature band | 12 to 25 degC | heat below, cool above (assumption) |
| Heater | 15 kW, efficiency 1 | resistive, inside the envelope |
| BMS / controls base load | 1 kW, 24/7 | assumption; dissipated inside the container |
| Cell-to-coolant / cell-to-air UA | 12 / 2 kW/K | assumption (~2.4 / ~0.4 W/K per cell) |
| Evaporator approach (liquid / air) | 6 / 10 K | assumption |
| Minimum supply temperature (liquid / air) | 15 / 12 degC | assumption |
| Cooling capacity (liquid / air) | 60 / 50 kW | assumption |
| Pump / fan power while running (liquid / air) | 1.5 / 2 kW | assumption; runs while cycling or heating/cooling |
| Chiller COP model | 0.38 x Carnot, condensing at T_amb + 12 K, max 6 | assumption; no free-cooling economiser |
| PCS loss | 5 kW + 0.008 |P| + 2.4e-06 P^2 (kW) | assumption, typical central-inverter curve; sleep when idle |
| MV transformer 2.5 MVA losses | no-load 1.575 kW, load 18.5 kW | EU Ecodesign Tier 2 maximum (Reg. (EU) 548/2014, Annex I, Table I.1); to be verified |

| Input (low / base / high) | 0.35 cyc/d: net PCC RTE low / high | 1.0 cyc/d: net PCC RTE low / high |
|---|---|---|
| Cell DC resistance at 25 degC [mOhm] (0.3 / 0.5 / 1) | 90.2 % / 89.0 % | 93.0 % / 90.8 % |
| PCS no-load loss [kW] (2.5 / 5 / 10) | 90.9 % / 87.8 % | 93.0 % / 91.2 % |
| BMS/controls base load [kW] (0.5 / 1 / 3) | 90.8 % / 86.4 % | 92.7 % / 91.1 % |
| Cell voltage hysteresis [mV] (0 / 10 / 20) | 90.5 % / 89.2 % | 93.0 % / 91.7 % |
| Transformer no-load loss [kW] (0.8 / 1.575 / 3.2) | 90.8 % / 88.0 % | 92.7 % / 91.7 % |
| Circulation power [kW] (0.75 / 1.5 / 3) | 90.4 % / 88.7 % | 92.6 % / 91.9 % |
| Cooling set point [degC] (22 / 25 / 30) | 89.6 % / 90.2 % | 92.2 % / 92.7 % |
| Envelope U-value [W/m2K] (0.5 / 1 / 3) | 89.2 % / 90.1 % | 92.3 % / 92.5 % |
| Ambient temperature shift [K] (-5 / 0 / 5) | 90.1 % / 89.5 % | 92.4 % / 92.3 % |
| Chiller Carnot fraction [-] (0.25 / 0.38 / 0.5) | 89.8 % / 89.8 % | 92.3 % / 92.4 % |
| Heating set point [degC] (5 / 12 / 20) | 89.8 % / 89.8 % | 92.4 % / 92.4 % |
