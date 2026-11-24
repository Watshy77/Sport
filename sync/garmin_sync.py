"""Synchronise Garmin Connect -> data/ chiffré, lu par l'appli Allure.

Utilise python-garminconnect (la bibliothèque de garmin_mcp) et le jeton créé par
`garmin-mcp-auth` (~/.garminconnect/garmin_tokens.json).

Variables d'environnement :
  SYNC_PASSPHRASE   phrase secrète de chiffrement (obligatoire, la même que dans l'appli)
  GARMIN_TOKENS     contenu de ~/.garminconnect/garmin_tokens.json (ou sa version base64)
  GARMIN_EMAIL / GARMIN_PASSWORD   secours si pas de jeton (ne marche pas avec la double authentification)
  SYNC_DAYS         historique d'activités au premier passage (défaut 365)
  WELLNESS_DAYS     historique santé au premier passage (défaut 42)
  MAX_DOWNLOADS     fichiers d'activité téléchargés par passage (défaut 120)
"""
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DATA = Path(os.environ.get("DATA_DIR", "data"))
ITER = 250_000
SPORT_FILES = ("running", "cycling", "biking", "ride", "treadmill", "trail", "track", "gravel", "mountain", "virtual")


def log(*a):
    print(*a, flush=True)


# ---------- chiffrement (format lu par l'appli : iv(12) + ciphertext AES-GCM) ----------
def get_key(passphrase: str) -> bytes:
    kf = DATA / "key.json"
    if kf.exists():
        meta = json.loads(kf.read_text())
    else:
        meta = {"v": 1, "salt": base64.b64encode(os.urandom(16)).decode(), "iter": ITER}
        DATA.mkdir(parents=True, exist_ok=True)
        kf.write_text(json.dumps(meta))
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(meta["salt"]), iterations=meta["iter"])
    return kdf.derive(passphrase.encode("utf-8"))


def enc(key: bytes, data: bytes) -> bytes:
    iv = os.urandom(12)
    return iv + AESGCM(key).encrypt(iv, data, None)


def dec(key: bytes, blob: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


# ---------- connexion Garmin ----------
def login():
    from garminconnect import Garmin

    tok = os.environ.get("GARMIN_TOKENS", "").strip()
    store = Path.home() / ".garminconnect"
    if tok:
        if not tok.startswith("{"):
            try:
                decoded = base64.b64decode(tok).decode("utf-8")
                if decoded.strip().startswith("{"):
                    tok = decoded
            except Exception:
                pass
        store.mkdir(mode=0o700, exist_ok=True)
        (store / "garmin_tokens.json").write_text(tok)
        try:
            g = Garmin()
            g.login(str(store))
            log("Connexion Garmin : jeton OK")
            return g
        except Exception as e:  # noqa: BLE001
            log(f"Jeton refusé ({e}).")
    email, pw = os.environ.get("GARMIN_EMAIL"), os.environ.get("GARMIN_PASSWORD")
    if email and pw:
        g = Garmin(email, pw)
        g.login()
        log("Connexion Garmin : identifiants OK")
        return g
    sys.exit("Aucun jeton valide. Relance `garmin-mcp-auth --force-reauth` puis mets à jour le secret GARMIN_TOKENS.")


# ---------- utilitaires ----------
def dig(o, *path):
    for p in path:
        if o is None:
            return None
        if isinstance(p, int):
            o = o[p] if isinstance(o, list) and len(o) > p else None
        else:
            o = o.get(p) if isinstance(o, dict) else None
    return o


def num(v):
    try:
        return None if v is None else round(float(v), 1)
    except (TypeError, ValueError):
        return None


def sport_of(type_key: str) -> str:
    t = (type_key or "").lower()
    if any(k in t for k in ("running", "treadmill", "trail", "track_run")):
        return "run"
    if any(k in t for k in ("cycling", "biking", "ride", "gravel", "mountain_bik", "virtual_ride")):
        return "bike"
    return "other"


def to_ms(gmt: str) -> int:
    d = dt.datetime.strptime(gmt[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def summary(a: dict) -> dict:
    tk = dig(a, "activityType", "typeKey") or ""
    s = {
        "sport": sport_of(tk),
        "start": to_ms(a.get("startTimeGMT") or a.get("startTimeLocal")),
        "dur": num(a.get("movingDuration") or a.get("duration")),
        "dist": num(a.get("distance")),
        "avgHr": num(a.get("averageHR")),
        "maxHr": num(a.get("maxHR")),
        "avgPw": num(a.get("avgPower")),
        "np": num(a.get("normPower")),
        "elev": num(a.get("elevationGain")),
        "kcal": num(a.get("calories")),
        "title": (a.get("activityName") or "")[:80],
        "type": tk,
        "src": "garmin",
    }
    zones = [a.get(f"hrTimeInZone_{i}") for i in range(1, 6)]
    if any(zones):
        s["gz"] = [num(z) or 0 for z in zones]
    return {k: v for k, v in s.items() if v not in (None, "")}


def safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log(f"  ! {getattr(fn, '__name__', fn)}{args}: {e}")
        return None


def wellness_day(g, day: str) -> dict:
    us = safe(g.get_user_summary, day) or {}
    sl = safe(g.get_sleep_data, day) or {}
    hv = safe(g.get_hrv_data, day) or {}
    tr = safe(g.get_training_readiness, day)
    mm = safe(g.get_max_metrics, day)
    tr0 = tr[0] if isinstance(tr, list) and tr else tr if isinstance(tr, dict) else {}
    mm0 = mm[0] if isinstance(mm, list) and mm else mm if isinstance(mm, dict) else {}
    w = {
        "rhr": num(us.get("restingHeartRate")),
        "steps": num(us.get("totalSteps")),
        "stress": num(us.get("averageStressLevel")),
        "bbHi": num(us.get("bodyBatteryHighestValue")),
        "bbLo": num(us.get("bodyBatteryLowestValue")),
        "bbWake": num(us.get("bodyBatteryAtWakeTime")),
        "sleep": num(dig(sl, "dailySleepDTO", "sleepScores", "overall", "value")),
        "sleepH": num((dig(sl, "dailySleepDTO", "sleepTimeSeconds") or 0) / 3600) or None,
        "hrv": num(dig(hv, "hrvSummary", "lastNightAvg")),
        "hrvWeek": num(dig(hv, "hrvSummary", "weeklyAvg")),
        "hrvStatus": dig(hv, "hrvSummary", "status"),
        "ready": num(dig(tr0, "score")),
        "vo2run": num(dig(mm0, "generic", "vo2MaxPreciseValue") or dig(mm0, "generic", "vo2MaxValue")),
        "vo2bike": num(dig(mm0, "cycling", "vo2MaxPreciseValue") or dig(mm0, "cycling", "vo2MaxValue")),
    }
    return {k: v for k, v in w.items() if v not in (None, "", 0)}


def download(g, aid):
    from garminconnect import Garmin

    fmt = getattr(getattr(Garmin, "ActivityDownloadFormat", None), "ORIGINAL", None)
    return g.download_activity(aid, dl_fmt=fmt) if fmt is not None else g.download_activity(aid)


# ---------- programme ----------
def main(g=None):
    pw = os.environ.get("SYNC_PASSPHRASE")
    if not pw or len(pw) < 8:
        sys.exit("SYNC_PASSPHRASE manquante ou trop courte (8 caractères minimum).")
    key = get_key(pw)
    (DATA / "a").mkdir(parents=True, exist_ok=True)
    mf = DATA / "manifest.bin"
    man = json.loads(dec(key, mf.read_bytes())) if mf.exists() else {"acts": {}, "well": {}}
    before = json.dumps({k: v for k, v in man.items() if k != "updated"}, sort_keys=True)
    salt = json.loads((DATA / "key.json").read_text())["salt"]
    g = g or login()
    today = dt.date.today()

    # activités
    if man["acts"]:
        last = max(v["sum"]["start"] for v in man["acts"].values())
        start = dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc).date() - dt.timedelta(days=3)
    else:
        start = today - dt.timedelta(days=int(os.environ.get("SYNC_DAYS", "365")))
    acts = safe(g.get_activities_by_date, start.isoformat(), today.isoformat()) or []
    log(f"{len(acts)} activités depuis {start}")
    for a in acts:
        aid = str(a.get("activityId"))
        if not aid or aid == "None":
            continue
        e = man["acts"].setdefault(aid, {})
        try:
            e["sum"] = summary(a)
        except Exception as ex:  # noqa: BLE001
            log(f"  ! résumé {aid}: {ex}")
            man["acts"].pop(aid, None)
            continue
        if any(k in (e["sum"].get("type") or "") for k in SPORT_FILES):
            e.setdefault("file", None)

    budget = int(os.environ.get("MAX_DOWNLOADS", "120"))
    todo = sorted((k for k, v in man["acts"].items() if "file" in v and not v["file"]), key=lambda k: -man["acts"][k]["sum"]["start"])
    for aid in todo[:budget]:
        b = safe(download, g, int(aid))
        if b:
            name = hashlib.sha256((salt + aid).encode()).hexdigest()[:20] + ".bin"
            (DATA / "a" / name).write_bytes(enc(key, bytes(b)))
            man["acts"][aid]["file"] = name
            log(f"  + fichier {aid} ({len(b) // 1024} Ko)")
        time.sleep(0.5)
    if len(todo) > budget:
        log(f"{len(todo) - budget} fichiers restants : ils viendront aux prochains passages.")

    # santé
    n = 3 if man["well"] else int(os.environ.get("WELLNESS_DAYS", "42"))
    for i in range(n):
        day = (today - dt.timedelta(days=i)).isoformat()
        w = wellness_day(g, day)
        if w:
            man["well"][day] = w
        time.sleep(0.2)

    if mf.exists() and json.dumps({k: v for k, v in man.items() if k != "updated"}, sort_keys=True) == before:
        log("Rien de nouveau.")
        return
    man["updated"] = int(time.time() * 1000)
    mf.write_bytes(enc(key, json.dumps(man, separators=(",", ":")).encode()))
    log(f"OK : {len(man['acts'])} activités, {len(man['well'])} jours de santé.")


if __name__ == "__main__":
    main()
