import datetime as dt
import re


def today() -> dt.date:
    return dt.date.today()


def parse_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def parse_duration(value) -> float:
    """'3:20:00', '45:30' (mm:ss) oder Sekunden -> Sekunden."""
    if value is None or value == "":
        raise ValueError("Dauer fehlt")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = [float(p) for p in text.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, (m, s) = 0, parts
    else:
        raise ValueError(f"Dauer nicht lesbar: {value!r}")
    return h * 3600 + m * 60 + s


def fmt_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(seconds_per_km: float | None) -> str | None:
    if not seconds_per_km:
        return None
    total = int(round(seconds_per_km))
    return f"{total // 60}:{total % 60:02d}"
