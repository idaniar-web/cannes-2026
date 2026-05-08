"""Merge parsed schedule with film metadata and emit a single HTML dashboard."""
import json
import re
from pathlib import Path
from film_meta import FILMS, DEFAULT_META


def norm_title(t):
    t = t.upper().strip()
    # Strip trailing "…" or truncation markers
    t = t.replace('…', '').strip()
    return t


def resolve_meta(title):
    key = norm_title(title)
    if key in FILMS:
        meta = FILMS[key]
        while isinstance(meta, dict) and 'alias_of' in meta:
            meta = FILMS[meta['alias_of']]
        return meta
    # Try alternate keys (with/without parenthetical)
    simpler = re.sub(r'\s*\(.*?\)\s*', ' ', key).strip()
    if simpler in FILMS:
        meta = FILMS[simpler]
        while isinstance(meta, dict) and 'alias_of' in meta:
            meta = FILMS[meta['alias_of']]
        return meta
    return DEFAULT_META


SECTION_ORDER = {
    'Competition': 0,
    'UCR': 1,
    'DF': 2,
    'Cannes Premiere': 3,
    'Out of Competition': 4,
    'Midnight Screenings': 5,
    'Semaine': 6,
    'Special': 7,
    'Shorts Competition': 8,
    'Other': 9,
}

SECTION_LABEL = {
    'Competition': 'Competition',
    'UCR': 'Un Certain Regard',
    'DF': "Directors' Fortnight",
    'Cannes Premiere': 'Cannes Premiere',
    'Out of Competition': 'Out of Competition',
    'Midnight Screenings': 'Midnight',
    'Semaine': 'Semaine de la Critique',
    'Special': 'Special Screenings',
    'Shorts Competition': 'Shorts Competition',
    'Other': 'Other',
}


def score_of(meta):
    return round((meta.get('taste', 5) + meta.get('prestige', 5)) / 2, 1)


def enrich(schedule):
    """Attach meta + score to each screening."""
    for date, sc in schedule.items():
        for s in sc:
            m = resolve_meta(s['title'])
            s['meta'] = {
                'director': m.get('director', s.get('director', '')),
                'section_tag': m.get('section_tag', 'Other'),
                'country': m.get('country', ''),
                'runtime': m.get('runtime'),
                'synopsis': m.get('synopsis', ''),
                'director_note': m.get('director_note', ''),
                'taste': m.get('taste', 4.5),
                'prestige': m.get('prestige', 5.0),
                'tags': m.get('tags', []),
                'score': score_of(m),
            }
    return schedule


def assemble_slots(sc):
    """Build a default itinerary using greedy max-score scheduling that
    respects the hard constraint: next film must start at or after the end
    time of the currently chosen film. Each film is only picked once per
    festival (across all days). Starts early in the morning; prefers higher
    total count of unique films while maximising scores.
    """
    plannable = [s for s in sc if s['prefix'] not in (
        'Press Conference', 'Rendez-vous with ...')]
    for s in sc:
        s['plannable'] = s['prefix'] not in (
            'Press Conference', 'Rendez-vous with ...')

    plannable.sort(key=lambda x: (x['start_minutes'] or 0, -x['meta']['score']))
    return plannable  # pre-sort; we rebuild slots at the top-level


def build_itinerary_across_days(schedule):
    """Build a festival-wide itinerary using weighted interval scheduling.

    Objectives per day:
    1. Hard-include all evening galas at Grand Théâtre Lumière.
    2. Respect any per-day blackout windows (e.g. user unavailable before 5pm
       on arrival day).
    3. Maximise total value of remaining picks, where
       value(screening) = 1 + score/10 (rewards both count and quality).
    4. Never repeat a film title across the whole festival (dedup is a hard
       constraint — if a film is picked on day N, every subsequent screening
       of the same title on any day is filtered out of the pool).

    Returns: {date: [slot dicts]}.
    """
    # Per-day blackout windows: list of (start_min, end_min) intervals that
    # are NOT available for screenings on that date. Galas pinned inside a
    # blackout are dropped.
    BLACKOUTS = {
        '2026-05-17': [(0, 17 * 60)],  # Nothing before 5pm on arrival day.
    }
    already_seen_title = set()

    def norm(t):
        t = (t or '').upper().strip()
        # Strip truncation markers
        t = t.replace('…', '').strip()
        # Strip full parentheticals and any orphaned unclosed one
        t = re.sub(r'\s*\(.*?\)\s*', ' ', t).strip()
        t = re.sub(r'\s*\([^)]*$', '', t).strip()  # unclosed '(' to end
        # Strip trailing single quotes / guillemets
        t = t.rstrip("'\u2019\u2018")
        # Collapse multiple spaces
        t = re.sub(r'\s+', ' ', t)
        return t

    result = {}
    for date in sorted(schedule.keys()):
        day = schedule[date]
        blackouts = BLACKOUTS.get(date, [])

        def in_blackout(sm, em):
            for bs, be in blackouts:
                if sm is None or em is None:
                    continue
                if sm < be and bs < em:
                    return True
            return False

        for s in day:
            s['plannable'] = s['prefix'] not in (
                'Press Conference', 'Rendez-vous with ...')

        # Pin galas first, but only if they fall outside blackouts.
        galas = [s for s in day if s.get('is_gala') and s['plannable']
                 and norm(s['title']) not in already_seen_title
                 and not in_blackout(s['start_minutes'], s['end_minutes'])]
        # Sort galas by start to chain them correctly.
        galas.sort(key=lambda x: x['start_minutes'])

        # Build disallowed windows from pinned galas.
        pinned = galas[:]
        pinned_titles = set(norm(s['title']) for s in pinned)

        # Pool of candidate non-gala films, unique-title, plannable, decent
        # score floor (avoid padding with low-quality fillers).
        # Among multiple screenings of the same title, keep the one with the
        # EARLIEST end time to maximise scheduling flexibility.
        by_title = {}
        for s in day:
            if not s.get('plannable'):
                continue
            t = norm(s['title'])
            if t in already_seen_title or t in pinned_titles:
                continue
            if s['meta']['score'] < 5.0:
                continue
            if s['end_minutes'] is None or s['start_minutes'] is None:
                continue
            if in_blackout(s['start_minutes'], s['end_minutes']):
                continue
            if t not in by_title or s['end_minutes'] < by_title[t]['end_minutes']:
                by_title[t] = s
        pool = list(by_title.values())

        # Weighted interval scheduling by DP:
        # Each screening has value = 1 + score/10 (density + quality).
        # We must pick a non-overlapping subset; additionally we must not
        # overlap the pinned galas.
        pinned_intervals = [(g['start_minutes'], g['end_minutes']) for g in pinned]

        def overlaps_pin(s, e):
            for ps, pe in pinned_intervals:
                if s < pe and ps < e:
                    return True
            return False

        # Rest constraint: if a film ends after 22:00, nothing before 9:00
        # next day is pickable — handled at the boundary between days
        # (we just skip <9:00 candidates if the LAST day's last pick was late).

        # Reject candidates that overlap pinned galas. Also reject candidates
        # starting too early the next morning if the previous day had a late
        # gala (approx: if any pinned_end on this day < own start, fine; we
        # handle the rest constraint across days via a boolean flag below).
        needs_rest_before = False
        if result:
            last_date = sorted(result.keys())[-1]
            last_day_picks = [p for sl in result[last_date] for p in
                              [next((s for s in schedule[last_date]
                                     if s['id'] == sl['main_id']), None)]
                              if p]
            if last_day_picks:
                last_end = last_day_picks[-1]['end_minutes'] or 0
                # Cross-midnight end times are already normalized to >24*60.
                if last_end >= 22 * 60 + 30:
                    needs_rest_before = True

        filtered = []
        for c in pool:
            if overlaps_pin(c['start_minutes'], c['end_minutes']):
                continue
            if needs_rest_before and c['start_minutes'] < 9 * 60:
                continue
            filtered.append(c)

        # Sort by end time for weighted interval scheduling DP.
        filtered.sort(key=lambda x: x['end_minutes'])
        n = len(filtered)
        if n == 0:
            chosen = []
        else:
            # p[i] = largest j < i such that filtered[j].end <= filtered[i].start
            p = [-1] * n
            for i, s in enumerate(filtered):
                lo, hi = 0, i - 1
                ans = -1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if filtered[mid]['end_minutes'] <= s['start_minutes']:
                        ans = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                p[i] = ans

            def value(s):
                return 1.0 + s['meta']['score'] / 10.0

            dp = [0.0] * n
            for i in range(n):
                incl = value(filtered[i]) + (dp[p[i]] if p[i] >= 0 else 0.0)
                excl = dp[i - 1] if i > 0 else 0.0
                dp[i] = max(incl, excl)

            chosen = []
            i = n - 1
            while i >= 0:
                incl = value(filtered[i]) + (dp[p[i]] if p[i] >= 0 else 0.0)
                excl = dp[i - 1] if i > 0 else 0.0
                if incl >= excl:
                    chosen.append(filtered[i])
                    i = p[i]
                else:
                    i -= 1
            chosen.reverse()

        # Combine chosen + pinned galas, ordered by start time.
        itinerary = sorted(chosen + pinned, key=lambda x: x['start_minutes'])
        for s in itinerary:
            already_seen_title.add(norm(s['title']))

        # Build slots with backups (films starting within ±120 min whose time
        # window fits between prev end and next start).
        slots = []
        for i, main in enumerate(itinerary):
            prev_end = itinerary[i - 1]['end_minutes'] if i > 0 else 0
            next_start = (
                itinerary[i + 1]['start_minutes']
                if i + 1 < len(itinerary) else 30 * 60)
            window_lo = main['start_minutes'] - 120
            window_hi = main['start_minutes'] + 120
            backups = []
            for s in day:
                if s is main or not s.get('plannable'):
                    continue
                sm = s['start_minutes']
                em = s['end_minutes']
                if sm is None or em is None:
                    continue
                if in_blackout(sm, em):
                    continue
                if sm < window_lo or sm > window_hi:
                    continue
                if sm < prev_end:
                    continue
                if em > next_start:
                    continue
                backups.append(s)

            backups.sort(key=lambda x: (
                -x['meta']['score'], abs(x['start_minutes'] - main['start_minutes'])))

            slots.append({
                'anchor_start': main['start_minutes'],
                'screenings': [main] + backups,
                'main_id': id(main),
            })
        result[date] = slots
    return result


def assign_ids(schedule):
    """Assign stable string IDs to each screening so the dashboard JS can
    reference them."""
    for date, sc in schedule.items():
        for i, s in enumerate(sc):
            s['id'] = f'{date}-{i:03d}'


def main():
    schedule = json.loads(Path('schedule.json').read_text())
    schedule = enrich(schedule)
    assign_ids(schedule)

    # Pre-compute slots per day
    slots_by_day = {}
    all_slots = build_itinerary_across_days(schedule)
    for date, slots in all_slots.items():
        simple = []
        for sl in slots:
            simple.append({
                'anchor_start': sl['anchor_start'],
                'screening_ids': [s['id'] for s in sl['screenings']],
                'main_id': sl['screenings'][0]['id'],
            })
        slots_by_day[date] = simple

    data = {
        'schedule': schedule,
        'slots': slots_by_day,
        'section_label': SECTION_LABEL,
    }

    Path('dashboard_data.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=2))
    print(f'Wrote dashboard_data.json ({Path("dashboard_data.json").stat().st_size} bytes)')

    # Self-contained HTML for GitHub Pages (and direct file:// use on iPhone).
    template = Path('dashboard.html').read_text()
    inlined = template.replace(
        '__DATA__',
        json.dumps(data, ensure_ascii=False))
    Path('index.html').write_text(inlined)
    print(f'Wrote index.html ({Path("index.html").stat().st_size} bytes)')


if __name__ == '__main__':
    main()
