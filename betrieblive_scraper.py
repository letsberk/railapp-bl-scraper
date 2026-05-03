"""
RailApp – Betrieb Live Scraper
Läuft 24/7 auf Railway.app

Railway Environment Variables (KEINE Credentials im Code!):
  BL_USERNAME   = Knotendisponent FF
  BL_PASSWORD   = [Railway Secret]
  BL_PROFILE    = 1
  SUPABASE_URL  = https://ehmacbdsjtcnlvezdfpj.supabase.co
  SUPABASE_KEY  = sb_publishable_...
  POLL_INTERVAL = 60
"""

import os, json, asyncio, hashlib, httpx
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BL_URL        = "https://betrieblive.reisenden.info"
BL_USERNAME   = os.environ["BL_USERNAME"]
BL_PASSWORD   = os.environ["BL_PASSWORD"]
BL_PROFILE    = int(os.environ.get("BL_PROFILE",    "1"))
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}

_seen: set = set()

# ── SUPABASE ────────────────────────────────────────────────

async def sb_exists(ext_id: str) -> bool:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/bl_meldungen",
            headers=SB_HEADERS,
            params={"ext_id": f"eq.{ext_id}", "select": "id", "limit": "1"},
        )
        return bool(r.json())

async def sb_save(payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{SUPABASE_URL}/rest/v1/bl_meldungen",
            headers=SB_HEADERS,
            content=json.dumps(payload),
        )
        if r.status_code in (200, 201):
            return True
        if "duplicate" in r.text.lower() or "unique" in r.text.lower():
            return False
        print(f"⚠ Supabase {r.status_code}: {r.text[:150]}")
        return False

# ── PARSE ────────────────────────────────────────────────────

def ext_id(raw: dict) -> str:
    rid = str(raw.get("id") or raw.get("_id") or raw.get("messageId") or "")
    if rid:
        return f"bl_{rid}"
    return "bl_" + hashlib.md5(
        (str(raw.get("title","")) + str(raw.get("created_at",""))).encode()
    ).hexdigest()[:16]

def parse(raw: dict) -> dict:
    eid = ext_id(raw)
    title = str(raw.get("title") or raw.get("subject") or raw.get("headline") or "Betriebsmeldung")[:500]
    body  = str(raw.get("body")  or raw.get("description") or raw.get("content") or raw.get("text") or "")[:2000]
    s     = str(raw.get("status") or raw.get("state") or "").lower()
    status = "beendet" if any(x in s for x in ["close","end","beend"]) \
        else "update"  if any(x in s for x in ["update","zwischen"]) \
        else "aktiv"
    combined = (title + str(raw.get("category",""))).lower()
    prio = "hoch"   if any(x in combined for x in ["sperrung","unfall","großstörung"]) \
      else "mittel" if any(x in combined for x in ["störung","einschränkung","signal"]) \
      else "normal"
    ts = str(raw.get("created_at") or raw.get("timestamp") or raw.get("date") or datetime.utcnow().isoformat())
    return {
        "ext_id":       eid,
        "typ":          "betrieblive",
        "quelle":       "Betrieb Live",
        "region":       str(raw.get("region") or raw.get("location") or "Allgemein"),
        "titel":        title,
        "volltext":     body,
        "strecke":      str(raw.get("route") or raw.get("line") or raw.get("track") or ""),
        "beschreibung": body[:300],
        "prioritaet":   prio,
        "status":       status,
        "ersteller_name": "Betrieb Live",
        "created_at":   ts,
    }

# ── SCRAPER ──────────────────────────────────────────────────

class Scraper:

    def __init__(self):
        self.page = None
        self.buf  = []

    async def init(self, pw):
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            viewport={"width": 390, "height": 844},
        )
        self.page = await ctx.new_page()
        self.page.set_default_timeout(30_000)

    async def _capture(self, resp):
        """Fängt JSON API-Antworten ab"""
        if "json" not in resp.headers.get("content-type",""):
            return
        url = resp.url.lower()
        if not any(k in url for k in ["meldung","incident","message","stoerung",
                                       "feed","notification","event","alert"]):
            return
        try:
            data = await resp.json()
            items = data if isinstance(data, list) else next(
                (data[k] for k in ["items","data","messages","incidents",
                                    "meldungen","results","entries","notifications"]
                 if isinstance(data.get(k), list)), []
            )
            if items:
                self.buf.extend(items)
                print(f"  📡 {len(items)} via API ({resp.url[:70]})")
        except Exception:
            pass

    async def login(self) -> bool:
        print(f"🔐 Login '{BL_USERNAME}'…")
        self.page.on("response", self._capture)
        await self.page.goto(BL_URL, wait_until="networkidle", timeout=45_000)
        await asyncio.sleep(2)

        # Felder befüllen
        for sel in ['input[type="text"]','input[name="username"]',
                    'input[placeholder*="Name"]','#username']:
            try:
                el = await self.page.wait_for_selector(sel, timeout=3000)
                if el:
                    await el.fill(BL_USERNAME); break
            except: pass

        for sel in ['input[type="password"]','#password']:
            try:
                el = await self.page.wait_for_selector(sel, timeout=3000)
                if el:
                    await el.fill(BL_PASSWORD); break
            except: pass

        for sel in ['button[type="submit"]','button:has-text("Anmelden")',
                    'button:has-text("Login")','input[type="submit"]']:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.click(); break
            except: pass

        await self.page.wait_for_load_state("networkidle", timeout=15_000)
        await asyncio.sleep(3)

        ok = "login" not in self.page.url.lower()
        print(f"{'✅ Eingeloggt' if ok else '❌ Login fehlgeschlagen'} – {self.page.url}")
        return ok

    async def select_profile(self):
        """Aktiviert Filterprofil 1"""
        print(f"🎛 Filterprofil {BL_PROFILE}…")
        for sel in [
            f'[data-profile="{BL_PROFILE}"]',
            f'button:has-text("Profil {BL_PROFILE}")',
            f'li:has-text("Profil {BL_PROFILE}")',
            f'a:has-text("Profil {BL_PROFILE}")',
            '.filter-profile', '.profile-tab',
        ]:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.click()
                    await asyncio.sleep(2)
                    print(f"  ✓ Profil via '{sel}'")
                    return
            except: pass
        print("  ℹ Profil-Selector nicht gefunden – nutze aktuellen Feed")

    async def poll(self) -> list:
        self.buf.clear()
        try:
            await self.page.reload(wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(4)
        except Exception as e:
            print(f"  ⚠ Reload: {e}")

        if self.buf:
            return list(self.buf)

        # DOM-Fallback
        print("  ⚙ Kein API-Feed, DOM-Scraping…")
        for sel in [".meldung",".incident","[class*='meldung']",
                    "[class*='incident']",".feed-item","article"]:
            try:
                els = await self.page.query_selector_all(sel)
                if not els: continue
                items = []
                for el in els[:60]:
                    try:
                        t = (await el.inner_text()).strip()
                        if len(t) > 20:
                            items.append({
                                "id": abs(hash(t)) % 2**31,
                                "title": t[:120], "body": t,
                                "created_at": datetime.utcnow().isoformat(),
                            })
                    except: pass
                if items:
                    print(f"  → {len(items)} via DOM")
                    return items
            except: pass
        return []


# ── MAIN ─────────────────────────────────────────────────────

async def main():
    print("=" * 55)
    print("  RailApp – Betrieb Live Scraper")
    print(f"  User:    {BL_USERNAME}")
    print(f"  Profil:  {BL_PROFILE}")
    print(f"  Interval:{POLL_INTERVAL}s")
    print(f"  Start:   {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print("=" * 55)

    errs = 0
    async with async_playwright() as pw:
        s = Scraper()
        await s.init(pw)

        if not await s.login():
            print("❌ Login fehlgeschlagen"); return

        await s.select_profile()

        while True:
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"\n🔄 [{ts}]")
                raw_list = await s.poll()
                saved = 0

                for raw in raw_list:
                    try:
                        p  = parse(raw)
                        eid= p["ext_id"]
                        if eid in _seen: continue
                        _seen.add(eid)
                        if await sb_exists(eid): continue
                        if await sb_save(p):
                            saved += 1
                            print(f"  ✅ {p['titel'][:65]}")
                    except Exception as e:
                        print(f"  ⚠ {e}")

                print(f"  → {len(raw_list)} geladen, {saved} neu")
                errs = 0

            except PWTimeout:
                errs += 1
                print(f"⚠ Timeout – Re-Login…")
                await s.login()
                await s.select_profile()
            except Exception as e:
                errs += 1
                print(f"⚠ Fehler {errs}: {e}")
                if errs >= 5:
                    await s.login()
                    await s.select_profile()
                    errs = 0

            await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
