"""Parse the seven Cannes PDFs into a structured list of screenings."""
import json
import re
from pathlib import Path

TIME_RE = re.compile(r'^\d{1,2}:\d{2}\s*(am|pm)$', re.I)
DATE_RE = re.compile(r'^\d{2}\.\d{2} AT \d{1,2}:\d{2}\s*(AM|PM)$')
PLAGE_RE = re.compile(r'^PLAGE MACÉ\s+(\d{1,2}:\d{2}\s*(am|pm))$', re.I)

KNOWN_VENUES = [
    'GRAND THÉÂTRE LUMIÈRE',
    'DEBUSSY THEATRE',
    'AGNÈS VARDA THEATRE',
    'BUÑUEL THEATRE',
    'BAZIN THEATRE',
    'THÉÂTRE CROISETTE',
    'CINEUM IMAX',
    'CINEUM AURORE',
    'CINEUM SCREEN X',
    'CINEUM SALLE 3',
    'LICORNE',
    'RAIMU',
    'ALEXANDRE III',
    'STUDIO 13',
    'ARCADES 1',
    'ARCADES 2',
    'MIRAMAR',
    'PLAGE MACÉ',
    'CARLTON',
    'PRESSE CONFERENCE ROOM',
]

PREFIXES = {'Q & A', 'Press Conference', 'Rendez-vous with ...', 'Opening',
            'Closing', 'Awards'}

BOOKING_JUNK_RE = re.compile(
    r'^BOOKING SEE ONLY AVAILABLE TICKETS\s+PER DAY\s+BY MOVIE ALL PROGRAMS\s*(.*)$')


def clean_lines(path):
    raw = Path(path).read_text()
    lines = []
    for l in raw.splitlines():
        if l.startswith('Ignoring') or l.startswith('--- PAGE'):
            continue
        lines.append(l)

    # Remove the "BOOKING / SEE ONLY AVAILABLE TICKETS / PER DAY BY MOVIE ALL
    # PROGRAMS" page-header block. It appears on each page and the lines are
    # spread over up to 3 rows.
    out = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == 'BOOKING':
            # Skip until we pass a line that ends with "ALL PROGRAMS"
            j = i
            while j < len(lines) and 'ALL PROGRAMS' not in lines[j]:
                j += 1
            i = j + 1  # skip the ALL PROGRAMS line too
            continue
        # Also catch when the junk is all on one line
        if BOOKING_JUNK_RE.match(stripped):
            m = BOOKING_JUNK_RE.match(stripped)
            remainder = m.group(1).strip()
            if remainder:
                out.append(remainder)
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def split_trailing_venue(text):
    for v in sorted(KNOWN_VENUES, key=len, reverse=True):
        if text.endswith(' ' + v) or text == v:
            head = text[:-len(v)].rstrip()
            return head, v
    return text, None


def time_to_minutes(t):
    m = re.match(r'(\d{1,2}):(\d{2})\s*(am|pm)', t, re.I)
    if not m:
        return None
    hour = int(m.group(1)) % 12
    minute = int(m.group(2))
    if m.group(3).lower() == 'pm':
        hour += 12
    return hour * 60 + minute


def flush_meta(buf, prefix=None):
    """Extract title/director/section/venue from a buffer of lines."""
    if not buf:
        return None

    # Peel off leading prefix like "Q & A"
    if prefix is None and buf and buf[0] in PREFIXES:
        prefix = buf[0]
        buf = buf[1:]

    if not buf:
        return None

    cinephile = False
    if buf[-1] == 'CANNES CINÉPHILES':
        cinephile = True
        buf = buf[:-1]

    if not buf:
        return None

    venue = None
    if buf[-1] in KNOWN_VENUES:
        venue = buf[-1]
        buf = buf[:-1]
    else:
        head, trailing = split_trailing_venue(buf[-1])
        if trailing:
            venue = trailing
            if head:
                buf[-1] = head
            else:
                buf = buf[:-1]
        else:
            venue = buf[-1]
            buf = buf[:-1]

    section = buf[-1] if buf else ''
    buf = buf[:-1]

    if not buf:
        title = section
        director = ''
        section = 'Unknown'
    else:
        director = buf[-1]
        title = ' '.join(buf[:-1]) if len(buf) > 1 else buf[0]
        if not title:
            title = director
            director = ''

    return {
        'title': title.strip(),
        'director': director.strip(),
        'section': section.strip(),
        'venue': venue,
        'cinephile_slot': cinephile,
        'prefix': prefix,
    }


def parse_day(path):
    lines = clean_lines(path)
    screenings = []
    buf = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Reserved-screening anchor: OPENING / RESERVATION / DATE
        if (stripped == 'OPENING'
                and i + 2 < n
                and lines[i + 1].strip() == 'RESERVATION'
                and DATE_RE.match(lines[i + 2].strip())):
            date_line = lines[i + 2].strip()
            offset = i + 3

            is_gala = False
            if offset < n:
                probe = lines[offset]
                if probe.strip() == '':
                    is_gala = True
                    offset += 1

            if offset + 2 >= n:
                break
            start_time = lines[offset].strip()
            end_marker = lines[offset + 1].strip()
            end_time = lines[offset + 2].strip()
            if end_marker == 'END' and TIME_RE.match(start_time) and TIME_RE.match(end_time):
                meta = flush_meta(buf)
                if meta:
                    meta.update({
                        'start': start_time,
                        'end': end_time,
                        'is_gala': is_gala,
                        'reservation': date_line,
                    })
                    screenings.append(meta)
                buf = []
                i = offset + 3
                continue

        # Plage Macé beach-screening anchor (no reservation block)
        mplage = PLAGE_RE.match(stripped)
        if mplage and i + 2 < n and lines[i + 1].strip() == 'END' and TIME_RE.match(lines[i + 2].strip()):
            start_time = mplage.group(1)
            end_time = lines[i + 2].strip()
            # The preceding buffer ends with section; venue is Plage Macé.
            # Push "PLAGE MACÉ" on the buffer so flush_meta treats it as the
            # venue in the same way it handles reserved screenings.
            buf.append('PLAGE MACÉ')
            meta = flush_meta(buf)
            if meta:
                meta.update({
                    'start': start_time,
                    'end': end_time,
                    'is_gala': False,
                    'reservation': None,
                })
                screenings.append(meta)
            buf = []
            i += 3
            continue

        if stripped:
            buf.append(stripped)
        i += 1

    for s in screenings:
        s['start_minutes'] = time_to_minutes(s['start'])
        end_m = time_to_minutes(s['end'])
        if end_m is not None and s['start_minutes'] is not None and end_m < s['start_minutes']:
            end_m += 24 * 60
        s['end_minutes'] = end_m
        # Post-fix short-film programs: the PDF swaps title and section.
        if s['title'].upper().startswith('IN COMPETITION - SHORT FILMS') and s['section'].upper().startswith('COURTS MÉTRAGES'):
            s['title'], s['section'] = s['section'], s['title']
            s['director'] = 'Various'

    screenings.sort(key=lambda s: (s['start_minutes'] or 0))
    return screenings


def main():
    days = {
        '2026-05-17': '17may.txt',
        '2026-05-18': '18may.txt',
        '2026-05-19': '19may.txt',
        '2026-05-20': '20may.txt',
        '2026-05-21': '21may.txt',
        '2026-05-22': '22may.txt',
        '2026-05-23': '23may.txt',
    }
    out = {}
    for date, path in days.items():
        out[date] = parse_day(path)

    Path('schedule.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))

    for d, sc in out.items():
        galas = sum(1 for s in sc if s['is_gala'])
        print(f'{d}: {len(sc)} screenings, {galas} gala')


if __name__ == '__main__':
    main()
