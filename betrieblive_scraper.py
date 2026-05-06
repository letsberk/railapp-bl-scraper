"""
RailApp – Betrieb Live Bot (kein Browser, kein Playwright)
Nur httpx – läuft auf Railway ohne extra Dependencies.

Railway Environment Variables:
  BL_USERNAME   = Knotendisponent FF
  BL_PASSWORD   = [dein Passwort]
  BL_PROFILE    = 1
  SUPABASE_URL  = https://ehmacbdsjtcnlvezdfpj.supabase.co
  SUPABASE_KEY  = sb_publishable_...
  POLL_INTERVAL = 60
"""

import os, json, asyncio, hashlib, httpx
from datetime import datetime

BL_BASE      = "https://betrieblive.noncd.db.de"   # Login + API auf gleicher Domain
BL_API_BASE  = "https://betrieblive.noncd.db.de"
BL_USERNAME  = os.environ.get("BL_USERNAME",  "")
BL_PASSWORD  = os.environ.get("BL_PASSWORD",  "")
BL_PROFILE   = int(os.environ.get("BL_PROFILE",    "1"))
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
POLL_SECS    = int(os.environ.get("POLL_INTERVAL", "60"))

# Sofortige Prüfung beim Start
print("=" * 50)
print("  Betrieb Live Bot – Startup Check")
print(f"  BL_USERNAME:  {'✓ ' + BL_USERNAME if BL_USERNAME else '❌ FEHLT'}")
print(f"  BL_PASSWORD:  {'✓ gesetzt' if BL_PASSWORD else '❌ FEHLT'}")
print(f"  SUPABASE_URL: {'✓ gesetzt' if SUPABASE_URL else '❌ FEHLT'}")
print(f"  SUPABASE_KEY: {'✓ gesetzt' if SUPABASE_KEY else '❌ FEHLT'}")
print("=" * 50)

if not BL_USERNAME or not BL_PASSWORD or not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Fehlende Environment Variables – Bot beendet")
    import sys; sys.exit(1)

SB = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}

_seen: set = set()

def decode_jwt_payload(jwt_str: str) -> str:
    """Dekodiert JWT-Payload zu JSON-String für User-Token-Data Header"""
    import base64, json
    try:
        # Token könnte base64(JWT) oder direkter JWT sein
        # Erst äußeres base64 versuchen
        try:
            raw = base64.b64decode(jwt_str + "==").decode()
            if raw.count(".") >= 2:
                jwt_str = raw  # War doppelt kodiert
        except Exception:
            pass
        # JWT Payload (mittlerer Teil)
        parts   = jwt_str.split(".")
        pad     = parts[1] + "=="
        payload = json.loads(base64.b64decode(pad).decode())
        return json.dumps(payload)
    except Exception as e:
        return jwt_str  # Fallback: raw token

# ── LOGIN ──────────────────────────────────────────────────
async def login(client: httpx.AsyncClient) -> str:
    """POST → JWT + ROLE_SELECT_TOKEN Cookie"""
    ep = "/api/v1/authentications"
    body = {"name": BL_USERNAME, "password": BL_PASSWORD, "sameSiteCookiePolicy": "lax"}
    try:
        r = await client.post(ep, json=body,
            headers={"Accept":"application/json","Content-Type":"application/json"},
            timeout=15)
        print(f"  POST {ep} -> {r.status_code}")
        if r.status_code in (200, 201):
            import json as _j
            data = r.json() if "json" in r.headers.get("content-type","") else {}
            jwt       = data.get("token") or ""
            user_id   = data.get("userId") or ""
            role_tok  = r.cookies.get("ROLE_SELECT_TOKEN","")
            print(f"  JWT: {jwt[:30]}...")
            print(f"  ROLE_SELECT_TOKEN: {role_tok[:30]}...")
            if jwt or role_tok:
                print("✅ Login OK")
                return _j.dumps({"jwt": jwt, "userId": user_id, "roleToken": role_tok})
            print(f"⚠ Kein Token in Response: {list(data.keys())}")
        else:
            print(f"  ⚠ {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"  ⚠ Login Fehler: {e}")
    return ""


# ── MELDUNGEN ABRUFEN ──────────────────────────────────────
async def fetch(client: httpx.AsyncClient, token: str) -> list:
    hdrs = {
        "Accept":      "application/json, text/plain, */*",
        "Referer":     "https://betrieblive.noncd.db.de/aktuell",
        "X-Version":   "2026.4.1",
        "Dnt":         "1",
        "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if token and token != "cookie":
        import json as _j
        try:
            td      = _j.loads(token)
            jwt     = td.get("jwt","")
            role_tok= td.get("roleToken","")

            # JWT Payload dekodieren → User-Token-Data Header
            if jwt:
                hdrs["User-Token-Data"] = decode_jwt_payload(jwt)
                print(f"  User-Token-Data: {hdrs['User-Token-Data'][:80]}")

            if role_tok:
                hdrs["Cookie"] = f"ROLE_SELECT_TOKEN={role_tok}"
        except Exception as e:
            print(f"  Token-Decode Fehler: {e}")

    # Daten kommen von noncd.db.de, Login von reisenden.info
    ep = f"{BL_API_BASE}/api/v1/incidents?limit=50&withPins=true&status=open,closed_recently"
    try:
        r = await client.get(ep, headers=hdrs, timeout=20)
        if r.status_code == 200:
            data  = r.json()
            items = data if isinstance(data, list) else data.get("incidents") or                     data.get("items") or data.get("data") or []
            print(f"  ✅ {len(items)} Meldungen")
            return items
        else:
            print(f"  ⚠ incidents: {r.status_code} – {r.text[:100]}")
    except Exception as e:
        print(f"  ⚠ fetch: {e}")
    return []


async def fetch_chat(client: httpx.AsyncClient, token: str, incident_id: str) -> list:
    """Holt Chat-Nachrichten einer Meldung"""
    hdrs = {
        "Accept":      "application/json, text/plain, */*",
        "Referer":     "https://betrieblive.noncd.db.de/aktuell",
        "X-Version":   "2026.4.1",
        "Dnt":         "1",
        "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if token and token != "cookie":
        import json as _j
        try:
            td      = _j.loads(token)
            jwt     = td.get("jwt","")
            role_tok= td.get("roleToken","")

            # JWT Payload dekodieren → User-Token-Data Header
            if jwt:
                hdrs["User-Token-Data"] = decode_jwt_payload(jwt)
                print(f"  User-Token-Data: {hdrs['User-Token-Data'][:80]}")

            if role_tok:
                hdrs["Cookie"] = f"ROLE_SELECT_TOKEN={role_tok}"
        except Exception as e:
            print(f"  Token-Decode Fehler: {e}")
    ep = f"{BL_API_BASE}/api/v1/incidents/{incident_id}/messages/"
    try:
        r = await client.get(ep, headers=hdrs, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else                    data.get("messages") or data.get("items") or []
    except Exception:
        pass
    return []

# ── SUPABASE ───────────────────────────────────────────────
async def save_chat(incident_id: str, messages: list, meldung_ext_id: str):
    """Speichert Chat-Nachrichten einer BetriebLive-Meldung"""
    # Meldungs-ID aus Supabase holen
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/bl_meldungen",
            headers=SB,
            params={"ext_id": f"eq.{meldung_ext_id}", "select": "id", "limit": "1"},
        )
        data = r.json()
        if not data: return
        meldung_id = data[0]["id"]

    saved = 0
    for msg in messages:
        text = str(msg.get("text") or msg.get("message") or msg.get("body") or msg.get("content") or "").strip()
        if not text: continue

        author   = msg.get("author") or msg.get("user") or {}
        username = str(author.get("name") or author.get("username") or
                      author.get("displayName") or author.get("fullName") or
                      msg.get("authorName") or "Betrieb Live").strip()
        dienst   = str(author.get("role") or author.get("title") or
                      author.get("organization") or "").strip()
        ts       = str(msg.get("createdAt") or msg.get("created_at") or
                      msg.get("timestamp") or datetime.utcnow().isoformat())

        # Ext-ID für Duplikat-Check
        msg_id  = str(msg.get("id") or msg.get("_id") or "")
        msg_eid = f"blchat_{msg_id}" if msg_id else f"blchat_{hash(text+ts) & 0x7FFFFFFF}"

        if msg_eid in _seen: continue
        _seen.add(msg_eid)

        payload = {
            "meldung_id":       meldung_id,
            "user_id":          None,
            "user_name":        username,
            "dienstbezeichnung": dienst or username,
            "user_role":        "betrieblive",
            "text":             text[:500],
            "created_at":       ts,
        }

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/bl_kommentare",
                headers=SB, content=json.dumps(payload),
            )
            if r.status_code in (200, 201): saved += 1

    if saved: print(f"    💬 {saved} Chat-Nachrichten gespeichert")


async def sb_exists(eid: str) -> bool:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/bl_meldungen", headers=SB,
            params={"ext_id":f"eq.{eid}","select":"id","limit":"1"})
        return bool(r.json())

async def sb_save(p: dict) -> bool:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/bl_meldungen",
            headers=SB, content=json.dumps(p))
        return r.status_code in (200, 201)

# ── PARSE ──────────────────────────────────────────────────
def parse(raw: dict) -> dict:
    """
    Betrieb Live API Felder (aus Screenshot):
      Titel/BST:   title, location, stationName, betriebsstelle, bezeichnung
      Kategorie:   category, type, stoerungsart, eventType, incidentType
      Von/Bis:     validFrom/validTo, startTime/endTime, begin/end
      Region:      region, area, leiDis, organisationUnit
      SF-Nummer:   sfNumber, stoerfall, sf
      Beschreibung:description, body, text, details
    """
    rid = str(raw.get("id") or raw.get("_id") or raw.get("sfNumber") or "")
    eid = f"bl_{rid}" if rid else "bl_" + hashlib.md5(
        (str(raw.get("title","")) + str(raw.get("created_at",""))).encode()
    ).hexdigest()[:16]

    # BetriebLive Felder (aus API-Screenshot):
    # stations = Betriebsstellen, category/subcategory = Störungsart
    # validFrom/validTo = Zeitraum, organization = Leitstelle

    # BST: stations-Array oder title
    stations = raw.get("stations") or []
    if isinstance(stations, list) and stations:
        bst = ", ".join(
            str(s.get("name") or s.get("rl100") or s.get("abbrev") or s)
            for s in stations[:3]
        )
    else:
        bst = str(
            raw.get("title") or raw.get("location") or raw.get("name") or
            raw.get("stationName") or raw.get("subject") or "Betriebsmeldung"
        )
    bst = bst[:500]

    # Kategorie: category.name + subcategory.name
    cat = raw.get("category") or {}
    subcat = raw.get("subcategory") or {}
    kat_name = str(cat.get("name") or cat if isinstance(cat, str) else "")
    sub_name = str(subcat.get("name") or subcat if isinstance(subcat, str) else "")
    kategorie = " - ".join(filter(None, [kat_name, sub_name])) or str(
        raw.get("type") or raw.get("incidentType") or raw.get("art") or ""
    )

    # Freitext
    body = str(
        raw.get("description") or raw.get("body") or raw.get("text") or
        raw.get("details") or raw.get("content") or ""
    )[:2000]

    volltext = (kategorie + "\n" + body).strip()[:2000]

    # Zeitraum
    von = str(raw.get("validFrom") or raw.get("startTime") or raw.get("begin") or "")
    bis = str(raw.get("validTo")   or raw.get("endTime")   or raw.get("end")   or "")

    # Region: organization oder leiDis
    org = raw.get("organization") or raw.get("leiDis") or {}
    region = str(
        org.get("name") if isinstance(org, dict) else org or
        raw.get("region") or raw.get("area") or "Allgemein"
    )

    # SF-Nummer
    sf = str(raw.get("sfNumber") or raw.get("number") or raw.get("sf") or "")

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

# ── MAIN ───────────────────────────────────────────────────
async def main():
    print("=" * 50)
    print("  Betrieb Live Bot")
    print(f"  User: {BL_USERNAME}")
    print(f"  Poll: {POLL_SECS}s")
    print(f"  Start: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("=" * 50)

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
                    is_new = eid not in _seen and not await sb_exists(eid)
                    _seen.add(eid)

                    if is_new:
                        if await sb_save(p):
                            saved += 1
                            print(f"  ✅ {p['titel'][:65]}")

                    # Chat-Nachrichten holen (für alle Meldungen)
                    inc_id = str(raw.get("id") or raw.get("_id") or "")
                    if inc_id and is_new:
                        msgs = await fetch_chat(client, token, inc_id)
                        if msgs:
                            await save_chat(inc_id, msgs, eid)

                print(f"  → {len(raw_list)} geladen, {saved} neu")
            except Exception as e:
                print(f"⚠ {e}")
                token = await login(client) or token
            await asyncio.sleep(POLL_SECS)

if __name__ == "__main__":
    asyncio.run(main())
