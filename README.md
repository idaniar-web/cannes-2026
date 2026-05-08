# Cannes 2026 Schedule Builder

A personalised, interactive schedule builder for the 79th Festival de Cannes (12–23 May 2026), tuned to my taste via my [Letterboxd ratings](https://letterboxd.com/daniarito/).

**Live dashboard:** https://idaniar-web.github.io/cannes-2026/

Open it on any device (including iPhone). Selections persist in browser localStorage.

## What's in the dashboard

- **🗓 Planner** — day-by-day, slot-by-slot. Each slot shows the default main pick plus backups, ranked by combined taste × prestige score. Click to switch; use the override dropdown for any other overlapping screening. Evening galas at the Grand Théâtre Lumière are hard-pinned (tuxedo nights).
- **📋 My Plan** — single scrollable view of all chosen films across the festival, sorted by date. One-click export to markdown.
- **🏆 Competition Tracker** — coverage of every Palme d'Or contender with going / skipping status.
- **🔍 Lookup** — live search by title, director, tag, country, or section. Details modal shows every screening of that film across the festival, with my chosen one highlighted.

## Taste model

- **Taste (0–10)** — alignment with my Letterboxd ratings, favouring bold/stylish/sensory/maximalist auteur cinema (Refn, Mandico, Harari, Na Hong-jin, Dupieux) over quiet restrained arthouse (Berlin School, minimalist Latin American).
- **Prestige (0–10)** — combination of Cannes section weight and director stature (Competition > UCR > Fortnight > Cannes Premiere > other; Palme-winning veterans top the scale).
- **Score** = mean of the two.

## Scheduling logic

Weighted interval scheduling (DP), run across all 7 days:

1. All Grand Théâtre Lumière galas get hard-pinned.
2. User-defined blackout windows respected (e.g. nothing before 5pm on May 17, my arrival day).
3. Each film is picked at most once across the whole festival — reruns on later days are automatically filtered.
4. Score floor of 5.0 to avoid padding with low-prestige fillers.
5. Next film must start at or after the previous one ends. After a late gala (end ≥ 22:30), mornings before 9am are skipped.

## Rebuilding

```bash
pip install pypdf  # if needed
python3 extract_pdf.py 17may.pdf > 17may.txt  # (one per day)
python3 parse_schedule.py   # writes schedule.json
python3 build_dashboard.py  # writes index.html
```

Schedule PDFs are the official `festival-cannes.com` ticketing exports.

## Files

- `index.html` — the self-contained dashboard (CSS + JS + data all inlined, ~340KB)
- `dashboard.html` — template used to regenerate `index.html`
- `film_meta.py` — per-film taste/prestige ratings and metadata
- `parse_schedule.py` — extracts structured screenings from the Cannes PDF exports
- `build_dashboard.py` — merges ratings + schedule → `index.html`
- `*.pdf`, `*.txt` — raw schedule exports per day (kept for reproducibility)
