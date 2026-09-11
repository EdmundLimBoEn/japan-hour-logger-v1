from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from radio_logger.models import RawDecode

JA_CALLS = [
    ("JA1XYZ", "PM95"),
    ("JH8ABC", "QN03"),
    ("JR3QWE", "PM74"),
    ("7K1AAA", "PM95"),
    ("JS6BBB", "PL36"),
    ("JA0CCC", "PM50"),
    ("8J1HAM", "PM95"),
]
OTHER = [
    ("VK2ABC", "QF56", "CQ VK2ABC QF56"),
    ("YB1HDR", "OI33", "CQ YB1HDR OI33"),
    ("BY1RX", "ON80", "CQ BY1RX ON80"),
    ("HL2XYZ", "PM37", "CQ HL2XYZ PM37"),
    ("HS0ZAA", "OK03", "CQ HS0ZAA OK03"),
    ("DU1ABC", "PK04", "CQ DU1ABC PK04"),
    ("W1AW", "FN31", "CQ W1AW FN31"),
    ("K1JT", "FN20", "CQ K1JT FN20"),
    ("9V1XX", "OJ11", "CQ 9V1XX OJ11"),
    ("VR2XYZ", "OL72", "CQ VR2XYZ OL72"),
]


def simulate_decode(
    *,
    when: datetime | None = None,
    japan: bool | None = None,
    japan_bias: float = 0.25,
    dial_hz: int = 14_074_000,
    source: str = "simulator",
) -> RawDecode:
    when = when or datetime.now(tz=timezone.utc)
    pick_japan = japan if japan is not None else random.random() < japan_bias
    if pick_japan:
        call, grid = random.choice(JA_CALLS)
        if random.random() < 0.7:
            message = f"CQ {call} {grid}"
        elif random.random() < 0.5:
            message = f"9V1XX {call} {grid}"
        else:
            message = f"9V1XX {call} R-{random.randint(5, 18)}"
        snr = random.randint(-18, 4)
    else:
        call, grid, cq = random.choice(OTHER)
        message = cq if random.random() < 0.7 else f"9V1XX {call} {grid}"
        snr = random.randint(-21, 8)
    return RawDecode(
        source=source,  # type: ignore[arg-type]
        instance_id="SIM",
        decode_time_utc=when,
        snr_db=float(snr),
        dt=round(random.uniform(-0.4, 0.5), 1),
        df=random.randint(300, 2500),
        mode="FT8",
        raw_message=message,
        dial_frequency_hz=dial_hz,
        raw_payload={"sim": True, "japan": pick_japan},
    )


def japan_spike_series(
    *,
    start: datetime | None = None,
    minutes: int = 90,
    quiet_bias: float = 0.08,
    spike_bias: float = 0.65,
) -> list[RawDecode]:
    """Generate a morning-SGT-shaped series with a Japan activity spike for analytics QA."""
    start = start or datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    out: list[RawDecode] = []
    spike_start = start + timedelta(minutes=30)
    spike_end = spike_start + timedelta(minutes=45)
    t = start
    end = start + timedelta(minutes=minutes)
    seq = 0
    while t < end:
        bias = spike_bias if spike_start <= t < spike_end else quiet_bias
        count = 4 if spike_start <= t < spike_end else 2
        for i in range(count):
            when = t + timedelta(seconds=i * 3 + (seq % 7))
            decode = simulate_decode(when=when, japan_bias=bias)
            # Keep identical-call repeats as distinct rows (different time/snr).
            out.append(decode)
        t += timedelta(seconds=15)
        seq += 1
    return out
