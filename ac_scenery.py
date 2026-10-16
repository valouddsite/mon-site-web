#!/usr/bin/env python3
"""
Décor automatique : bâtiments (extrudés depuis leur emprise) et végétation (arbres isolés + forêts)
d'après OpenStreetMap (API Overpass), placés autour du tracé aux bonnes coordonnées.
Python 3.8+, aucune dépendance. Appelé par ac_track_builder.build_track().
"""
import hashlib, json, math, os, random, re, urllib.parse, urllib.request

R = 6371008.8
OVERPASS = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache')
MAX_VERTS = 60000          # par objet : KsEditor/kn5 utilisent des index 16 bits


# ------------------------------------------------------------------ OpenStreetMap
def bbox_of(ll, margin):
    lats, lngs = [p[1] for p in ll], [p[0] for p in ll]
    dlat = margin / 111320.0
    dlng = margin / (111320.0 * math.cos(math.radians((min(lats) + max(lats)) / 2)))
    return min(lats) - dlat, min(lngs) - dlng, max(lats) + dlat, max(lngs) + dlng

def make_query(bb, want_b, want_t):
    b, parts = '(%.6f,%.6f,%.6f,%.6f)' % bb, []
    if want_b:
        parts += ['way["building"]%s;' % b, 'relation["building"]["type"="multipolygon"]%s;' % b]
    if want_t:
        parts += ['node["natural"="tree"]%s;' % b, 'way["natural"="wood"]%s;' % b, 'way["landuse"="forest"]%s;' % b,
                  'relation["natural"="wood"]%s;' % b, 'relation["landuse"="forest"]%s;' % b]
    return '[out:json][timeout:90];(' + ''.join(parts) + ');out geom;'

def fetch_osm(bb, want_b, want_t):
    if max(bb[2] - bb[0], bb[3] - bb[1]) > 0.15:
        raise ValueError('Zone trop grande pour OpenStreetMap (> 0,15°). Réduis la marge ou le parcours.')
    q = make_query(bb, want_b, want_t)
    key = os.path.join(CACHE, hashlib.sha1(q.encode()).hexdigest() + '.json')
    try:
        return json.load(open(key, encoding='utf-8'))
    except Exception:
        pass
    last = None
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(),
                                         headers={'User-Agent': 'AC-Track-Studio/1.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=110).read().decode('utf-8'))
            try:
                os.makedirs(CACHE, exist_ok=True); json.dump(data, open(key, 'w', encoding='utf-8'))
            except OSError:
                pass
            return data
        except Exception as e:
            last = e
    raise RuntimeError('OpenStreetMap injoignable (%s)' % last)

def rings(el):
    """Polygones fermés [(lon, lat), ...] d'un way ou des contours extérieurs d'une relation."""
    geoms = []
    if el['type'] == 'way' and 'geometry' in el: geoms.append(el['geometry'])
    elif el['type'] == 'relation':
        geoms += [m['geometry'] for m in el.get('members', [])
                  if m.get('type') == 'way' and m.get('role') == 'outer' and 'geometry' in m]
    out = []
    for g in geoms:
        p = [(q['lon'], q['lat']) for q in g]
        if len(p) >= 4 and p[0] == p[-1]: out.append(p[:-1])
    return out


# ------------------------------------------------------------------ outils géométriques
class RouteIndex:
    """Distance au tracé et altitude du point de route le plus proche (grille de 25 m)."""
    def __init__(self, pts, cell=25.0):
        self.c, self.pts, self.g = cell, pts, {}
        for p in pts: self.g.setdefault((int(p[0] // cell), int(p[2] // cell)), []).append(p)
    def nearest(self, x, z, maxr=700.0):
        cx, cz, best, k = int(x // self.c), int(z // self.c), (1e18, None), 0
        while k * self.c <= maxr:
            for i in range(cx - k, cx + k + 1):
                for j in range(cz - k, cz + k + 1):
                    if max(abs(i - cx), abs(j - cz)) != k: continue
                    for p in self.g.get((i, j), ()):
                        d = math.hypot(p[0] - x, p[2] - z)
                        if d < best[0]: best = (d, p)
            if best[1] is not None and best[0] <= k * self.c: break
            k += 1
        return (best[0], best[1][1]) if best[1] else (1e18, self.pts[0][1])

def cross2d(a, b, c): return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
def area2(poly): return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))

def orient(poly):
    """Sens tel que area2 < 0 (face vers le haut / murs vers l'extérieur avec nos conventions)."""
    return poly[::-1] if area2(poly) > 0 else poly

def triangulate(poly):
    idx, tris = list(range(len(poly))), []
    guard = 0
    while len(idx) > 3 and guard < 20000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross2d(a, b, c) > -1e-9: continue
            if any(cross2d(a, b, poly[j]) <= 0 and cross2d(b, c, poly[j]) <= 0 and cross2d(c, a, poly[j]) <= 0
                   for j in idx if j not in (i0, i1, i2)): continue
            tris.append((i0, i1, i2)); del idx[k]; break
        else:
            tris += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]; return tris
    if len(idx) == 3: tris.append(tuple(idx))
    return tris

def in_poly(p, poly):
    x, z, ins = p[0], p[1], False
    for i in range(len(poly)):
        (x0, z0), (x1, z1) = poly[i], poly[(i + 1) % len(poly)]
        if (z0 > z) != (z1 > z) and x < (x1 - x0) * (z - z0) / (z1 - z0) + x0: ins = not ins
    return ins


# ------------------------------------------------------------------ écriture OBJ par groupes (≤ 60 000 sommets)
class Group:
    def __init__(self, obj, name): self.o, self.name, self.n, self.v, self.f = obj, name, 0, [], []
    def add(self, verts, tris):
        if len(self.v) + len(verts) > MAX_VERTS: self.flush()
        b = len(self.v); self.v += verts; self.f += [(a + b, c + b, d + b) for a, c, d in tris]
    def flush(self):
        if not self.v: return
        o = self.o; o.lines.append('o %s_%d' % (self.name, self.n)); self.n += 1
        o.lines += ['v %.3f %.3f %.3f' % (x, y, z) for x, y, z, _, _ in self.v]
        o.lines += ['vt %.3f %.3f' % (u, v) for _, _, _, u, v in self.v]
        b = o.base + 1
        o.lines += ['f %d/%d %d/%d %d/%d' % (a + b, a + b, c + b, c + b, d + b, d + b) for a, c, d in self.f]
        o.base += len(self.v); self.v, self.f = [], []


# ------------------------------------------------------------------ éléments
def building_height(tags, rnd):
    h = tags.get('height', '')
    m = re.match(r'\s*([\d.]+)', h) if h else None
    if m: return max(2.5, float(m.group(1)))
    m = re.match(r'\s*([\d.]+)', tags.get('building:levels', '') or '')
    if m: return max(2.5, float(m.group(1)) * 3.2 + 1.0)
    return 5.5 if tags.get('building') in ('house', 'garage', 'garages', 'shed', 'hut', 'detached') else rnd.uniform(6, 11)

def add_building(walls, roofs, poly, y0, h):
    poly = orient(poly); n = len(poly); top = y0 + h
    for i in range(n):
        (x0, z0), (x1, z1) = poly[i], poly[(i + 1) % n]
        L = math.hypot(x1 - x0, z1 - z0) / 4
        walls.add([(x0, y0 - 1, z0, 0, 0), (x1, y0 - 1, z1, L, 0), (x1, top, z1, L, (h + 1) / 3.2), (x0, top, z0, 0, (h + 1) / 3.2)],
                  [(0, 1, 2), (0, 2, 3)])
    roofs.add([(x, top, z, x / 6, z / 6) for x, z in poly], triangulate(poly))

def add_tree(leaves, trunks, x, y0, z, H, conifer, rnd):
    Rr = max(1.4, H * rnd.uniform(.26, .34)); ht = H * .3; n = 6
    ang = [-2 * math.pi * k / n for k in range(n)]
    v, f = [], []
    for k in range(n):                                                   # tronc : prisme à 6 faces
        a0, a1 = ang[k], ang[(k + 1) % n]; r = .22 + H * .012
        b = len(v)
        v += [(x + r * math.cos(a0), y0, z + r * math.sin(a0), 0, 0), (x + r * math.cos(a1), y0, z + r * math.sin(a1), 1, 0),
              (x + r * math.cos(a1), y0 + ht, z + r * math.sin(a1), 1, 1), (x + r * math.cos(a0), y0 + ht, z + r * math.sin(a0), 0, 1)]
        f += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    trunks.add(v, f)
    levels = [(.2, 1.0)] if conifer else [(.3, .65), (.55, 1.0)]          # (hauteur relative, rayon relatif)
    v, f = [], []
    for li, (hf, rf) in enumerate(levels):
        up = levels[li + 1] if li + 1 < len(levels) else None
        r0, y_0 = Rr * rf, y0 + H * hf
        r1, y_1 = (Rr * up[1], y0 + H * up[0]) if up else (0, y0 + H)
        for k in range(n):
            a0, a1 = ang[k], ang[(k + 1) % n]; b = len(v)
            v += [(x + r0 * math.cos(a0), y_0, z + r0 * math.sin(a0), 0, 0), (x + r0 * math.cos(a1), y_0, z + r0 * math.sin(a1), 1, 0),
                  (x + r1 * math.cos(a1), y_1, z + r1 * math.sin(a1), 1, 1), (x + r1 * math.cos(a0), y_1, z + r1 * math.sin(a0), 0, 1)]
            f += [(b, b + 1, b + 2)] if not up else [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    leaves.add(v, f)


# ------------------------------------------------------------------ point d'entrée
def add_scenery(obj, ll0, route, clear, opt):
    """
    obj    : Obj (ac_track_builder) auquel on ajoute les objets
    ll0    : (lng, lat, alt) du premier point = origine du repère
    route  : points (x, y, z) du tracé lissé (même repère)
    clear  : distance minimale (m) entre l'axe de la route et un bâtiment / arbre
    opt    : buildings, trees, margin, density (arbres/ha), max_buildings, max_trees, osm (données déjà chargées)
    """
    want_b, want_t = opt.get('buildings', True), opt.get('trees', True)
    osm = opt.get('osm')
    if osm is None:
        raise_ll = opt['ll']                                     # tracé d'origine pour la boîte englobante
        osm = fetch_osm(bbox_of(raise_ll, float(opt.get('margin', 200))), want_b, want_t)
    lng0, lat0 = math.radians(ll0[0]), math.radians(ll0[1])
    xy = lambda lo, la: ((math.radians(lo) - lng0) * math.cos(lat0) * R, -(math.radians(la) - lat0) * R)
    idx = RouteIndex(route)
    walls, roofs = Group(obj, 'BUILDING_WALLS'), Group(obj, 'BUILDING_ROOFS')
    leaves, trunks = Group(obj, 'TREE_LEAVES'), Group(obj, 'TREE_TRUNKS')
    stats = {'buildings': 0, 'buildings_skipped': 0, 'trees': 0}
    els = osm.get('elements', [])
    maxb, maxt = int(opt.get('max_buildings', 3000)), int(opt.get('max_trees', 4000))
    spacing = math.sqrt(10000.0 / max(1.0, float(opt.get('density', 25))))

    if want_b:
        for el in els:
            tags = el.get('tags', {})
            if 'building' not in tags or stats['buildings'] >= maxb: continue
            for ring in rings(el):
                poly = [xy(lo, la) for lo, la in ring]
                if len(poly) < 3 or abs(area2(poly)) < 6: continue
                pts = poly + [((poly[i][0] + poly[(i + 1) % len(poly)][0]) / 2, (poly[i][1] + poly[(i + 1) % len(poly)][1]) / 2) for i in range(len(poly))]
                cx, cz = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
                dmin = min(idx.nearest(px, pz, clear + 5)[0] for px, pz in pts + [(cx, cz)])
                if dmin < clear: stats['buildings_skipped'] += 1; continue
                rnd = random.Random(el['id'])
                add_building(walls, roofs, poly, idx.nearest(cx, cz)[1], building_height(tags, rnd))
                stats['buildings'] += 1

    if want_t:
        cand = []                                                # (x, z, hauteur, conifère)
        for el in els:
            tags = el.get('tags', {})
            rnd = random.Random(el['id'])
            if el['type'] == 'node' and tags.get('natural') == 'tree':
                x, z = xy(el['lon'], el['lat'])
                m = re.match(r'\s*([\d.]+)', tags.get('height', '') or '')
                cand.append((x, z, float(m.group(1)) if m else rnd.uniform(7, 12), tags.get('leaf_type') == 'needleleaved'))
            elif tags.get('natural') == 'wood' or tags.get('landuse') == 'forest':
                conif = tags.get('leaf_type') == 'needleleaved'
                for ring in rings(el):
                    poly = [xy(lo, la) for lo, la in ring]
                    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
                    gx = min(xs)
                    while gx < max(xs):
                        gz = min(zs)
                        while gz < max(zs):
                            p = (gx + rnd.uniform(0, spacing), gz + rnd.uniform(0, spacing))
                            if in_poly(p, poly):
                                cand.append((p[0], p[1], rnd.uniform(8, 16), conif if 'leaf_type' in tags else rnd.random() < .4))
                            gz += spacing
                        gx += spacing
        random.Random(1).shuffle(cand)
        for x, z, H, conif in cand:
            if stats['trees'] >= maxt: break
            d, y = idx.nearest(x, z, clear + 5)
            if d < clear + 0.5: continue
            _, y = idx.nearest(x, z)
            add_tree(leaves, trunks, x, y, z, H, conif, random.Random(int(x * 7 + z * 13)))
            stats['trees'] += 1

    for g in (walls, roofs, leaves, trunks): g.flush()
    return stats
