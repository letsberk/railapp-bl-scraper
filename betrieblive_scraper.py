“””
RailApp – Betrieb Live Bot (kein Browser, kein Playwright)
Nur httpx – läuft auf Railway ohne extra Dependencies.

Railway Environment Variables:
BL_USERNAME   = Knotendisponent FF
BL_PASSWORD   = [dein Passwort]
BL_PROFILE    = 1
SUPABASE_URL  = https://ehmacbdsjtcnlvezdfpj.supabase.co
SUPABASE_KEY  = sb_publishable_…
POLL_INTERVAL = 60
“””

import os, json, asyncio, hashlib, httpx
from datetime import datetime

BL_BASE      = “https://betrieblive.reisenden.info”
BL_USERNAME  = os.environ[“BL_USERNAME”]
BL_PASSWORD  = os.environ[“BL_PASSWORD”]
BL_PROFILE   = int(os.environ.get(“BL_PROFILE”,    “1”))
SUPABASE_URL = os.environ[“SUPABASE_URL”]
SUPABASE_KEY = os.environ[“SUPABASE_KEY”]
POLL_SECS    = int(os.environ.get(“POLL_INTERVAL”, “60”))

SB = {
“apikey”:        SUPABASE_KEY,
“Authorization”: f”Bearer {SUPABASE_KEY}”,
“Content-Type”:  “application/json”,
“Prefer”:        “return=minimal”,
}

_seen: set = set()

# ── LOGIN ──────────────────────────────────────────────────

async def login(client: httpx.AsyncClient) -> str:
ep = “/api/v1/authentications”
bodies = [
{“username”: BL_USERNAME, “password”: BL_PASSWORD},
{“email”:    BL_USERNAME, “password”: BL_PASSWORD},
{“login”:    BL_USERNAME, “password”: BL_PASSWORD},
{“authentication”: {“username”: BL_USERNAME, “password”: BL_PASSWORD}},
{“authentication”: {“email”:    BL_USERNAME, “password”: BL_PASSWORD}},
{“user”: {“username”: BL_USERNAME, “password”: BL_PASSWORD}},
]
for body in bodies:
try:
r = await client.post(ep, json=body,
headers={“Accept”:“application/json”,“Content-Type”:“application/json”},
timeout=15)
print(f”  POST {ep} [{list(body.keys())[0]}] -> {r.status_code}”)
if r.status_code in (200, 201):
ct   = r.headers.get(“content-type”,””)
data = r.json() if “json” in ct else {}
token = (data.get(“token”) or data.get(“access_token”) or
data.get(“accessToken”) or data.get(“jwt”) or
data.get(“authToken”) or data.get(“auth_token”) or “”)
if token:
print(f”✅ Login OK – Token”)
return str(token)
auth_hdr = r.headers.get(“Authorization”,””) or r.headers.get(“X-Auth-Token”,””)
if auth_hdr:
print(f”✅ Login OK – Header-Token”)
return auth_hdr.replace(“Bearer “,””)
print(f”✅ Login OK – Cookie/Session”)
return “cookie”
elif r.status_code == 422:
print(f”  422: {r.text[:120]}”)
elif r.status_code == 401:
print(f”  401: falsches Format”)
except Exception as e:
print(f”  Fehler: {e}”)
return “”

# ── MELDUNGEN ABRUFEN ──────────────────────────────────────

async def fetch(client: httpx.AsyncClient, token: str) -> list:
hdrs = {“Accept”: “application/json”}
if token and token != “cookie”:
hdrs[“Authorization”] = f”Bearer {token}”

```
for ep in [
    f"/api/meldungen?profil={BL_PROFILE}",
    f"/api/incidents?profile={BL_PROFILE}",
    f"/api/messages?filter={BL_PROFILE}",
    f"/api/feed?profil={BL_PROFILE}",
    "/api/meldungen", "/api/incidents",
    "/api/messages",  "/api/feed",
    "/api/v1/meldungen", "/api/stoerungen",
    "/api/events",    "/api/notifications",
]:
    try:
        r = await client.get(ep, headers=hdrs, timeout=15)
        if r.status_code != 200: continue
        if "json" not in r.headers.get("content-type",""): continue
        data  = r.json()
        items = data if isinstance(data, list) else next(
            (data[k] for k in ["items","data","meldungen","messages",
                                "incidents","results","feed","entries"]
             if isinstance(data.get(k), list)), [])
        if items:
            print(f"  ✅ {len(items)} Meldungen via {ep}")
            return items
    except Exception:
        pass

print("  ⚠ Kein API-Endpoint gefunden – bitte API-URL ermitteln")
print("    → Chrome → F12 → Network → Einloggen → XHR/Fetch URLs kopieren")
return []
```

# ── SUPABASE ───────────────────────────────────────────────

async def sb_exists(eid: str) -> bool:
async with httpx.AsyncClient(timeout=10) as c:
r = await c.get(f”{SUPABASE_URL}/rest/v1/bl_meldungen”, headers=SB,
params={“ext_id”:f”eq.{eid}”,“select”:“id”,“limit”:“1”})
return bool(r.json())

async def sb_save(p: dict) -> bool:
async with httpx.AsyncClient(timeout=15) as c:
r = await c.post(f”{SUPABASE_URL}/rest/v1/bl_meldungen”,
headers=SB, content=json.dumps(p))
return r.status_code in (200, 201)

# ── PARSE ──────────────────────────────────────────────────

def parse(raw: dict) -> dict:
“””
Betrieb Live API Felder (aus Screenshot):
Titel/BST:   title, location, stationName, betriebsstelle, bezeichnung
Kategorie:   category, type, stoerungsart, eventType, incidentType
Von/Bis:     validFrom/validTo, startTime/endTime, begin/end
Region:      region, area, leiDis, organisationUnit
SF-Nummer:   sfNumber, stoerfall, sf
Beschreibung:description, body, text, details
“””
rid = str(raw.get(“id”) or raw.get(”*id”) or raw.get(“sfNumber”) or “”)
eid = f”bl*{rid}” if rid else “bl_” + hashlib.md5(
(str(raw.get(“title”,””)) + str(raw.get(“created_at”,””))).encode()
).hexdigest()[:16]

```
# BST / Betriebsstelle als Titel
bst = str(
    raw.get("title") or raw.get("location") or raw.get("stationName") or
    raw.get("betriebsstelle") or raw.get("bezeichnung") or
    raw.get("name") or raw.get("subject") or "Betriebsmeldung"
)[:500]

# Störungsart / Kategorie als Beschreibung
kategorie = str(
    raw.get("category") or raw.get("type") or raw.get("stoerungsart") or
    raw.get("eventType") or raw.get("incidentType") or raw.get("art") or ""
)

# Freitext
body = str(
    raw.get("description") or raw.get("body") or raw.get("text") or
    raw.get("details") or raw.get("content") or ""
)[:2000]

# Volltext = Kategorie + Freitext kombiniert für Suche
volltext = (kategorie + "\n" + body).strip()[:2000]

# Zeitraum
von = str(
    raw.get("validFrom") or raw.get("startTime") or raw.get("begin") or
    raw.get("start") or raw.get("vonDatum") or raw.get("from") or ""
)
bis = str(
    raw.get("validTo") or raw.get("endTime") or raw.get("end") or
    raw.get("bis") or raw.get("bisDatum") or raw.get("to") or ""
)

# Region / Leitstelle
region = str(
    raw.get("region") or raw.get("area") or raw.get("leiDis") or
    raw.get("organisationUnit") or raw.get("location") or "Allgemein"
)

# SF-Nummer
sf = str(raw.get("sfNumber") or raw.get("stoerfall") or raw.get("sf") or "")

# Status
s = str(raw.get("status") or raw.get("state") or "").lower()
status = "beendet" if any(x in s for x in ["close","end","beend","resolve"])         else "update"  if any(x in s for x in ["update","zwischen"])         else "aktiv"

# Priorität
combined = (bst + kategorie).lower()
if any(x in combined for x in ["sperrung","unfall","gross","vollsp"]):
    prio = "hoch"
elif any(x in combined for x in ["stoerung","storung","einschr","signal","bu","bue"]):
    prio = "mittel"
else:
    prio = "normal"

ts = str(
    raw.get("created_at") or raw.get("timestamp") or raw.get("createdAt") or
    raw.get("date") or datetime.utcnow().isoformat()
)

return {
    "ext_id":       eid,
    "typ":          "betrieblive",
    "quelle":       "Betrieb Live",
    "region":       region,
    "titel":        bst,           # BST als Titel
    "strecke":      bst,           # auch als Strecke
    "beschreibung": kategorie,     # Störungsart als Beschreibung
    "volltext":     volltext,
    "von_datum":    von if von else None,
    "bis_datum":    bis if bis else None,
    "prioritaet":   prio,
    "status":       status,
    "ersteller_name": f"Betrieb Live{' · SF ' + sf if sf else ''}",
    "created_at":   ts,
}
```

# ── MAIN ───────────────────────────────────────────────────

async def main():
print(”=” * 50)
print(”  Betrieb Live Bot”)
print(f”  User: {BL_USERNAME}”)
print(f”  Poll: {POLL_SECS}s”)
print(f”  Start: {datetime.now().strftime(’%d.%m.%Y %H:%M:%S’)}”)
print(”=” * 50)

```
async with httpx.AsyncClient(base_url=BL_BASE, follow_redirects=True) as client:
    token = await login(client)
    if not token:
        print("❌ Login fehlgeschlagen – Zugangsdaten prüfen")
        return

    while True:
        try:
            print(f"\n🔄 [{datetime.now().strftime('%H:%M:%S')}]")
            raw_list = await fetch(client, token)
            saved = 0
            for raw in raw_list:
                p = parse(raw); eid = p["ext_id"]
                if eid in _seen: continue
                _seen.add(eid)
                if await sb_exists(eid): continue
                if await sb_save(p):
                    saved += 1
                    print(f"  ✅ {p['titel'][:65]}")
            print(f"  → {len(raw_list)} geladen, {saved} neu")
        except Exception as e:
            print(f"⚠ {e}")
            token = await login(client) or token
        await asyncio.sleep(POLL_SECS)
```

if **name** == “**main**”:
asyncio.run(main())
