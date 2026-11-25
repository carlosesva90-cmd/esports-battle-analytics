import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

# Uso: python actualizar_datos.py battle.db index.html
base, pagina = map(Path, sys.argv[1:3])
formatos = ('8min', '12min')
with sqlite3.connect(base.resolve().as_uri() + '?mode=ro', uri=True) as db:
    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('Base de datos dañada')
    fin = date.fromisoformat(db.execute(
        'SELECT MAX(local_date) FROM matches WHERE score1 IS NOT NULL AND score2 IS NOT NULL'
    ).fetchone()[0])
    inicio45 = fin - timedelta(days=44)
    inicio120 = fin - timedelta(days=119)
    filas = db.execute('''
        SELECT format, local_date, player1, player2, score1, score2
        FROM matches
        WHERE local_date BETWEEN ? AND ?
          AND format IN ('8min', '12min')
          AND score1 >= 0 AND score2 >= 0
        ORDER BY start_utc, id
    ''', (inicio120.isoformat(), fin.isoformat())).fetchall()

# Datos generales por partido, sin duplicar el registro por jugador.
with sqlite3.connect(base.resolve().as_uri() + '?mode=ro', uri=True) as db:
    global_filas = db.execute("""
        SELECT format, local_date, start_utc, player1, player2, score1, score2, tournament_id
        FROM matches WHERE local_date BETWEEN ? AND ?
        AND format IN ('8min','12min') AND score1 >= 0 AND score2 >= 0
        ORDER BY start_utc, id
    """, (inicio45.isoformat(), fin.isoformat())).fetchall()
    # Completar las alineaciones aunque un torneo cruce el límite del periodo.
    tournament_rows = db.execute("""
        SELECT t.id,t.league,t.venue,t.start_utc,m.format,m.player1,m.player2
        FROM matches m JOIN tournaments t ON t.id=m.tournament_id
        WHERE m.tournament_id IN (SELECT DISTINCT tournament_id FROM matches WHERE local_date BETWEEN ? AND ?)
          AND m.format IN ('8min','12min')
    """, (inicio45.isoformat(), fin.isoformat())).fetchall()
meta = {}
for tid,league,venue,start,f,a,b in tournament_rows:
    item = meta.setdefault(str(tid), {'league':league or 'Torneo','venue':venue or '', 'start':start,'format':f,'players':set()})
    item['players'].update(x for x in (a,b) if x)
for item in meta.values():
    item['players'] = sorted(item['players'],key=str.casefold)
    item['key'] = json.dumps([item['format'], item['venue'],item['players']],ensure_ascii=False,separators=(',',':'))
gnames = {}
grows = {f: [] for f in formatos}
for f,d,t,a,b,x,y,tid in global_filas:
    for jugador in (a,b):
        if jugador not in gnames:
            gnames[jugador] = len(gnames)
    grows[f].append([(date.fromisoformat(d)-inicio45).days, t, gnames[a],gnames[b],x,y,tid])
G = {'names':list(gnames), 'rows':grows, 'tournaments':meta, 'generated':datetime.now(timezone.utc).isoformat(timespec='seconds')}

hist = {f: defaultdict(list) for f in formatos}
pares = defaultdict(list)
medias = {f: [0, 0] for f in formatos}
nombres = {}
dias = [(inicio45 + timedelta(days=i)).isoformat() for i in range(45)]
indices = {d: i for i, d in enumerate(dias)}

for f, d, a, b, x, y in filas:
    if not a or not b or a.casefold() == b.casefold():
        raise ValueError('Jugadores inválidos')
    if any(c in a + b for c in '<>"&|'):
        raise ValueError('Nombre incompatible con el HTML original')
    if a.lower() > b.lower():
        a, b, x, y = b, a, y, x
    pares[f + '|' + a + '|' + b].append((x, y))
    if d not in indices:
        continue
    for jugador, rival, gf, gc in ((a, b, x, y), (b, a, y, x)):
        if rival not in nombres:
            nombres[rival] = len(nombres)
        hist[f][jugador].append([indices[d], nombres[rival], gf, gc])
    medias[f][0] += x + y
    medias[f][1] += 2

D = {'mu': {}, 'p': {}, 'h': {}, 'upto': fin.isoformat(),
     'm': hist, 'n': list(nombres), 'days': dias}
for f in formatos:
    total, n = medias[f]
    if not total or not n:
        raise RuntimeError('Sin resultados válidos de ' + f)
    D['mu'][f] = round(total / n, 3)
    D['p'][f] = {}
    for jugador, partidos in hist[f].items():
        ultimos = partidos[-15:]
        D['p'][f][jugador] = [len(partidos),
            sum(p[2] for p in partidos), sum(p[3] for p in partidos),
            len(ultimos), sum(p[2] for p in ultimos), sum(p[3] for p in ultimos)]

for clave, resultados in pares.items():
    D['h'][clave] = [len(resultados), sum(x for x, y in resultados),
        sum(y for x, y in resultados), sum(x > y for x, y in resultados),
        sum(x == y for x, y in resultados), sum(x < y for x, y in resultados),
        [f'{x}-{y}' for x, y in resultados[-5:]]]

html = pagina.read_text(encoding='utf-8')
prefijo = 'const D='
ini = html.index(prefijo) + len(prefijo)
anterior, largo = json.JSONDecoder().raw_decode(html[ini:])
if set(anterior) != set(D) or fin < date.fromisoformat(anterior['upto']):
    raise RuntimeError('Formato incompatible o datos más antiguos')
nuevo = json.dumps(D, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
html = html[:ini] + nuevo + html[ini + largo:]
if 'const G=' in html:
    gi = html.index('const G=') + len('const G=')
    _, gl = json.JSONDecoder().raw_decode(html[gi:])
    gj = json.dumps(G, ensure_ascii=True, separators=(',', ':'), allow_nan=False).replace('<', r'\u003c')
    html = html[:gi] + gj + html[gi+gl:]
temporal = pagina.with_suffix('.html.tmp')
temporal.write_text(html, encoding='utf-8')
temporal.replace(pagina)
print('Datos hasta:', D['upto'])
for f in formatos:
    print(f, 'jugadores:', len(D['p'][f]), 'partidos recientes:', medias[f][1] // 2)
