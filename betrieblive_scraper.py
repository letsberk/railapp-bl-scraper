"""
RailApp – Betrieb Live Scraper (Playwright)
Echter Browser → kein WAF / TrafficShield Problem
"""

import os, json, asyncio, hashlib, httpx, subprocess, sys
from datetime import datetime
from playwright.async_api import async_playwright

# Chromium beim Start installieren falls nicht vorhanden
try:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
        check=True, capture_output=True)
    print("Chromium installiert ✓")
except Exception as e:
    print(f"Playwright install: {e}")

BL_URL       = "https://betrieblive.noncd.db.de"
BL_USERNAME  = os.environ.get("BL_USERNAME",  "")
BL_PASSWORD  = os.environ.get("BL_PASSWORD",  "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
POLL_SECS    = int(os.environ.get("POLL_INTERVAL", "60"))

SB = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}
_seen: set = set()

print("=" * 50)
print("  Betrieb Live Scraper (Playwright)")
print(f"  User: {BL_USERNAME}")
print(f"  Poll: {POLL_SECS}s")
print("=" * 50)
if not BL_USERNAME or not BL_PASSWORD:
    print("FEHLT: BL_USERNAME / BL_PASSWORD"); exit(1)

async def sb_exists(eid):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/bl_meldungen", headers=SB,
            params={"ext_id":f"eq.{eid}","select":"id","limit":"1"})
        try: return bool(r.json())
        except: return False

async def sb_save(p):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/bl_meldungen",
            headers=SB, content=json.dumps(p))
        return r.status_code in (200, 201)

async def sb_chat(meldung_db_id, msgs):
    for msg in msgs:
        text = str(msg.get("text") or msg.get("message") or "").strip()
        if not text: continue
        author = msg.get("author") or {}
        name   = str(author.get("name") or "Betrieb Live")
        ts     = str(msg.get("createdAt") or datetime.utcnow().isoformat())
        mid    = f"blchat_{msg.get('id','')}" or f"blchat_{abs(hash(text))}"
        if mid in _seen: continue
        _seen.add(mid)
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{SUPABASE_URL}/rest/v1/bl_kommentare", headers=SB,
                content=json.dumps({"meldung_id":meldung_db_id,"user_id":None,
                    "user_name":name,"dienstbezeichnung":name,
                    "user_role":"betrieblive","text":text[:500],"created_at":ts}))

def parse(raw):
    rid = str(raw.get("id") or raw.get("_id") or "")
    eid = f"bl_{rid}" if rid else "bl_" + hashlib.md5(str(raw).encode()).hexdigest()[:12]
    stations = raw.get("stations") or []
    bst = ", ".join(s.get("name","") for s in stations[:3]) if stations \
          else str(raw.get("title") or raw.get("name") or "Meldung")
    cat = raw.get("category") or {}
    kat = cat.get("name","") if isinstance(cat,dict) else str(cat or "")
    org = raw.get("organization") or {}
    reg = org.get("name","Allgemein") if isinstance(org,dict) else "Allgemein"
    s   = str(raw.get("status") or "").lower()
    return {
        "ext_id": eid, "typ": "betrieblive", "quelle": "Betrieb Live",
        "region": reg, "titel": bst[:500], "strecke": bst[:200],
        "beschreibung": kat, "volltext": kat[:2000],
        "von_datum": raw.get("validFrom"), "bis_datum": raw.get("validTo"),
        "prioritaet": "mittel", "status": "beendet" if "close" in s else "aktiv",
        "ersteller_name": "Betrieb Live",
        "created_at": str(raw.get("createdAt") or datetime.utcnow().isoformat()),
    }

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"],
        )
        ctx  = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width":1280,"height":800},
        )
        page = await ctx.new_page()

        print("Login...")
        await page.goto(f"{BL_URL}/#/login", wait_until="networkidle")
        await asyncio.sleep(2)

        for sel in ['input[type="text"]','input[name="name"]','#name']:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el: await el.fill(BL_USERNAME); break
            except: pass

        for sel in ['input[type="password"]','#password']:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el: await el.fill(BL_PASSWORD); break
            except: pass

        for sel in ['button[type="submit"]','button:has-text("Anmelden")']:
            try:
                el = await page.query_selector(sel)
                if el: await el.click(); break
            except: pass

        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(4)

        if "login" in page.url.lower():
            print("Login fehlgeschlagen"); await browser.close(); return
        print(f"Eingeloggt: {page.url}")

        while True:
            try:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}]")

                result = await page.evaluate("""async () => {
                    try {
                        const r = await fetch('/api/v1/incidents?limit=50&withPins=true&status=open,closed_recently');
                        const text = await r.text();
                        if (!r.ok) return {error: r.status + ' ' + text.slice(0,100)};
                        return {data: JSON.parse(text)};
                    } catch(e) { return {error: String(e)}; }
                }""")

                if "error" in result:
                    print(f"  Fehler: {result['error']}")
                    await page.reload(wait_until="networkidle")
                    await asyncio.sleep(5)
                else:
                    raw_list = result.get("data",[])
                    if isinstance(raw_list, dict):
                        raw_list = raw_list.get("incidents") or raw_list.get("data") or []
                    print(f"  {len(raw_list)} Meldungen empfangen")
                    saved = 0
                    for raw in raw_list:
                        p   = parse(raw)
                        eid = p["ext_id"]
                        if eid in _seen: continue
                        _seen.add(eid)
                        if await sb_exists(eid): continue
                        if await sb_save(p):
                            saved += 1
                            print(f"  Neu: {p['titel'][:60]}")
                            inc_id = str(raw.get("id") or raw.get("_id",""))
                            if inc_id:
                                chat = await page.evaluate(f"""async () => {{
                                    try {{
                                        const r = await fetch('/api/v1/incidents/{inc_id}/messages/');
                                        if (!r.ok) return [];
                                        return await r.json();
                                    }} catch(e) {{ return []; }}
                                }}""")
                                if chat and isinstance(chat, list):
                                    async with httpx.AsyncClient(timeout=10) as hc:
                                        mr = await hc.get(f"{SUPABASE_URL}/rest/v1/bl_meldungen",
                                            headers=SB, params={"ext_id":f"eq.{eid}","select":"id","limit":"1"})
                                        md = mr.json()
                                        if md: await sb_chat(md[0]["id"], chat)
                    print(f"  Gespeichert: {saved}")

            except Exception as e:
                print(f"Fehler: {e}")
                try: await page.reload(wait_until="networkidle"); await asyncio.sleep(5)
                except: pass

            await asyncio.sleep(POLL_SECS)

if __name__ == "__main__":
    asyncio.run(main())
