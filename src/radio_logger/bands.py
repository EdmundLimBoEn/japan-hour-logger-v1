from __future__ import annotations

# IARU Region allocations used by amateur HF/VHF FT8. Edges are inclusive Hz.
HAM_BANDS: tuple[tuple[int, int, str], ...] = (
    (135_700, 137_800, "2200m"),
    (472_000, 479_000, "630m"),
    (1_800_000, 2_000_000, "160m"),
    (3_500_000, 4_000_000, "80m"),
    (5_250_000, 5_450_000, "60m"),
    (7_000_000, 7_300_000, "40m"),
    (10_100_000, 10_150_000, "30m"),
    (14_000_000, 14_350_000, "20m"),
    (18_068_000, 18_168_000, "17m"),
    (21_000_000, 21_450_000, "15m"),
    (24_890_000, 24_990_000, "12m"),
    (28_000_000, 29_700_000, "10m"),
    (50_000_000, 54_000_000, "6m"),
    (70_000_000, 70_500_000, "4m"),
    (144_000_000, 148_000_000, "2m"),
    (222_000_000, 225_000_000, "1.25m"),
    (420_000_000, 450_000_000, "70cm"),
)

# Common WSJT-X USB dial frequencies (Hz) as a fallback when only audio DF is known.
FT8_DIALS: dict[str, int] = {
    "160m": 1_840_000,
    "80m": 3_573_000,
    "60m": 5_357_000,
    "40m": 7_074_000,
    "30m": 10_136_000,
    "20m": 14_074_000,
    "17m": 18_100_000,
    "15m": 21_074_000,
    "12m": 24_915_000,
    "10m": 28_074_000,
    "6m": 50_313_000,
    "2m": 144_174_000,
}


def band_from_hz(frequency_hz: int | float | None) -> str | None:
    if frequency_hz is None:
        return None
    freq = int(frequency_hz)
    for lo, hi, name in HAM_BANDS:
        if lo <= freq <= hi:
            return name
    return None


def signal_frequency_hz(dial_hz: int | None, audio_hz: int | None) -> int | None:
    if dial_hz is None:
        return None
    if audio_hz is None:
        return int(dial_hz)
    return int(dial_hz) + int(audio_hz)
