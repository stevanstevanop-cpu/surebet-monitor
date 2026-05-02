# Sure Bet Monitor

Prati `oddsportal.com/sure-bets/` i salje **klikabilnu** Windows toast notifikaciju
kad god se pojavi novi sure bet. Klik na notifikaciju otvara strancu meca u browseru.

## Pokretanje (prvi put)

1. Instaliraj Python 3.10+ sa https://www.python.org/downloads/ (cekiraj **Add to PATH**)
2. Dupli klik na `setup.bat` — instalira pakete + Chromium za Playwright
3. Dupli klik na `run.bat` (ili `watchdog.bat` — vidi nize)

## Auto-start sa Windows-om

- `register-startup.bat` — pokreni jednom; monitor ce se startovati pri svakom logovanju u Windows
- `unregister-startup.bat` — skida iz auto-start-a
- `watchdog.bat` — pokrece monitor i automatski restart-uje ako pukne (preporuceno za auto-start)

## Filteri

Sve preko env varijabli (mozes ih staviti u `run.bat` pre `python main.py`):

| varijabla | opis | primer |
|---|---|---|
| `POLL_SECONDS` | razmak izmedju ciklusa | `30` |
| `HEADLESS` | `0` da vidis browser, `1` skriveno | `1` |
| `FAST_LOAD` | blokira slike/fontove/oglase za bržu stranicu | `1` |
| `MIN_PROFIT` | minimalni profit % za notifikaciju | `2` |
| `SPORTS` | dozvoljeni sportovi (zarez razdvojeno, lowercase) | `football,basketball` |
| `MY_BOOKMAKERS` | samo arb gde su SVE kladionice u listi | `mozzart,maxbet,pinnbet` |

Primer (`run.bat`):

```bat
@echo off
call .venv\Scripts\activate.bat
set POLL_SECONDS=30
set MIN_PROFIT=2
set SPORTS=football,basketball
set MY_BOOKMAKERS=mozzart,maxbet,pinnbet
python main.py
```

Da nadjes ime kladionice za `MY_BOOKMAKERS`: pogledaj `monitor.log` — uz svaki bet stoji
`[mozzart+maxbet]` u zagradama. Koristi tacno te slug-ove.

## Klikabilne notifikacije

Toast notifikacije imaju ugradjenu `launch_action` koja na klik otvara
URL meca u default browser-u (npr. `https://www.oddsportal.com/basketball/h2h/.../#bet-id`).
Ako tvoj Windows iz nekog razloga ne podrzava `InteractableWindowsToaster`,
program automatski padne na obican toast (samo tekst).

## Fajlovi

- `monitor.log` — trenutni snapshot (resetuje se svakih ~30s)
- `notifications.log` — trajna istorija svih novih sure betova
- `seen_bets.json` — interno stanje (da se isti bet ne javlja dva puta). Obrisi za reset.
- `last_page.html` — poslednja ucitana stranica (za debugovanje)

## Reset

Obrisi `seen_bets.json` ako zelis da svi trenutni betovi budu tretirani kao novi.

## Sta ako ne stizu notifikacije
- Provjeri `Settings -> Notifications` da je dozvoljeno za "Sure Bet Monitor"
- Pogledaj `monitor.log` — ako pise `nema novih sure betova`, znaci da nema novih, a ne da ne radi
- Ako pise `filtrirano: N`, tvoj filter mozda iskljucuje sve. Ublazi `MIN_PROFIT` ili obrisi `MY_BOOKMAKERS`
