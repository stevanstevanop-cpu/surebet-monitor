"""
Sure Bet Monitor za oddsportal.com/sure-bets/

Periodicno otvara stranicu, izvlaci listu sure betova i salje
Windows toast notifikaciju kad se pojavi novi.

Pokretanje:
    python main.py
"""

import asyncio
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# Toaster: probaj prvo InteractableWindowsToaster (klikabilne notifikacije),
# pa fallback na obican WindowsToaster (samo prikaz teksta).
TOASTER = None
INTERACTIVE = False
try:
    from windows_toasts import Toast, WindowsToaster
    try:
        from windows_toasts import InteractableWindowsToaster
        TOASTER = InteractableWindowsToaster("Sure Bet Monitor")
        INTERACTIVE = True
    except Exception:
        TOASTER = WindowsToaster("Sure Bet Monitor")
except Exception:
    pass

BASE_URL = "https://www.oddsportal.com"
URL = BASE_URL + "/sure-bets/"
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))
HEADLESS = os.environ.get("HEADLESS", "1") != "0"
FAST_LOAD = os.environ.get("FAST_LOAD", "1") != "0"
# ONE_SHOT=1 -> izvrsi jedan ciklus i izadji (za stari cron model)
ONE_SHOT = os.environ.get("ONE_SHOT", "0") == "1"
# MAX_RUNTIME_SECONDS=N -> izadji uredno posle N sekundi (za long-running CI jobove)
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "0") or 0)

# === Filteri (env vars) ===
# MIN_PROFIT=2  -> samo arb >= 2.0%
# SPORTS=football,basketball  -> samo navedeni sportovi (case-insensitive)
# MY_BOOKMAKERS=mozzart,maxbet,pinnbet  -> samo arb gde su SVE kladionice u mojoj listi
MIN_PROFIT = float(os.environ.get("MIN_PROFIT", "0") or 0)
SPORTS = {s.strip().lower() for s in os.environ.get("SPORTS", "").split(",") if s.strip()}
MY_BOOKMAKERS = {b.strip().lower() for b in os.environ.get("MY_BOOKMAKERS", "").split(",") if b.strip()}

# === Telegram ===
# Postavi TELEGRAM_TOKEN i TELEGRAM_CHAT_ID u run.bat da bi notifikacije išle i na telefon.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

STATE_FILE = Path(__file__).parent / "seen_bets.json"
DEBUG_HTML = Path(__file__).parent / "last_page.html"
LOG_FILE = Path(__file__).parent / "monitor.log"
NOTIFY_LOG = Path(__file__).parent / "notifications.log"


_LOG_BUFFER: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _LOG_BUFFER.append(line)


def flush_log_atomic() -> None:
    """Atomicno upisi log: na disku uvek stoji kompletan poslednji ciklus."""
    try:
        LOG_FILE.write_text("\n".join(_LOG_BUFFER) + "\n", encoding="utf-8")
    except Exception:
        pass


def begin_cycle_log() -> None:
    """Krene novi ciklus — bafer se prazni, ali fajl JOS NE piše do flush_log_atomic()."""
    _LOG_BUFFER.clear()


def append_notification_history(title: str, body: str) -> None:
    """Trajna istorija notifikacija (NE brise se po ciklusu)."""
    try:
        with NOTIFY_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title} | "
                f"{body.replace(chr(10), ' / ')}\n"
            )
    except Exception:
        pass


def load_seen() -> set[str]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen", []))
        except Exception:
            return set()
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.write_text(
        json.dumps({"seen": sorted(seen)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _full_url(href: str | None) -> str | None:
    if not href:
        return None
    return href if href.startswith("http") else BASE_URL + href


def send_telegram(
    title: str,
    body: str,
    match_url: str | None,
    bookmakers: list[dict] | None = None,
) -> None:
    """Salje poruku na Telegram. Sync HTTP poziv (timeout 10s)."""
    if not TELEGRAM_ENABLED:
        return
    parts = [f"<b>{html.escape(title)}</b>", html.escape(body)]
    if bookmakers:
        parts.append("")  # prazan red pre kladionica
        parts.append("<b>Kladionice:</b>")
        for bk in bookmakers:
            bk_url = _full_url(bk.get("href"))
            name = html.escape(bk.get("name") or bk.get("slug") or "?")
            if bk_url:
                parts.append(f'• <a href="{html.escape(bk_url)}">{name}</a>')
            else:
                parts.append(f"• {name}")
    if match_url:
        parts.append("")
        parts.append(f'<a href="{html.escape(match_url)}">Otvori meč na oddsportal-u</a>')
    text = "\n".join(parts)

    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(api, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                log(f"telegram HTTP {resp.status}")
    except Exception as e:
        log(f"telegram greska: {e}")


def notify(
    title: str,
    body: str,
    url: str | None = None,
    bookmakers: list[dict] | None = None,
) -> None:
    log(f"NOTIFY: {title} | {body}")
    append_notification_history(title, body)
    match_url = _full_url(url)

    # 1) Windows toast
    if TOASTER is not None:
        try:
            toast = Toast()
            toast.text_fields = [title, body]
            if INTERACTIVE and match_url:
                try:
                    toast.launch_action = match_url
                except Exception:
                    pass
                toast.on_activated = lambda _args, _u=match_url: webbrowser.open(_u)
            TOASTER.show_toast(toast)
        except Exception as e:
            log(f"toast greska: {e}")

    # 2) Telegram (ako je konfigurisan) — sadrzi i klikabilnu listu kladionica
    send_telegram(title, body, match_url, bookmakers)


def signature(href: str) -> str:
    """Stabilan kljuc za jedan sure bet — bazira se na hrefu (ukljucujuci #fragment)."""
    norm = href.strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


async def accept_cookies(page) -> None:
    selectors = [
        "button:has-text('I Accept')",
        "button:has-text('Accept')",
        "button:has-text('Prihvatam')",
        "#onetrust-accept-btn-handler",
    ]
    for sel in selectors:
        try:
            await page.locator(sel).first.click(timeout=2000)
            log(f"prihvacen cookie banner ({sel})")
            return
        except Exception:
            pass


async def extract_bets(page) -> list[dict]:
    """
    Izvlaci sure-bet redove preko data-testid atributa koje koristi oddsportal.

    Svaki sure bet ima jedan `data-testid="game-row"` link sa hrefom u formi:
      /sport/h2h/team-a-XXX/team-b-YYY/#ARBID:bettype;line;...
    Hash deo (ARBID:bettype...) je stabilan ID arb prilike.

    Stranica zbog responsivnog rasporeda renderuje SVAKI red dvaput
    (mobile i desktop varijanta). Dedupliciramo po hrefu.
    """
    # Sacekaj da se sure-bet sekcija renderuje
    try:
        await page.wait_for_selector('[data-testid="game-row"]', timeout=20000)
    except PWTimeout:
        log("nije nadjen game-row u 20s")

    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PWTimeout:
        pass

    # Sacuvaj HTML radi debugovanja
    try:
        DEBUG_HTML.write_text(await page.content(), encoding="utf-8")
    except Exception:
        pass

    rows = await page.evaluate(
        r"""
        () => {
          const out = [];
          const seenHrefs = new Set();

          const links = document.querySelectorAll('a[data-testid="game-row"][href]');
          for (const link of links) {
            const href = link.getAttribute('href') || '';
            if (!href || seenHrefs.has(href)) continue;
            seenHrefs.add(href);

            // popni se do najblizeg "game-section" da bi obuhvatio bet-type i profit
            let scope = link;
            for (let i = 0; i < 8 && scope.parentElement; i++) {
              scope = scope.parentElement;
              if (scope.getAttribute && scope.getAttribute('data-testid') === 'game-section') break;
            }

            // timovi
            const partsEl = scope.querySelector('[data-testid="event-participants"]');
            const teamLinks = partsEl ? partsEl.querySelectorAll('a[title]') : [];
            const teams = Array.from(teamLinks).map(a => a.getAttribute('title')).filter(Boolean);
            const match = teams.length ? teams.join(' vs ') : (link.innerText || '').trim().slice(0, 80);

            // tip kvote (npr. "O/U 172.5, OT" ili "1X2")
            const betTypeEl = scope.querySelector('[data-testid="bet-type-header"]');
            const betType = betTypeEl ? betTypeEl.innerText.trim() : '';

            // sport / liga (gleda se prethodni "sport-country-league-item")
            let leagueText = '';
            let prev = scope.previousElementSibling;
            while (prev) {
              const sl = prev.querySelector && prev.querySelector('[data-testid="sport-country-league-item"]');
              if (sl) { leagueText = sl.innerText.replace(/\s+/g, ' ').trim(); break; }
              if (prev.getAttribute && prev.getAttribute('data-testid') === 'sport-country-league-item') {
                leagueText = prev.innerText.replace(/\s+/g, ' ').trim(); break;
              }
              prev = prev.previousElementSibling;
            }
            // fallback: trazi unutar parent-a (game-section ima sport-country-league-item kao sibling iznad)
            if (!leagueText) {
              const parent = scope.parentElement;
              if (parent) {
                const sl = parent.querySelector('[data-testid="sport-country-league-item"]');
                if (sl) leagueText = sl.innerText.replace(/\s+/g, ' ').trim();
              }
            }

            // profit % - trazimo zelenu oznaku u istom scope-u
            let profit = '';
            const greens = scope.querySelectorAll('p.text-green-dark, .text-green-dark');
            for (const g of greens) {
              const m = (g.innerText || '').match(/\d+\.\d+\s*%/);
              if (m) { profit = m[0].trim(); break; }
            }
            if (!profit) {
              const m = (scope.innerText || '').match(/\d+\.\d+\s*%/);
              if (m) profit = m[0].trim();
            }

            // vreme pocetka
            const dateEl = scope.querySelector('[data-testid="date-time-item"]');
            const startTime = dateEl ? dateEl.innerText.replace(/\s+/g, ' ').trim() : '';

            // kladionice koje cine arb (iz odds-box linkova)
            const bookLinks = scope.querySelectorAll('[data-testid="odds-box"] a[href*="/bookmaker/"]');
            const bookmakers = [];
            const seenBks = new Set();
            for (const a of bookLinks) {
              const bkHref = a.getAttribute('href') || '';
              const m = bkHref.match(/\/bookmaker\/([^\/]+)/);
              if (!m) continue;
              const slug = m[1].toLowerCase();
              if (seenBks.has(slug)) continue;
              seenBks.add(slug);
              const img = a.querySelector('img');
              const name = (img && img.getAttribute('alt')) || slug;
              bookmakers.push({ slug, name, href: bkHref });
            }

            out.push({
              href,
              match,
              betType,
              league: leagueText,
              profit,
              startTime,
              bookmakers,
            });
          }
          return out;
        }
        """
    )
    return rows


def bookmaker_slugs(b: dict) -> list[str]:
    return [bk["slug"] for bk in b.get("bookmakers") or [] if isinstance(bk, dict)]


def format_bet(b: dict) -> str:
    parts = []
    if b.get("profit"):
        parts.append(b["profit"])
    if b.get("match"):
        parts.append(b["match"])
    if b.get("betType"):
        parts.append(b["betType"])
    if b.get("startTime"):
        parts.append(b["startTime"].replace("\n", " "))
    slugs = bookmaker_slugs(b)
    if slugs:
        parts.append("[" + "+".join(slugs) + "]")
    return " | ".join(parts)


def parse_profit(profit_str: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", profit_str or "")
    return float(m.group(0)) if m else 0.0


def passes_filter(b: dict) -> tuple[bool, str]:
    """Vraca (prolazi_li, razlog_ako_ne)."""
    if MIN_PROFIT > 0:
        if parse_profit(b.get("profit", "")) < MIN_PROFIT:
            return False, f"profit < {MIN_PROFIT}%"
    if SPORTS:
        league = (b.get("league") or "").lower()
        sport_part = league.split("/", 1)[0].strip() if league else ""
        if not any(s == sport_part or sport_part.startswith(s) for s in SPORTS):
            return False, f"sport='{sport_part}' nije u {SPORTS}"
    if MY_BOOKMAKERS:
        bks = set(bookmaker_slugs(b))
        if not bks:
            return False, "nepoznate kladionice"
        if not bks.issubset(MY_BOOKMAKERS):
            missing = bks - MY_BOOKMAKERS
            return False, f"nemam nalog: {','.join(missing)}"
    return True, ""


async def run_once(page) -> tuple[int, int]:
    begin_cycle_log()  # bafer prazan, fajl ostaje sa prethodnim ciklusom dok ovaj ne zavrsi
    log("ucitavam stranicu...")
    await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    try:
        title = await page.title()
        log(f"page title: {title!r}")
        if any(s in title.lower() for s in ("just a moment", "attention required", "cloudflare")):
            log("UPOZORENJE: Cloudflare challenge — stranica blokira pristup")
    except Exception:
        pass
    await accept_cookies(page)
    bets = await extract_bets(page)
    log(f"pronadjeno {len(bets)} sure betova")
    if not bets:
        try:
            html_size = len(await page.content())
            n_rows = await page.locator('[data-testid="game-row"]').count()
            log(f"debug: html_size={html_size}, game-row count={n_rows}")
        except Exception as e:
            log(f"debug greska: {e}")

    # ispisi trenutnu listu da mozes da uporedis sa onim sto vidis u browseru
    for i, b in enumerate(bets, 1):
        log(f"  {i:2d}. {format_bet(b)}")

    seen = load_seen()
    new_bets = []
    filtered_out = []
    current_sigs = set()
    for b in bets:
        sig = signature(b["href"])
        current_sigs.add(sig)
        if sig in seen:
            continue
        ok, reason = passes_filter(b)
        if ok:
            new_bets.append(b)
        else:
            filtered_out.append((b, reason))

    if filtered_out:
        log(f"filtrirano: {len(filtered_out)}")
        for b, r in filtered_out[:5]:
            log(f"  - {format_bet(b)}  ({r})")

    if new_bets:
        for b in new_bets[:5]:
            title = f"Novi sure bet {b.get('profit', '')}".strip()
            body_lines = []
            if b.get("match"):
                body_lines.append(b["match"])
            tail = []
            if b.get("betType"):
                tail.append(b["betType"])
            if b.get("startTime"):
                tail.append(b["startTime"].replace("\n", " "))
            slugs = bookmaker_slugs(b)
            if slugs:
                tail.append("+".join(slugs))
            if tail:
                body_lines.append(" — ".join(tail))
            body = "\n".join(body_lines)[:200] if body_lines else "(bez detalja)"
            notify(title, body, url=b.get("href"), bookmakers=b.get("bookmakers"))
        if len(new_bets) > 5:
            notify("Sure bets", f"+ jos {len(new_bets) - 5} novih")
    else:
        log("nema novih sure betova (posle filtera)" if filtered_out else "nema novih sure betova")

    # cuvamo SAMO aktuelno + male istorije (max 500 unosa) da signature ne bubri zauvek
    merged = current_sigs | seen
    if len(merged) > 500:
        merged = current_sigs | set(list(seen)[-400:])
    save_seen(merged)
    flush_log_atomic()
    return len(bets), len(new_bets)


_BLOCK_TYPES = {"image", "media", "font"}
_BLOCK_HOST_HINTS = (
    "googletagmanager", "doubleclick", "google-analytics",
    "facebook.net", "facebook.com/tr", "scorecardresearch",
    "adservice", "adsystem", "criteo", "outbrain", "taboola",
    "hotjar", "yandex", "mc.yandex",
)


async def block_heavy_requests(route, request):
    if request.resource_type in _BLOCK_TYPES:
        await route.abort()
        return
    url = request.url.lower()
    if any(h in url for h in _BLOCK_HOST_HINTS):
        await route.abort()
        return
    await route.continue_()


async def main() -> None:
    if not NOTIFY_LOG.exists():
        NOTIFY_LOG.write_text(
            "# Istorija novih sure betova (dodaje se svaki put kad stigne notifikacija)\n",
            encoding="utf-8",
        )
    log(
        f"start | poll={POLL_SECONDS}s | headless={HEADLESS} | fast_load={FAST_LOAD} | "
        f"interactive_toasts={INTERACTIVE} | telegram={'ON' if TELEGRAM_ENABLED else 'off'} | "
        f"max_runtime={MAX_RUNTIME_SECONDS}s"
    )
    if MIN_PROFIT or SPORTS or MY_BOOKMAKERS:
        log(
            f"filteri: MIN_PROFIT={MIN_PROFIT}% | "
            f"SPORTS={','.join(sorted(SPORTS)) or '(svi)'} | "
            f"MY_BOOKMAKERS={','.join(sorted(MY_BOOKMAKERS)) or '(sve)'}"
        )
    else:
        log("filteri: nijedan (postavi MIN_PROFIT, SPORTS, MY_BOOKMAKERS u env)")
    if TOASTER is None:
        log("UPOZORENJE: windows_toasts nije dostupan, notifikacije idu samo u log")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1400, "height": 900},
        )
        if FAST_LOAD:
            await context.route("**/*", block_heavy_requests)

        page = await context.new_page()

        start_time = time.time()
        try:
            if ONE_SHOT:
                try:
                    total, new = await run_once(page)
                    log(f"ciklus gotov: ukupno={total} novih={new}")
                    flush_log_atomic()
                except Exception as e:
                    log(f"greska u ciklusu: {e}")
                    flush_log_atomic()
                    raise
                return
            while True:
                try:
                    total, new = await run_once(page)
                    log(f"ciklus gotov: ukupno={total} novih={new}")
                    flush_log_atomic()
                except Exception as e:
                    log(f"greska u ciklusu: {e}")
                    flush_log_atomic()
                if MAX_RUNTIME_SECONDS and (time.time() - start_time) >= MAX_RUNTIME_SECONDS:
                    log(f"max_runtime={MAX_RUNTIME_SECONDS}s dostignut, izlazim uredno")
                    flush_log_atomic()
                    break
                await asyncio.sleep(POLL_SECONDS)
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("zaustavljeno (Ctrl+C)")
        sys.exit(0)
