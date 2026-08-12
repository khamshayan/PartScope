"""Generate a synthetic parts catalog and load it into MongoDB.

THE DATA IS SYNTHETIC. Part numbers are built from templates that imitate real
manufacturer conventions so the matcher faces realistic string structure, but
the parts themselves do not correspond to real products. See docs/data-sources.md.

Everything derives from one integer seed, so two runs produce an identical
catalog. That matters: the matcher accuracy figures and the backtest results
quoted in the README have to be reproducible by whoever clones this.

Two design points worth knowing before reading on:

1. Specs are generated first, and the MPN is built *from* the specs. Real part
   numbers encode their own values -- CRCW0603 10K0 FKEA is a 0603, 10k, 1%
   resistor -- so generating the two independently would produce a catalog where
   every part number contradicts its own datasheet. Phase 1 ranks alternate
   parts by distance over these spec numbers, so they have to mean something.

2. The catalog lives in Mongo rather than Postgres because `datasheet_specs`
   has a different shape per category. An op-amp has slew rate and gain
   bandwidth; a circular connector has shell size and contact count. Modelling
   that relationally means a wide sparse table or an EAV join, and the matcher
   reads the whole nested object at once anyway.

Run standalone:  python ml-service/data/generate_catalog.py --dry-run
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow `python ml-service/data/generate_catalog.py` to find the app package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

LIFECYCLE_ACTIVE = "Active"
LIFECYCLE_NRND = "NRND"
LIFECYCLE_OBSOLETE = "Obsolete"
LIFECYCLE_EOL = "EOL"
DEAD_LIFECYCLES = (LIFECYCLE_OBSOLETE, LIFECYCLE_EOL)

# Reference date for the synthetic universe. Fixed, not `today`, so that
# "introduced 15 years ago" means the same thing on every run forever.
CATALOG_EPOCH = date(2025, 1, 6)

# E24 significant figures -- the values real passives are actually made in.
E24 = [10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30,
       33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91]

SUFFIX_ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ"

# Murata encodes case size in its own two-digit scheme rather than the imperial
# code, so GRM parts need the translation: 0603 imperial -> GRM18.
MURATA_SIZE = {"0201": "03", "0402": "15", "0603": "18", "0805": "21",
               "1206": "31", "1210": "32", "1812": "43", "2220": "55"}

# TDK and other Japanese makers use the metric (mm x 10) code instead.
METRIC_SIZE = {"0201": "0603", "0402": "1005", "0603": "1608", "0805": "2012",
               "1206": "3216", "1210": "3225", "1812": "4532", "2220": "5750"}

# Voltage code used in the middle of a ceramic capacitor part number.
CAP_VOLTAGE_CODE = {6.3: "0J", 10: "1A", 16: "1C", 25: "1E", 50: "1H",
                    100: "2A", 200: "2D", 630: "2J"}

# Capacitance tolerance letter.
CAP_TOL_CODE = {1: "F", 2: "G", 5: "J", 10: "K", 20: "M"}

# Resistance tolerance letter.
RES_TOL_CODE = {0.1: "B", 0.5: "D", 1.0: "F", 5.0: "J"}

# The two-or-three digit function code in a 74-series logic part number is not
# arbitrary -- '00' has meant quad NAND since 1964. Keeping the mapping honest
# means the description and the part number agree.
LOGIC_FUNCTION_CODE = {
    "NAND": "00", "NOR": "02", "Inverter": "04", "AND": "08",
    "Schmitt Trigger": "14", "OR": "32", "D Flip-Flop": "74", "XOR": "86",
    "Buffer": "125", "Decoder": "138", "Multiplexer": "157",
    "Shift Register": "595",
}

# ST pin-count letter and flash-size letter, from the STM32 numbering scheme.
STM32_PIN_CODE = {14: "F", 20: "K", 26: "C", 32: "C", 45: "R",
                  51: "R", 64: "V", 82: "V", 114: "Z"}
STM32_FLASH_CODE = {8: "3", 16: "4", 32: "6", 64: "8", 128: "B",
                    256: "C", 512: "E", 1024: "G"}
PIN_COUNT_FOR_GPIO = {14: "32", 20: "32", 26: "48", 32: "48", 45: "64",
                      51: "64", 64: "100", 82: "100", 114: "144"}


# ---------------------------------------------------------------------------
# Value-code formatting for passives
# ---------------------------------------------------------------------------

def _eia_4char(ohms: float) -> str:
    """Vishay CRCW-style 4-character resistance code: 10K0, 4K70, 100R, 1M00."""
    for limit, letter, scale in ((1e3, "R", 1.0), (1e6, "K", 1e3), (1e9, "M", 1e6)):
        if ohms < limit:
            scaled = ohms / scale
            digits = f"{scaled:.10g}".replace(".", "")
            whole = f"{int(scaled)}"
            # The multiplier letter sits where the decimal point would be.
            return (whole + letter + digits[len(whole):]).ljust(4, "0")[:4]
    return "1M00"


def _eia_3char(ohms: float) -> str:
    """Yageo RC-style short resistance code: 10K, 4K7, 100R, 100K.

    Drops the trailing pad zero of the 4-character form, but keeps four
    characters when the fourth is significant (100K must not become 100).
    """
    code = _eia_4char(ohms)
    return code[:3] if code[3] == "0" else code


def _cap_code(pf: float) -> str:
    """Three-digit EIA capacitance code in pF: 104 == 100 nF."""
    if pf < 10:
        return f"{int(round(pf))}R{int(round(pf * 10)) % 10}"
    exp = int(math.floor(math.log10(pf))) - 1
    mantissa = int(round(pf / (10 ** exp)))
    if mantissa >= 100:
        mantissa //= 10
        exp += 1
    return f"{mantissa:02d}{min(exp, 9)}"


def _volt_code(volts: float) -> str:
    """Zener/regulator style voltage code: 5.6 -> 5V6, 12.0 -> 12."""
    if volts == int(volts):
        return str(int(volts))
    whole, frac = str(volts).split(".")
    return f"{whole}V{frac[0]}"


# ---------------------------------------------------------------------------
# Spec builders -- one per category
# ---------------------------------------------------------------------------
# These produce the nested `datasheet_specs` object and run BEFORE the MPN is
# built. Numeric fields matter: Phase 1 ranks alternate parts by weighted
# distance over exactly these numbers.

def _specs_mcu(rng: random.Random) -> dict:
    flash = rng.choice([8, 16, 32, 64, 128, 256, 512, 1024])
    return {
        "core": rng.choice(["ARM Cortex-M0+", "ARM Cortex-M3", "ARM Cortex-M4F",
                            "ARM Cortex-M7", "8051", "AVR", "PIC16", "PIC32"]),
        "flash_kb": flash,
        "ram_kb": max(2, flash // rng.choice([4, 8, 16])),
        "max_freq_mhz": rng.choice([8, 16, 20, 48, 72, 80, 120, 168, 216, 400]),
        "gpio_count": rng.choice(list(STM32_PIN_CODE)),
        "adc_bits": rng.choice([8, 10, 12, 12, 16]),
        "operating_voltage": {"min_v": rng.choice([1.62, 1.8, 2.0, 2.7]),
                              "max_v": rng.choice([3.6, 5.5])},
        "temp_range": {"min_c": rng.choice([-40, -40, 0]),
                       "max_c": rng.choice([85, 105, 125])},
    }


def _specs_opamp(rng: random.Random) -> dict:
    return {
        "channels": rng.choice([1, 1, 2, 2, 4]),
        "gbw_mhz": rng.choice([0.35, 1.0, 3.0, 8.0, 10.0, 22.0, 50.0, 100.0]),
        "slew_rate_v_us": round(rng.uniform(0.1, 60.0), 3),
        "input_offset_uv": rng.choice([1, 5, 25, 100, 500, 3000]),
        "supply_voltage": {"min_v": rng.choice([1.8, 2.7, 3.0, 4.5]),
                           "max_v": rng.choice([5.5, 12.0, 36.0, 44.0])},
        "quiescent_current_ua": rng.choice([1, 20, 100, 600, 1500, 5000]),
        "rail_to_rail": rng.choice([True, False]),
    }


def _specs_capacitor(rng: random.Random) -> dict:
    dielectric = rng.choice(["C0G", "X7R", "X7R", "X5R", "Y5V", "NP0", "Tantalum"])
    if dielectric == "Tantalum":
        capacitance = rng.choice([1_000_000, 2_200_000, 4_700_000, 10_000_000])
        voltage = rng.choice([10, 16, 25])
    elif dielectric in ("C0G", "NP0"):
        # C0G/NP0 are the precision, low-value dielectrics; you do not get
        # microfarads out of them.
        capacitance = rng.choice([10, 100, 1000])
        voltage = rng.choice([25, 50, 100, 200, 630])
    else:
        capacitance = rng.choice([1000, 10000, 100000, 1_000_000])
        voltage = rng.choice([6.3, 10, 16, 25, 50, 100])
    return {
        "capacitance_pf": capacitance,
        "tolerance_pct": rng.choice([1, 2, 5, 10, 20]),
        "voltage_rating_v": voltage,
        "dielectric": dielectric,
        "esr_mohm": rng.choice([5, 20, 60, 150, 900, 2500]),
        "temp_range": {"min_c": -55, "max_c": rng.choice([85, 125, 150])},
    }


def _specs_resistor(rng: random.Random) -> dict:
    return {
        "resistance_ohm": round(rng.choice(E24) * (10 ** rng.randint(0, 5)) / 10, 2),
        "tolerance_pct": rng.choice([0.1, 0.5, 1.0, 5.0]),
        "power_rating_w": rng.choice([0.05, 0.063, 0.1, 0.125, 0.25, 0.5, 1.0]),
        "tcr_ppm": rng.choice([25, 50, 100, 200]),
        "voltage_rating_v": rng.choice([25, 50, 75, 150, 200]),
        "temp_range": {"min_c": -55, "max_c": 155},
    }


def _specs_logic(rng: random.Random) -> dict:
    function = rng.choice(list(LOGIC_FUNCTION_CODE))
    # Single-gate packages exist for simple functions only; nobody ships a
    # one-channel shift register.
    channels = 1 if function in ("Buffer", "Decoder", "Multiplexer",
                                 "Shift Register") else rng.choice([1, 2, 4, 6, 8])
    return {
        "logic_family": rng.choice(["LVC", "HC", "HCT", "AHC", "AUP", "LS", "AC"]),
        "function": function,
        "channels": channels,
        "propagation_delay_ns": rng.choice([1.8, 3.3, 5.5, 9.0, 15.0, 25.0]),
        "supply_voltage": {"min_v": rng.choice([0.8, 1.65, 2.0, 4.5]),
                           "max_v": rng.choice([3.6, 5.5, 6.0])},
        "output_current_ma": rng.choice([4, 8, 16, 24, 32]),
    }


# The 1N47xx series is a real, fixed ladder that every distributor knows by
# heart, so the number has to map to the correct voltage. The MMSZ47xxA line
# mirrors it in SOD-123.
ZENER_1N47XX = {3.3: 4728, 4.7: 4732, 5.1: 4733, 5.6: 4734, 6.2: 4735,
                8.2: 4738, 10.0: 4740, 12.0: 4742, 15.0: 4744, 18.0: 4746,
                24.0: 4749, 33.0: 4752}


def _specs_zener(rng: random.Random) -> dict:
    return {
        "zener_voltage_v": rng.choice(list(ZENER_1N47XX)),
        "power_dissipation_mw": rng.choice([200, 225, 350, 500, 1000, 1500, 5000]),
        "tolerance_pct": rng.choice([1, 2, 5]),
        "reverse_leakage_ua": rng.choice([0.1, 0.5, 2.0, 5.0, 50.0]),
        "forward_voltage_v": round(rng.uniform(0.7, 1.2), 2),
        "temp_range": {"min_c": -65, "max_c": rng.choice([150, 175, 200])},
    }


# On-resistance is not free to vary independently of breakdown voltage: for a
# given die area R_DS(on) rises steeply with V_DS. Sampling the three headline
# MOSFET numbers independently produces parts like "600V, 195A, 1.2 mOhm" that
# no reviewer with hardware experience would believe. Range per voltage class,
# then current follows from the resistance.
RDS_RANGE_BY_VDS = {
    20: (1.0, 25.0), 30: (1.3, 35.0), 40: (2.2, 50.0), 60: (3.5, 90.0),
    80: (5.5, 120.0), 100: (9.0, 180.0), 150: (20.0, 300.0), 200: (30.0, 450.0),
    500: (150.0, 2000.0), 600: (190.0, 2500.0),
}


def _specs_fet(rng: random.Random) -> dict:
    vds = rng.choice(list(RDS_RANGE_BY_VDS))
    lo, hi = RDS_RANGE_BY_VDS[vds]
    # Log-uniform across the class: die sizes are geometric, not linear.
    rds = round(math.exp(rng.uniform(math.log(lo), math.log(hi))), 1)
    # Continuous current is thermally limited, so it falls off as ~1/sqrt(R).
    current = round(min(200.0, max(0.3, 3.2 * math.sqrt(1000.0 / rds))), 1)
    return {
        "channel_type": rng.choice(["N-Channel", "N-Channel", "P-Channel"]),
        "vds_max_v": vds,
        "id_continuous_a": current,
        "rds_on_mohm": rds,
        # Bigger die => lower R_DS(on) => more gate charge to move.
        "vgs_threshold_v": rng.choice([0.8, 1.2, 1.8, 2.5, 4.0]),
        "gate_charge_nc": round(max(1.5, 220.0 / math.sqrt(rds)) * rng.uniform(0.7, 1.4), 1),
        "temp_range": {"min_c": -55, "max_c": rng.choice([150, 175])},
    }


def _specs_regulator(rng: random.Random) -> dict:
    topology = rng.choice(["LDO", "LDO", "Buck", "Boost", "Buck-Boost", "Linear"])
    return {
        "topology": topology,
        "output_voltage_v": rng.choice([1.2, 1.8, 2.5, 3.3, 5.0, 12.0]),
        "input_voltage_max_v": rng.choice([6.0, 16.0, 20.0, 36.0, 60.0]),
        "output_current_max_a": rng.choice([0.15, 0.25, 0.5, 1.0, 1.5, 3.0, 5.0]),
        "dropout_mv": rng.choice([80, 150, 300, 600, 1200]) if topology == "LDO" else None,
        "efficiency_pct": rng.choice([82, 88, 92, 95]) if topology not in ("LDO", "Linear") else None,
        "quiescent_current_ua": rng.choice([1, 25, 55, 300, 5000]),
        "temp_range": {"min_c": rng.choice([-40, 0]), "max_c": rng.choice([85, 125])},
    }


def _specs_fpga(rng: random.Random) -> dict:
    le = rng.choice([1500, 6000, 12000, 25000, 60000, 150000, 250000])
    return {
        "logic_elements": le,
        "block_ram_kb": le // rng.choice([8, 12, 20]),
        "dsp_blocks": rng.choice([0, 18, 56, 84, 240]),
        "user_io_count": rng.choice([68, 100, 144, 200, 292, 377]),
        "speed_grade": rng.choice(["-1", "-2", "STD"]),
        "core_voltage_v": rng.choice([1.0, 1.2, 1.5]),
        "temp_range": {"min_c": rng.choice([-40, 0, -55]),
                       "max_c": rng.choice([85, 100, 125])},
    }


def _specs_sram(rng: random.Random) -> dict:
    density, organization = rng.choice([
        (256, "32K x 8"), (1024, "128K x 8"), (2048, "128K x 16"),
        (4096, "256K x 16"), (16384, "1M x 16"), (64, "8K x 8"),
    ])
    return {
        "density_kbit": density,
        "organization": organization,
        "access_time_ns": rng.choice([8, 10, 12, 45, 55, 70]),
        "interface": rng.choice(["Parallel", "Parallel", "SPI"]),
        "supply_voltage": {"min_v": rng.choice([1.65, 2.2, 2.7, 4.5]),
                           "max_v": rng.choice([3.6, 5.5])},
        "temp_range": {"min_c": rng.choice([-40, 0]), "max_c": rng.choice([85, 125])},
    }


def _specs_flash(rng: random.Random) -> dict:
    return {
        "density_mbit": rng.choice([4, 8, 16, 32, 64, 128, 256, 512]),
        "interface": rng.choice(["SPI", "SPI", "Quad SPI", "Parallel"]),
        "max_clock_mhz": rng.choice([50, 75, 80, 104, 133]),
        "erase_cycles": rng.choice([10000, 100000, 1000000]),
        "page_size_bytes": rng.choice([256, 256, 512]),
        "data_retention_years": rng.choice([20, 100]),
        "temp_range": {"min_c": rng.choice([-40, 0]), "max_c": rng.choice([85, 105])},
    }


def _specs_connector(rng: random.Random) -> dict:
    shell, contacts = rng.choice([
        (8, 2), (10, 3), (12, 4), (14, 6), (16, 7), (18, 10),
        (20, 13), (22, 19), (24, 26), (28, 37),
    ])
    return {
        "shell_size": shell,
        "contact_count": contacts,
        "contact_gender": rng.choice(["Pin", "Socket"]),
        "shell_plating": rng.choice(["Olive Drab Cadmium", "Electroless Nickel",
                                     "Black Zinc Nickel", "Passivated Stainless"]),
        "service_rating_v": rng.choice([250, 500, 900, 1800]),
        "mating_cycles": rng.choice([200, 500, 1500]),
        "mil_spec": rng.choice(["MIL-DTL-5015", "MIL-DTL-38999 Series III",
                                "MIL-DTL-26482", "MIL-DTL-83723"]),
        "temp_range": {"min_c": -65, "max_c": rng.choice([175, 200])},
    }


def _specs_oscillator(rng: random.Random) -> dict:
    return {
        "frequency_mhz": rng.choice([4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 25.0, 48.0, 100.0]),
        "frequency_tolerance_ppm": rng.choice([10, 20, 30, 50, 100]),
        "load_capacitance_pf": rng.choice([8, 12, 15, 18, 20]),
        "esr_ohm": rng.choice([30, 50, 60, 80, 100]),
        "stability_ppm": rng.choice([10, 25, 50, 100]),
        "output_type": rng.choice(["CMOS", "LVDS", "Resonator"]),
        "temp_range": {"min_c": rng.choice([-40, -20, 0]),
                       "max_c": rng.choice([85, 105, 125])},
    }


# ---------------------------------------------------------------------------
# MPN builders -- one per (category, manufacturer)
# ---------------------------------------------------------------------------
# Signature is uniform: (rng, specs, package) -> str. Builders read from `specs`
# wherever the real convention encodes a value in the part number, and ignore it
# where it genuinely does not (an OPA333 tells you nothing about its bandwidth).
# They are deliberately verbose rather than clever: a reader should be able to
# hold any line next to a real datasheet and see the convention is right.

def _mcu_st(rng, specs, package) -> str:
    fam = rng.choice(["F0", "F1", "F3", "F4", "F7", "G0", "G4", "H7", "L0", "L4"])
    num = rng.choice(["03", "05", "07", "10", "30", "31", "51", "70", "72", "76"])
    pins = STM32_PIN_CODE[specs["gpio_count"]]
    flash = STM32_FLASH_CODE[specs["flash_kb"]]
    suffix = rng.choice(["T6", "T7", "U6", "H6", "I6", "Y6"])
    return f"STM32{fam}{num}{pins}{flash}{suffix}"


def _mcu_ti(rng, specs, package) -> str:
    fam = rng.choice(["F", "G", "FR"])
    return (f"MSP430{fam}{rng.randint(1000, 6999)}"
            f"{rng.choice(['IPM', 'IPN', 'IRGE', 'IDA', 'IPZ'])}")


# An ATmega's number is its flash size in KB (ATmega328 is the exception that
# proves the rule; ATmega2560 is 256KB). Parts above 256KB are not in the line.
ATMEGA_FOR_FLASH = {8: "8", 16: "16", 32: "32", 64: "64", 128: "128", 256: "2560"}


def _mcu_microchip(rng, specs, package) -> str:
    flash = specs["flash_kb"]
    if flash in ATMEGA_FOR_FLASH and (specs["core"] == "AVR" or rng.random() < 0.4):
        return (f"ATMEGA{ATMEGA_FOR_FLASH[flash]}"
                f"{rng.choice(['', 'P', 'A', 'PA'])}-{rng.choice(['PU', 'AU', 'MU'])}")
    series = rng.choice(["10", "12", "16", "18", "24", "32"])
    return (f"PIC{series}{rng.choice(['F', 'LF'])}{rng.randint(84, 4550)}"
            f"{rng.choice(['', 'A', 'B'])}")


def _mcu_nxp(rng, specs, package) -> str:
    pins = PIN_COUNT_FOR_GPIO[specs["gpio_count"]]
    if rng.random() < 0.5:
        return (f"LPC{rng.choice(['11', '15', '17', '18', '54', '55'])}"
                f"{rng.randint(10, 99)}{rng.choice(['FBD', 'FET', 'JBD'])}{pins}")
    return (f"MK{rng.choice(['22', '4', '6', '10'])}{rng.choice(['FN', 'FX'])}"
            f"{specs['flash_kb']}VLL{rng.choice(['12', '15'])}")


def _mcu_infineon(rng, specs, package) -> str:
    pins = PIN_COUNT_FOR_GPIO[specs["gpio_count"]]
    return (f"XMC{rng.choice(['1100', '1300', '4100', '4400', '4500'])}"
            f"{rng.choice(['F', 'Q', 'T'])}{pins}"
            f"{rng.choice(['K', 'F'])}{specs['flash_kb']}{rng.choice(['AB', 'AC'])}")


def _opamp_ti(rng, specs, package) -> str:
    fam = rng.choice(["OPA", "TLV", "TLC", "LMV"])
    return (f"{fam}{rng.randint(300, 4990)}"
            f"{rng.choice(['IDR', 'ID', 'AIDR', 'IDBVR', 'PA', 'IDGKR'])}")


def _opamp_adi(rng, specs, package) -> str:
    if rng.random() < 0.3:
        return (f"OP{rng.choice(['07', '27', '37', '77', '177', '297'])}"
                f"{rng.choice(['CP', 'AZ', 'GSZ', 'ARZ'])}")
    if rng.random() < 0.4:
        # ADA parts spell out the channel count after the dash.
        return (f"ADA{rng.randint(4000, 4899)}-{specs['channels']}"
                f"{rng.choice(['ARZ', 'ACPZ', 'ARMZ'])}")
    return f"AD{rng.randint(600, 8999)}{rng.choice(['ARZ', 'ANZ', 'BRZ', 'ARMZ', 'ACPZ'])}"


def _opamp_st(rng, specs, package) -> str:
    if rng.random() < 0.4:
        # TL07x: the last digit is the channel count (1, 2 or 4).
        base = rng.choice(["06", "07", "08"])
        return (f"TL{base}{specs['channels']}"
                f"{rng.choice(['CDT', 'CN', 'IDT', 'ACDT'])}")
    return (f"TS{rng.choice(['V', 'Z', 'X'])}{rng.randint(321, 994)}"
            f"{rng.choice(['ILT', 'IDT', 'AILT'])}")


def _opamp_microchip(rng, specs, package) -> str:
    return (f"MCP{rng.choice(['600', '601', '602', '604', '61', '62', '63', '64'])}"
            f"{rng.randint(1, 9)}-{rng.choice(['I', 'E'])}"
            f"/{rng.choice(['SN', 'P', 'MS', 'OT'])}")


def _opamp_onsemi(rng, specs, package) -> str:
    return (f"{rng.choice(['LM', 'NCS', 'MC'])}"
            f"{rng.choice(['358', '324', '2904', '33272', '20001'])}"
            f"{rng.choice(['DR2G', 'DG', 'ADR2G', 'SNT1G'])}")


def _cap_murata(rng, specs, package) -> str:
    return (f"GRM{MURATA_SIZE[package]}{rng.randint(5, 9)}"
            f"{rng.choice(['R6', 'R7', 'R8'])}"
            f"{CAP_VOLTAGE_CODE[specs['voltage_rating_v']]}"
            f"{_cap_code(specs['capacitance_pf'])}"
            f"{CAP_TOL_CODE[specs['tolerance_pct']]}"
            f"{rng.choice(['A93D', 'A88D', 'A01D'])}")


def _cap_tdk(rng, specs, package) -> str:
    return (f"C{METRIC_SIZE[package]}{specs['dielectric']}"
            f"{CAP_VOLTAGE_CODE[specs['voltage_rating_v']]}"
            f"{_cap_code(specs['capacitance_pf'])}"
            f"{CAP_TOL_CODE[specs['tolerance_pct']]}"
            f"{rng.choice(['080AA', '160AB', '250AA'])}")


def _cap_kemet(rng, specs, package) -> str:
    if specs["dielectric"] == "Tantalum":
        case = rng.choice(["A", "B", "C", "D"])
        return (f"T{rng.choice(['491', '494', '520'])}{case}"
                f"{_cap_code(specs['capacitance_pf'])}"
                f"{CAP_TOL_CODE[specs['tolerance_pct']]}"
                f"{int(specs['voltage_rating_v']):03d}AT")
    return (f"C{package}C{_cap_code(specs['capacitance_pf'])}"
            f"{CAP_TOL_CODE[specs['tolerance_pct']]}"
            f"{rng.choice(['5R', '4R', '3G'])}"
            f"{rng.choice(['ACTU', 'AC7800', 'ACAUTO'])}")


def _cap_yageo(rng, specs, package) -> str:
    return (f"CC{package}{CAP_TOL_CODE[specs['tolerance_pct']]}R"
            f"{specs['dielectric']}{rng.choice(['9BB', '8BB', '7BB'])}"
            f"{_cap_code(specs['capacitance_pf'])}")


def _res_vishay(rng, specs, package) -> str:
    return (f"CRCW{package}{_eia_4char(specs['resistance_ohm'])}"
            f"{RES_TOL_CODE[specs['tolerance_pct']]}"
            f"{rng.choice(['KEA', 'KEB', 'KEC', 'KEAHP'])}")


def _res_yageo(rng, specs, package) -> str:
    return (f"RC{package}{RES_TOL_CODE[specs['tolerance_pct']]}R-07"
            f"{_eia_3char(specs['resistance_ohm'])}L")


def _logic_ti(rng, specs, package) -> str:
    fn = LOGIC_FUNCTION_CODE[specs["function"]]
    gate = f"{specs['channels']}G" if specs["channels"] <= 2 and rng.random() < 0.5 else ""
    return (f"SN74{specs['logic_family']}{gate}{fn}"
            f"{rng.choice(['DBVR', 'DR', 'PWR', 'N', 'NSR', 'DCKR'])}")


def _logic_nxp(rng, specs, package) -> str:
    return (f"74{specs['logic_family']}{LOGIC_FUNCTION_CODE[specs['function']]}"
            f"{rng.choice(['D', 'PW', 'N', 'DB'])}")


def _logic_nexperia(rng, specs, package) -> str:
    gate = f"{specs['channels']}G" if specs["channels"] <= 2 else ""
    return (f"74{specs['logic_family']}{gate}"
            f"{LOGIC_FUNCTION_CODE[specs['function']]}"
            f"{rng.choice(['GW', 'GV', 'DB', 'PW'])}")


def _logic_diodes(rng, specs, package) -> str:
    return (f"74{specs['logic_family']}1G{LOGIC_FUNCTION_CODE[specs['function']]}"
            f"{rng.choice(['SE', 'W5', 'GW'])}-7")


def _logic_onsemi(rng, specs, package) -> str:
    gate = f"{specs['channels']}G" if specs["channels"] <= 2 else ""
    return (f"MC74{specs['logic_family']}{gate}"
            f"{LOGIC_FUNCTION_CODE[specs['function']]}"
            f"{rng.choice(['DFT2G', 'DTT1G', 'MEL'])}")


def _zener_onsemi(rng, specs, package) -> str:
    number = ZENER_1N47XX[specs["zener_voltage_v"]]
    if rng.random() < 0.4:
        return f"1N{number}{rng.choice(['A', 'ATR', 'RLG'])}"
    return f"MMSZ{number}{rng.choice(['T1G', 'AT1G'])}"


def _zener_vishay(rng, specs, package) -> str:
    code = _volt_code(specs["zener_voltage_v"])
    return (f"{rng.choice(['BZX84C', 'TZX', 'BZT52C'])}{code}"
            f"{rng.choice(['-V-GS08', '-GS08', 'B', ''])}")


def _zener_nexperia(rng, specs, package) -> str:
    code = _volt_code(specs["zener_voltage_v"])
    return f"BZX84{rng.choice(['-C', 'C'])}{code}{rng.choice([',215', ',235', ''])}"


def _zener_diodes(rng, specs, package) -> str:
    code = _volt_code(specs["zener_voltage_v"])
    return f"DDZ{code}{rng.choice(['S', 'C', 'B'])}-7"


def _fet_infineon(rng, specs, package) -> str:
    # BSC030N08NS5: 3.0 mOhm, 80V. The OptiMOS BSC line is low-voltage only and
    # its code has no room for a four-digit resistance, so it is used only where
    # the specs actually fit; everything else falls back to the IRF line.
    if specs["vds_max_v"] <= 100 and specs["rds_on_mohm"] < 100:
        return (f"BSC{int(specs['rds_on_mohm'] * 10):03d}"
                f"N{specs['vds_max_v']:02d}"
                f"{rng.choice(['LS5', 'NS5', 'LSI'])}")
    return (f"IRF{rng.choice(['', 'Z', 'B', 'R', 'P'])}{rng.randint(120, 9640)}"
            f"{rng.choice(['N', 'NPBF', 'PBF', 'ZPBF'])}")


def _fet_vishay(rng, specs, package) -> str:
    return (f"SI{rng.choice(['', 'R', 'H'])}{rng.randint(2300, 7999)}"
            f"{rng.choice(['DP', 'DN', 'ADY', 'DDY'])}"
            f"{rng.choice(['-T1-GE3', '-T1-E3', '', '-GE3'])}")


def _fet_onsemi(rng, specs, package) -> str:
    polarity = "P" if specs["channel_type"] == "P-Channel" else rng.choice(["N", "NL"])
    return (f"NT{rng.choice(['D', 'MFS', 'R'])}{rng.randint(2955, 8899)}"
            f"{polarity}{rng.choice(['-1G', 'G', 'T4G'])}")


def _fet_st(rng, specs, package) -> str:
    # STP55NF06: 55A, N-channel, 60V.
    volt = {20: "F03", 30: "F03", 40: "F06", 60: "F06", 80: "F10",
            100: "F10", 150: "M60", 200: "M60", 500: "S10", 600: "S10"}
    polarity = "P" if specs["channel_type"] == "P-Channel" else "N"
    return (f"ST{rng.choice(['P', 'B', 'D', 'W'])}"
            f"{max(1, int(specs['id_continuous_a']))}"
            f"{polarity}{volt[specs['vds_max_v']]}{rng.choice(['L', '', 'LT4'])}")


def _fet_diodes(rng, specs, package) -> str:
    polarity = "P" if specs["channel_type"] == "P-Channel" else rng.choice(["N", "G"])
    return (f"DM{polarity}{rng.randint(2004, 6099)}"
            f"{rng.choice(['U', 'K', 'UD', 'DW'])}-7")


def _fet_nexperia(rng, specs, package) -> str:
    # PSMN is an N-channel family; P-channel parts carry the PMV prefix instead,
    # so the part number cannot contradict the channel type.
    if specs["channel_type"] == "P-Channel":
        return f"PMV{rng.randint(30, 99)}{rng.choice(['XP', 'UP', 'EP'])}"
    # PSMN1R0-30YLC: 1.0 mOhm, 30V.
    return (f"PSMN{_volt_code(specs['rds_on_mohm'])}"
            f"-{specs['vds_max_v']}{rng.choice(['YLC', 'PL', 'YL'])}")


def _reg_ti(rng, specs, package) -> str:
    if rng.random() < 0.3:
        return (f"LM{rng.choice(['317', '337', '1117', '2596', '2940'])}"
                f"{rng.choice(['T', 'MP', 'DT', 'IMP'])}"
                f"-{specs['output_voltage_v']:g}")
    return f"TPS{rng.randint(5400, 7999)}{rng.choice(['DGNR', 'DRBR', 'DCQR', 'RGTR', 'DDCR'])}"


def _reg_st(rng, specs, package) -> str:
    vout = specs["output_voltage_v"]
    if vout in (5.0, 12.0) and rng.random() < 0.5:
        # L7805CV: the 78 series encodes output volts in the two digits after.
        return f"L78{int(vout):02d}{rng.choice(['CV', 'ABV', 'CD2T', 'ACV'])}"
    return (f"LD{rng.choice(['1117', '1086', '39', '59'])}"
            f"{rng.choice(['S', 'AS', 'V'])}"
            f"{str(vout).replace('.', '')[:2]}{rng.choice(['TR', 'CTR', ''])}")


def _reg_adi(rng, specs, package) -> str:
    if rng.random() < 0.45:
        return (f"LT{rng.randint(1000, 3099)}{rng.choice(['CS8', 'IQ', 'CDD', 'EMS'])}"
                f"-{specs['output_voltage_v']:g}")
    return (f"ADP{rng.randint(1600, 7199)}{rng.choice(['AKCZ', 'ACPZ', 'ARMZ'])}"
            f"-{specs['output_voltage_v']:g}")


def _reg_onsemi(rng, specs, package) -> str:
    return (f"NC{rng.choice(['P', 'V'])}{rng.randint(1117, 4699)}"
            f"{rng.choice(['ST', 'DT', 'SN'])}"
            f"{str(specs['output_voltage_v']).replace('.', '')[:2]}"
            f"{rng.choice(['T3G', 'T1G', 'G'])}")


def _reg_diodes(rng, specs, package) -> str:
    return (f"AP{rng.randint(1117, 7399)}{rng.choice(['H', 'K', 'Y', 'D'])}"
            f"-{specs['output_voltage_v']:g}TRG1")


def _reg_microchip(rng, specs, package) -> str:
    # MCP1700-3302E/TO: '3302' is 3.3V to two decimal places.
    vcode = f"{int(specs['output_voltage_v'] * 100):04d}"
    return (f"MCP{rng.choice(['1700', '1703', '1754', '1826', '1827'])}"
            f"-{vcode}{rng.choice(['E', 'I'])}"
            f"/{rng.choice(['TO', 'TT', 'MB', 'DB'])}")


def _fpga_microchip(rng, specs, package) -> str:
    fam = rng.choice(["M2GL", "M2S", "A3P", "A3PN", "AGLN", "MPF"])
    # Family size digits are the logic-element count in thousands.
    size = f"{max(5, specs['logic_elements'] // 1000):03d}"
    pkg = package.replace("-", "").replace("FBGA", "FG").replace("VQFP", "VQ") \
                 .replace("TQFP", "TQ").replace("CSP", "CS")
    grade = rng.choice(["I", "", "T"])
    return f"{fam}{size}{rng.choice(['T', ''])}-{pkg}{grade}"


# CY7C part numbers encode total density: CY7C1041 is 4 Mbit, CY7C1061 is 16.
CY7C_FOR_DENSITY = {1024: "1021", 2048: "1031", 4096: "1041", 16384: "1061"}


def _sram_infineon(rng, specs, package) -> str:
    speed = f"{specs['access_time_ns']:02d}"
    density = specs["density_kbit"]
    # CY62xxx spells out density in kbit: CY62256 is a 256 kbit part.
    if density <= 1024 or density not in CY7C_FOR_DENSITY:
        return (f"CY62{density}{rng.choice(['E', 'G', 'NLL', 'DV30'])}-{speed}"
                f"{rng.choice(['SNXI', 'ZSXI', 'ZXI'])}")
    return (f"CY7C{CY7C_FOR_DENSITY[density]}"
            f"{rng.choice(['GN30', 'DV33', 'G30'])}-{speed}"
            f"{rng.choice(['ZSXI', 'ZXI'])}")


def _sram_microchip(rng, specs, package) -> str:
    return (f"23{rng.choice(['A', 'LC', 'K'])}{specs['density_kbit']}"
            f"-I/{rng.choice(['SN', 'P', 'ST'])}")


def _flash_microchip(rng, specs, package) -> str:
    density = f"{specs['density_mbit']:03d}"
    if rng.random() < 0.5:
        return (f"SST2{rng.choice(['5', '6'])}VF{density}{rng.choice(['B', 'A'])}"
                f"-{specs['max_clock_mhz']}I/{rng.choice(['SN', 'MF'])}")
    return (f"AT25{rng.choice(['SF', 'DF', 'SL'])}{density}"
            f"-{rng.choice(['SSHD-B', 'MHB-Y', 'SHD-T'])}")


def _flash_infineon(rng, specs, package) -> str:
    return (f"S25FL{specs['density_mbit']:03d}"
            f"{rng.choice(['L', 'S'])}{rng.choice(['AGMFI', 'AGNFI', 'AGMFB'])}"
            f"{rng.choice(['010', '011', '001'])}")


def _conn_mil(rng, specs, package) -> str:
    """MIL-spec circular connectors follow the standard, not a house style."""
    shell = specs["shell_size"]
    insert = specs["contact_count"]
    gender = "P" if specs["contact_gender"] == "Pin" else "S"
    style = rng.random()
    if style < 0.4:
        return (f"MS3{rng.choice(['10', '11', '12'])}{rng.randint(1, 9)}"
                f"{rng.choice(['A', 'E', 'F', 'R'])}-{shell}-{insert}{gender}")
    if style < 0.75:
        return (f"D38999/{rng.choice(['20', '24', '26'])}"
                f"{rng.choice(['W', 'F', 'J'])}{rng.choice(['A', 'B', 'C', 'D', 'E'])}"
                f"{shell}{insert}{gender}N")
    return (f"97-3{rng.choice(['10', '02', '06', '08'])}{rng.randint(0, 9)}"
            f"A-{shell}-{insert}{gender}")


def _xtal_murata(rng, specs, package) -> str:
    # CSTCE8M00G55-R0: 8.00 MHz written with M as the decimal point.
    mhz = specs["frequency_mhz"]
    freq = f"{int(mhz)}M{int(round((mhz - int(mhz)) * 100)):02d}"
    return f"CST{rng.choice(['CE', 'LS', 'NE'])}{freq}G{rng.choice(['55', '52', '15'])}-R0"


def _xtal_microchip(rng, specs, package) -> str:
    return (f"DSC{rng.choice(['1001', '1033', '6011', '1123'])}"
            f"{rng.choice(['CI', 'BI', 'CE'])}{rng.randint(1, 5)}"
            f"-{specs['frequency_mhz']:09.4f}")


# ---------------------------------------------------------------------------
# Category plan
# ---------------------------------------------------------------------------
# `weight` is share of the catalog. Passives dominate, matching a real
# distributor line card far better than an even split would.
# `obsolescence_bias` scales how fast a category ages out: memory and
# programmable logic go end-of-life quickly, jellybean passives almost never do.

class CategoryPlan:
    def __init__(self, weight, builders, specs, packages,
                 obsolescence_bias=1.0, defense_ratio=0.03, price_tier="mid",
                 package_for=None):
        self.weight = weight
        self.builders = builders  # {manufacturer: mpn_builder}
        self.specs = specs
        self.packages = packages
        self.obsolescence_bias = obsolescence_bias
        self.defense_ratio = defense_ratio
        self.price_tier = price_tier
        # Optional (rng, specs) -> package. Set where physics constrains the
        # choice; a 1W resistor does not come in an 0402.
        self.package_for = package_for


# A resistor's power rating is a function of how much copper it can dump heat
# into, which is to say its case size. These are the standard pairings.
SIZES_FOR_POWER = {
    0.05: ["0201"], 0.063: ["0402"], 0.1: ["0402", "0603"],
    0.125: ["0603", "0805"], 0.25: ["0805", "1206"],
    0.5: ["1206", "1210"], 1.0: ["1812", "2220"],
}


def _package_for_resistor(rng, specs):
    return rng.choice(SIZES_FOR_POWER[specs["power_rating_w"]])


def _package_for_capacitor(rng, specs):
    """Capacitance and voltage both push the case size up."""
    pf = specs["capacitance_pf"]
    if specs["dielectric"] == "Tantalum":
        floor = 4  # 1206 and up
    elif pf >= 1_000_000:
        floor = 3
    elif pf >= 100_000:
        floor = 2
    elif pf >= 10_000:
        floor = 1
    else:
        floor = 0
    if specs["voltage_rating_v"] >= 200:
        floor = max(floor, 3)
    return rng.choice(PASSIVE_SIZES[floor:])


def _package_for_sram(rng, specs):
    """Wide parallel busses need pins; a serial part fits in eight."""
    if specs["interface"] == "SPI":
        return rng.choice(["SOIC-8", "SOP-28"])
    if specs["density_kbit"] >= 4096:
        return rng.choice(["TSOP-44", "TSOP-II-44", "BGA-48"])
    return rng.choice(["TSOP-44", "SOP-28", "TSOP-II-44"])


# Passive package lists are restricted to the sizes the value-code tables know
# how to encode; a package the builder cannot express would produce a part
# number that contradicts its own package field.
PASSIVE_SIZES = ["0201", "0402", "0603", "0805", "1206", "1210", "1812", "2220"]

CATEGORY_PLANS: dict[str, CategoryPlan] = {
    "Fixed Resistors": CategoryPlan(
        weight=0.155,
        builders={"Vishay": _res_vishay, "Yageo": _res_yageo},
        specs=_specs_resistor,
        packages=PASSIVE_SIZES,
        package_for=_package_for_resistor,
        obsolescence_bias=0.35, defense_ratio=0.04, price_tier="jellybean",
    ),
    "Fixed Capacitors": CategoryPlan(
        weight=0.155,
        builders={"Murata": _cap_murata, "TDK": _cap_tdk,
                  "KEMET": _cap_kemet, "Yageo": _cap_yageo},
        specs=_specs_capacitor,
        packages=PASSIVE_SIZES,
        package_for=_package_for_capacitor,
        obsolescence_bias=0.4, defense_ratio=0.05, price_tier="jellybean",
    ),
    "Logic Gates": CategoryPlan(
        weight=0.105,
        builders={"Texas Instruments": _logic_ti, "NXP": _logic_nxp,
                  "Nexperia": _logic_nexperia, "Diodes Inc": _logic_diodes,
                  "onsemi": _logic_onsemi},
        specs=_specs_logic,
        packages=["SOIC-14", "TSSOP-14", "SOT-23-5", "SC-70-5", "SOIC-16",
                  "DIP-14", "TSSOP-20", "SOP-8"],
        obsolescence_bias=1.4, defense_ratio=0.05, price_tier="low",
    ),
    "Operational Amplifiers": CategoryPlan(
        weight=0.10,
        builders={"Texas Instruments": _opamp_ti, "Analog Devices": _opamp_adi,
                  "STMicroelectronics": _opamp_st, "Microchip": _opamp_microchip,
                  "onsemi": _opamp_onsemi},
        specs=_specs_opamp,
        packages=["SOIC-8", "MSOP-8", "SOT-23-5", "TSSOP-14", "DIP-8", "QFN-16", "SC-70-5"],
        obsolescence_bias=1.1, defense_ratio=0.06, price_tier="low",
    ),
    "Voltage Regulators": CategoryPlan(
        weight=0.095,
        builders={"Texas Instruments": _reg_ti, "STMicroelectronics": _reg_st,
                  "Analog Devices": _reg_adi, "onsemi": _reg_onsemi,
                  "Diodes Inc": _reg_diodes, "Microchip": _reg_microchip},
        specs=_specs_regulator,
        packages=["SOT-223", "TO-220", "SOT-23-5", "QFN-16", "DFN-8",
                  "TO-263", "MSOP-8", "SOT-89"],
        obsolescence_bias=1.2, defense_ratio=0.04, price_tier="mid",
    ),
    "Power FETs": CategoryPlan(
        weight=0.09,
        builders={"Infineon": _fet_infineon, "Vishay": _fet_vishay,
                  "onsemi": _fet_onsemi, "STMicroelectronics": _fet_st,
                  "Diodes Inc": _fet_diodes, "Nexperia": _fet_nexperia},
        specs=_specs_fet,
        packages=["TO-220", "TO-263", "PowerPAK 1212-8", "DPAK", "SOT-23",
                  "QFN-8", "TO-247", "SO-8"],
        obsolescence_bias=1.0, defense_ratio=0.04, price_tier="mid",
    ),
    "Zener Diodes": CategoryPlan(
        weight=0.075,
        builders={"onsemi": _zener_onsemi, "Vishay": _zener_vishay,
                  "Nexperia": _zener_nexperia, "Diodes Inc": _zener_diodes},
        specs=_specs_zener,
        packages=["SOD-123", "SOT-23", "DO-35", "DO-41", "SOD-323", "SMA", "SMB"],
        obsolescence_bias=0.8, defense_ratio=0.07, price_tier="jellybean",
    ),
    "Microcontrollers": CategoryPlan(
        weight=0.085,
        builders={"STMicroelectronics": _mcu_st, "Texas Instruments": _mcu_ti,
                  "Microchip": _mcu_microchip, "NXP": _mcu_nxp,
                  "Infineon": _mcu_infineon},
        specs=_specs_mcu,
        packages=["LQFP-48", "LQFP-64", "LQFP-100", "UFQFPN-32", "TSSOP-20",
                  "DIP-28", "BGA-176", "QFN-40"],
        obsolescence_bias=1.6, defense_ratio=0.03, price_tier="high",
    ),
    "Crystal Oscillators": CategoryPlan(
        weight=0.045,
        builders={"Murata": _xtal_murata, "Microchip": _xtal_microchip},
        specs=_specs_oscillator,
        packages=["SMD-3225", "SMD-2520", "SMD-7050", "SMD-5032", "DIP-4"],
        obsolescence_bias=0.9, defense_ratio=0.04, price_tier="low",
    ),
    "Flash Memory": CategoryPlan(
        weight=0.035,
        builders={"Microchip": _flash_microchip, "Infineon": _flash_infineon},
        specs=_specs_flash,
        packages=["SOIC-8", "WSON-8", "TSSOP-8", "BGA-24", "SOIC-16"],
        obsolescence_bias=2.0, defense_ratio=0.04, price_tier="mid",
    ),
    "SRAM": CategoryPlan(
        weight=0.03,
        builders={"Infineon": _sram_infineon, "Microchip": _sram_microchip},
        specs=_specs_sram,
        packages=["TSOP-44", "SOIC-8", "BGA-48", "TSOP-II-44", "SOP-28"],
        package_for=_package_for_sram,
        obsolescence_bias=2.2, defense_ratio=0.06, price_tier="mid",
    ),
    # None of the semiconductor makers listed above produce MIL-spec circular
    # connectors, so this category carries the two interconnect houses that do.
    "MIL-Spec Circular Connectors": CategoryPlan(
        weight=0.02,
        builders={"Amphenol": _conn_mil, "ITT Cannon": _conn_mil},
        specs=_specs_connector,
        packages=["Wall Mount Receptacle", "Straight Plug", "Box Mount Receptacle",
                  "Jam Nut Receptacle", "Cable Connecting Plug"],
        obsolescence_bias=1.5, defense_ratio=1.0, price_tier="high",
    ),
    "FPGAs": CategoryPlan(
        weight=0.01,
        builders={"Microchip": _fpga_microchip},
        specs=_specs_fpga,
        packages=["FBGA-484", "FBGA-676", "VQFP-100", "TQFP-144", "CSP-256"],
        obsolescence_bias=2.4, defense_ratio=0.25, price_tier="premium",
    ),
}


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------

def _format_capacitance(pf: float) -> str:
    if pf < 1000:
        return f"{pf:g}PF"
    if pf < 1_000_000:
        return f"{pf / 1000:g}NF"
    return f"{pf / 1_000_000:g}UF"


def _describe(category: str, specs: dict, package: str) -> str:
    """Short human description, the kind a distributor line card would carry."""
    if category == "Fixed Resistors":
        return (f"RES {specs['resistance_ohm']:g} OHM {specs['tolerance_pct']:g}% "
                f"{specs['power_rating_w']:g}W {package} THICK FILM")
    if category == "Fixed Capacitors":
        kind = "TANT" if specs["dielectric"] == "Tantalum" else "CER"
        return (f"CAP {kind} {_format_capacitance(specs['capacitance_pf'])} "
                f"{specs['voltage_rating_v']:g}V {specs['dielectric']} "
                f"{specs['tolerance_pct']:g}% {package}")
    if category == "Logic Gates":
        return (f"IC {specs['function'].upper()} {specs['channels']}CH "
                f"{specs['logic_family']} {package}")
    if category == "Operational Amplifiers":
        rr = "RRIO " if specs["rail_to_rail"] else ""
        return f"IC OPAMP {specs['channels']}CH {rr}{specs['gbw_mhz']:g}MHZ GBW {package}"
    if category == "Voltage Regulators":
        return (f"IC REG {specs['topology'].upper()} {specs['output_voltage_v']:g}V "
                f"{specs['output_current_max_a']:g}A {package}")
    if category == "Power FETs":
        return (f"MOSFET {specs['channel_type'].upper()} {specs['vds_max_v']}V "
                f"{specs['id_continuous_a']:g}A {specs['rds_on_mohm']:g}MOHM {package}")
    if category == "Zener Diodes":
        return (f"DIODE ZENER {specs['zener_voltage_v']:g}V "
                f"{specs['power_dissipation_mw']}MW {package}")
    if category == "Microcontrollers":
        return (f"IC MCU {specs['core']} {specs['flash_kb']}KB FLASH "
                f"{specs['max_freq_mhz']}MHZ {package}")
    if category == "Crystal Oscillators":
        return (f"{specs['output_type'].upper()} {specs['frequency_mhz']:g}MHZ "
                f"{specs['frequency_tolerance_ppm']}PPM {package}")
    if category == "Flash Memory":
        return (f"IC FLASH {specs['density_mbit']}MBIT {specs['interface'].upper()} "
                f"{specs['max_clock_mhz']}MHZ {package}")
    if category == "SRAM":
        return (f"IC SRAM {specs['density_kbit']}KBIT {specs['organization']} "
                f"{specs['access_time_ns']}NS {package}")
    if category == "MIL-Spec Circular Connectors":
        return (f"CONN CIRCULAR {specs['contact_gender'].upper()} "
                f"{specs['contact_count']}POS SHELL {specs['shell_size']} "
                f"{specs['mil_spec']}")
    if category == "FPGAs":
        return f"IC FPGA {specs['logic_elements']} LE {specs['user_io_count']} I/O {package}"
    return f"{category.upper()} {package}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def _pick_lifecycle(rng: random.Random, age_years: float, bias: float) -> str:
    """Lifecycle follows from age, not the other way round.

    Deriving status from a part's introduction date gives the catalog a real
    correlation to exploit later: obsolete parts really are the old ones, so
    "introduced >15 years ago" and "Obsolete" reinforce each other in the Phase 6
    risk score instead of being two independent coin flips.

    The constants are tuned so the catalog lands near 25% Obsolete/EOL overall,
    which is the share the brief calls for -- those are the interesting parts.
    """
    p_dead = min(0.9, max(0.0, (age_years - 4.5) / 15.5) * bias)
    roll = rng.random()
    if roll < p_dead:
        # Obsolete is the terminal state; EOL is the announced-but-not-yet
        # stage, so EOL is the smaller slice.
        return LIFECYCLE_OBSOLETE if rng.random() < 0.62 else LIFECYCLE_EOL
    if roll < p_dead + 0.09:
        return LIFECYCLE_NRND
    return LIFECYCLE_ACTIVE


def _pick_authorized_stock(rng: random.Random, lifecycle: str) -> int:
    """Obsolete and EOL parts have no authorized stock. That is the premise of
    the secondary market, so it is a hard rule here, not a probability."""
    if lifecycle in DEAD_LIFECYCLES:
        return 0
    if lifecycle == LIFECYCLE_NRND:
        return 0 if rng.random() < 0.35 else int(rng.lognormvariate(5.5, 1.4))
    return int(rng.lognormvariate(8.2, 1.9))


def _militarize(mpn: str, category: str, rng: random.Random) -> str:
    """Give some defense-grade parts genuine military part numbering.

    Real screened parts are re-identified under the qualifying spec -- a JAN
    prefix on a diode, an M39014 slash sheet on a capacitor -- so the matcher
    has to cope with the same component appearing under two naming systems.
    """
    if category == "Zener Diodes" and mpn.startswith("1N"):
        return rng.choice(["JAN", "JANTX", "JANTXV"]) + mpn
    if category == "Fixed Capacitors":
        return f"M39014/{rng.randint(1, 22):02d}-{rng.randint(1000, 1599)}V"
    if category == "Fixed Resistors":
        return (f"RN{rng.choice(['C', 'R'])}{rng.choice(['50', '55', '60', '65'])}"
                f"{rng.choice(['H', 'J', 'K'])}{rng.randint(1000, 9999)}"
                f"{rng.choice(['F', 'B'])}{rng.choice(['SB', 'RB'])}14")
    if category in ("Logic Gates", "SRAM") and rng.random() < 0.5:
        return f"M38510/{rng.randint(30, 65)}{rng.randint(100, 999)}BCA"
    return mpn


# ---------------------------------------------------------------------------
# Part assembly
# ---------------------------------------------------------------------------

def _allocate_counts(total: int) -> dict[str, int]:
    """Split the catalog across categories by weight, remainder to the largest."""
    counts = {name: int(total * plan.weight) for name, plan in CATEGORY_PLANS.items()}
    shortfall = total - sum(counts.values())
    for name in sorted(counts, key=lambda n: -CATEGORY_PLANS[n].weight):
        if shortfall <= 0:
            break
        counts[name] += 1
        shortfall -= 1
    return counts


def _build_part(category: str, plan: CategoryPlan, rng: random.Random) -> dict:
    manufacturer = rng.choice(list(plan.builders))

    # Specs first, then the package the specs imply, then the part number built
    # from both. See the module docstring for why the order matters.
    specs = plan.specs(rng)
    package = (plan.package_for(rng, specs) if plan.package_for
               else rng.choice(plan.packages))
    mpn = plan.builders[manufacturer](rng, specs, package)

    # Age drives lifecycle drives stock. Most of the catalog is recent; the tail
    # reaching back 30 years is where the interesting obsolescence lives.
    age_years = min(32.0, max(0.4, rng.lognormvariate(1.9, 0.72)))
    date_introduced = CATALOG_EPOCH - timedelta(days=int(age_years * 365.25))
    lifecycle = _pick_lifecycle(rng, age_years, plan.obsolescence_bias)

    is_defense = rng.random() < plan.defense_ratio
    if is_defense:
        mpn = _militarize(mpn, category, rng)

    return {
        "mpn": mpn,
        "manufacturer": manufacturer,
        "category": category,
        "description": _describe(category, specs, package),
        "package": package,
        "lifecycle_status": lifecycle,
        "is_defense_grade": is_defense,
        "authorized_stock": _pick_authorized_stock(rng, lifecycle),
        "datasheet_specs": specs,
        "date_introduced": date_introduced.isoformat(),
        # Consumed by generate_price_history to pick a base price distribution.
        "price_tier": plan.price_tier,
    }


def generate_parts(count: int, seed: int) -> list[dict]:
    """Build `count` unique synthetic parts deterministically from `seed`."""
    rng = random.Random(seed)
    counts = _allocate_counts(count)
    seen: set[str] = set()
    parts: list[dict] = []

    for category, target in counts.items():
        plan = CATEGORY_PLANS[category]
        produced = 0
        attempts = 0
        # Templates collide by design -- a real line card has near-identical
        # neighbours too. Retry, then disambiguate rather than loop forever.
        while produced < target:
            attempts += 1
            part = _build_part(category, plan, rng)
            if part["mpn"] in seen:
                if attempts % 40 != 0:
                    continue
                part["mpn"] = f"{part['mpn']}{rng.choice(SUFFIX_ALPHABET)}"
                if part["mpn"] in seen:
                    continue
            seen.add(part["mpn"])
            parts.append(part)
            produced += 1

    rng.shuffle(parts)
    return parts


# ---------------------------------------------------------------------------
# Mongo load
# ---------------------------------------------------------------------------

def write_to_mongo(parts: list[dict], drop: bool = True) -> int:
    from pymongo import ASCENDING

    from app.config import mongo_client

    settings = get_settings()
    client = mongo_client()
    try:
        collection = client[settings.mongo_db]["parts"]
        if drop:
            collection.drop()

        # Chunked inserts keep the BSON batch well under the 16MB limit however
        # large datasheet_specs grows in later phases.
        inserted = 0
        chunk = 1000
        for i in range(0, len(parts), chunk):
            batch = [dict(p) for p in parts[i:i + chunk]]
            collection.insert_many(batch, ordered=False)
            inserted += len(batch)

        collection.create_index([("mpn", ASCENDING)], unique=True, name="uniq_mpn")
        collection.create_index([("category", ASCENDING)], name="by_category")
        collection.create_index([("manufacturer", ASCENDING)], name="by_manufacturer")
        collection.create_index([("lifecycle_status", ASCENDING)], name="by_lifecycle")
        # Phase 1 ranks alternates within a category across manufacturers.
        collection.create_index(
            [("category", ASCENDING), ("manufacturer", ASCENDING)],
            name="by_category_manufacturer",
        )
        return inserted
    finally:
        client.close()


def summarise(parts: list[dict]) -> str:
    total = len(parts)
    dead = sum(1 for p in parts if p["lifecycle_status"] in DEAD_LIFECYCLES)
    zero_stock = sum(1 for p in parts if p["authorized_stock"] == 0)
    defense = sum(1 for p in parts if p["is_defense_grade"])
    by_cat: dict[str, int] = {}
    for p in parts:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1

    lines = [
        f"  parts                 {total}",
        f"  obsolete or EOL       {dead} ({dead / total:.1%})",
        f"  zero authorized stock {zero_stock} ({zero_stock / total:.1%})",
        f"  defense grade         {defense} ({defense / total:.1%})",
        f"  manufacturers         {len({p['manufacturer'] for p in parts})}",
        f"  categories            {len(by_cat)}",
    ]
    for cat in sorted(by_cat, key=lambda c: -by_cat[c]):
        lines.append(f"      {cat:<32} {by_cat[cat]}")
    return "\n".join(lines)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Generate the synthetic parts catalog")
    parser.add_argument("--count", type=int, default=settings.catalog_size)
    parser.add_argument("--seed", type=int, default=settings.seed)
    parser.add_argument("--dry-run", action="store_true",
                        help="generate and summarise without touching Mongo")
    parser.add_argument("--sample", type=int, default=10,
                        help="how many example parts to print")
    args = parser.parse_args()

    print(f"generating {args.count} parts (seed={args.seed})...")
    parts = generate_parts(args.count, args.seed)
    print(summarise(parts))
    if args.sample:
        print("\n  sample:")
        for p in parts[:args.sample]:
            print(f"      {p['mpn']:<30} {p['manufacturer']:<20} "
                  f"{p['lifecycle_status']:<9} {p['description'][:52]}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    inserted = write_to_mongo(parts)
    print(f"\nwrote {inserted} parts to mongo "
          f"({settings.mongo_uri}/{settings.mongo_db}.parts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
