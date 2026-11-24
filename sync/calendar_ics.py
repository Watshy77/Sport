"""Lecture d'un calendrier iCalendar (webcal/https) -> créneaux occupés par jour.

Gère : lignes repliées, TZID, heures UTC (Z), journées entières, DURATION,
RRULE (via python-dateutil), EXDATE, RECURRENCE-ID (occurrences modifiées), STATUS:CANCELLED.
"""
import datetime as dt
import re
import urllib.request
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr


def fetch(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[9:]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (allure-sync)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _events(text: str):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    evs, cur, depth = [], None, 0
    for line in lines:
        u = line.strip().upper()
        if u == "BEGIN:VEVENT":
            cur, depth = {}, 0
            continue
        if cur is None:
            continue
        if u.startswith("BEGIN:"):
            depth += 1
            continue
        if u.startswith("END:"):
            if u == "END:VEVENT" and depth == 0:
                evs.append(cur)
                cur = None
            else:
                depth -= 1
            continue
        if depth or ":" not in line:
            continue
        k, v = line.split(":", 1)
        name, *params = k.split(";")
        pr = {}
        for p in params:
            if "=" in p:
                a, b = p.split("=", 1)
                pr[a.upper()] = b.strip('"')
        cur.setdefault(name.upper(), []).append((pr, v.strip()))
    return evs


def _zone(name, default):
    try:
        return ZoneInfo(name) if name else default
    except Exception:  # noqa: BLE001
        return default


def _dt(pr, v, tz):
    """-> (datetime naïf en heure locale de l'événement, zone, journée entière ?)"""
    if pr.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v[:8], "%Y%m%d"), tz, True
    if v.endswith("Z"):
        d = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc).astimezone(tz)
        return d.replace(tzinfo=None), tz, False
    return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S"), _zone(pr.get("TZID"), tz), False


def _dur(v):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def busy_days(text: str, tzname: str = "Europe/Paris", start: dt.date = None, end: dt.date = None):
    """Retourne {"AAAA-MM-JJ": [[début_min, fin_min, titre, journée_entière], ...]} en heure de tzname."""
    tz = ZoneInfo(tzname)
    start = start or dt.date.today() - dt.timedelta(days=7)
    end = end or dt.date.today() + dt.timedelta(days=28)
    lo = dt.datetime.combine(start - dt.timedelta(days=2), dt.time())
    hi = dt.datetime.combine(end + dt.timedelta(days=2), dt.time())
    evs = _events(text)

    overridden = set()
    for e in evs:
        if "RECURRENCE-ID" in e and "UID" in e:
            pr, v = e["RECURRENCE-ID"][0]
            d0, z0, _ = _dt(pr, v, tz)
            overridden.add((e["UID"][0][1], d0))

    occ = []  # (début aware, fin aware, titre, journée entière)
    for e in evs:
        if "DTSTART" not in e:
            continue
        if any(v.upper() == "CANCELLED" for _, v in e.get("STATUS", [])):
            continue
        title = (e.get("SUMMARY", [({}, "")])[0][1] or "Occupé").replace("\\,", ",").replace("\\n", " ")[:40]
        s0, z, allday = _dt(*e["DTSTART"][0], tz)
        if "DTEND" in e:
            e0, _, _ = _dt(*e["DTEND"][0], tz)
            if e["DTEND"][0][1].endswith("Z") and z is not tz:  # fin en UTC, début avec TZID
                e0 = dt.datetime.strptime(e["DTEND"][0][1][:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc).astimezone(z).replace(tzinfo=None)
            length = e0 - s0
        elif "DURATION" in e:
            length = _dur(e["DURATION"][0][1]) or dt.timedelta()
        else:
            length = dt.timedelta(days=1) if allday else dt.timedelta()
        if length <= dt.timedelta():
            length = dt.timedelta(days=1) if allday else dt.timedelta(minutes=30)
        uid = e.get("UID", [({}, "")])[0][1]

        starts = [s0]
        if "RRULE" in e:
            rule = e["RRULE"][0][1]
            m = re.search(r"UNTIL=(\d{8}(?:T\d{6}Z?)?)", rule)
            if m:  # UNTIL ramené en heure locale naïve, sinon dateutil refuse le mélange
                u = m.group(1)
                if len(u) == 8:
                    nu = u + "T235959"
                elif u.endswith("Z"):
                    nu = dt.datetime.strptime(u[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc).astimezone(z).strftime("%Y%m%dT%H%M%S")
                else:
                    nu = u
                rule = rule.replace("UNTIL=" + u, "UNTIL=" + nu)
            try:
                starts = list(rrulestr(rule, dtstart=s0).between(lo - length, hi, inc=True))
            except Exception:  # noqa: BLE001
                starts = [s0]
            ex = set()
            for pr, v in e.get("EXDATE", []):
                for part in v.split(","):
                    d, _, _ = _dt(pr, part, z)
                    ex.add(d)
                    ex.add(d.replace(hour=0, minute=0, second=0))
            starts = [s for s in starts if s not in ex and s.replace(hour=0, minute=0, second=0) not in ex and (uid, s) not in overridden]

        for s in starts:
            if allday:
                occ.append((s, s + length, title, True))
            else:
                a = s.replace(tzinfo=z).astimezone(tz).replace(tzinfo=None)
                occ.append((a, a + length, title, False))

    days = {}
    for s, e, title, allday in occ:
        if e <= lo or s >= hi:
            continue
        d = s.date()
        while dt.datetime.combine(d, dt.time()) < e:
            if start <= d <= end:
                d0 = dt.datetime.combine(d, dt.time())
                a = max(0, int((s - d0).total_seconds() // 60))
                b = min(1440, int((e - d0).total_seconds() // 60))
                if b > a:
                    days.setdefault(d.isoformat(), []).append([a, b, title, 1 if allday else 0])
            d += dt.timedelta(days=1)
    for v in days.values():
        v.sort()
    return days
