#!/usr/bin/env python3
"""
AC Track Builder – transforme un tracé (GeoJSON / GPX / CSV) en dossier de piste Assetto Corsa.
Python 3.8+, aucune dépendance.

  python ac_track_builder.py trace.geojson --name ma_piste --width 9
  python ac_track_builder.py trace.gpx --name ma_piste --walls --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe"

Sortie : build/<name>/  (source/, data/, ui/, map.png) + <name>.fbx si Blender est fourni.
"""
import argparse, csv, glob, json, math, os, shutil, subprocess, sys, zlib
import xml.etree.ElementTree as ET

R = 6371008.8

# ------------------------------------------------------------------ lecture
def load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.geojson', '.json'):
        d = json.load(open(path, encoding='utf-8'))
        for f in (d['features'] if d.get('type') == 'FeatureCollection' else [d]):
            g = f.get('geometry', f)
            if g.get('type') == 'LineString':
                return [(c[0], c[1], c[2] if len(c) > 2 else 0.0) for c in g['coordinates']]
        sys.exit('Aucun LineString dans le fichier.')
    if ext == '.gpx':
        pts = []
        for e in ET.parse(path).getroot().iter():
            if e.tag.endswith('trkpt'):
                ele = next((float(c.text) for c in e if c.tag.endswith('ele')), 0.0)
                pts.append((float(e.get('lon')), float(e.get('lat')), ele))
        return pts
    if ext == '.csv':
        return [(float(r['lng']), float(r['lat']), float(r.get('y_m') or 0))
                for r in csv.DictReader(open(path, encoding='utf-8'))]
    sys.exit('Format non géré : ' + ext)

def project(ll):
    lng0, lat0 = math.radians(ll[0][0]), math.radians(ll[0][1])
    e0 = ll[0][2]
    return [((math.radians(lo) - lng0) * math.cos(lat0) * R, el - e0, -(math.radians(la) - lat0) * R)
            for lo, la, el in ll]

# ------------------------------------------------------------------ géométrie
def resample(pts, step):
    out, carry = [pts[0]], 0.0
    for a, b in zip(pts, pts[1:]):
        d = math.dist((a[0], a[2]), (b[0], b[2]))
        if d == 0: continue
        t = step - carry
        while t <= d:
            f = t / d
            out.append(tuple(a[k] + (b[k] - a[k]) * f for k in range(3)))
            t += step
        carry = d - (t - step)
    return out

def smooth(pts, passes, closed):
    n = len(pts)
    for _ in range(passes):
        c = pts[:]
        for i in range(n):
            if not closed and i in (0, n - 1): continue
            p, q, r = c[(i - 1) % n], c[i], c[(i + 1) % n]
            pts[i] = tuple((p[k] + 2 * q[k] + r[k]) / 4 for k in range(3))
    return pts

def frames(pts, closed):
    n, tg, dist = len(pts), [], [0.0]
    for i in range(n):
        a = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        b = pts[(i + 1) % n] if (closed or i < n - 1) else pts[i]
        tx, tz = b[0] - a[0], b[2] - a[2]
        l = math.hypot(tx, tz) or 1
        tg.append((tx / l, tz / l))
        if i: dist.append(dist[-1] + math.dist((pts[i][0], pts[i][2]), (pts[i - 1][0], pts[i - 1][2])))
    return tg, dist

def at(pts, tg, dist, d, lateral=0.0):
    """Point à la distance d (offset latéral >0 = droite)."""
    i = min(range(len(dist)), key=lambda k: abs(dist[k] - d))
    nx, nz = -tg[i][1], tg[i][0]
    return (pts[i][0] + nx * lateral, pts[i][1], pts[i][2] + nz * lateral), tg[i]

class Obj:
    def __init__(self): self.lines, self.base = [], 0
    def ribbon(self, name, pts, tg, dist, hi, lo, dy, closed, uvw):
        n = len(pts)
        self.lines.append('o ' + name)
        for p, t in zip(pts, tg):
            nx, nz = -t[1], t[0]
            for off in (hi, lo):
                self.lines.append('v %.3f %.3f %.3f' % (p[0] + nx * off, p[1] + dy, p[2] + nz * off))
        for d in dist:
            self.lines += ['vt 0 %.4f' % (d / uvw), 'vt 1 %.4f' % (d / uvw)]
        b = self.base
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            a, c, e, f = b + i * 2 + 1, b + j * 2 + 1, b + j * 2 + 2, b + i * 2 + 2
            self.lines.append('f %d/%d %d/%d %d/%d %d/%d' % (a, a, c, c, e, e, f, f))
        self.base += n * 2
    def wall(self, name, pts, tg, dist, off, h, closed):
        n = len(pts)
        self.lines.append('o ' + name)
        for p, t in zip(pts, tg):
            x, z = p[0] - t[1] * off, p[2] + t[0] * off
            self.lines += ['v %.3f %.3f %.3f' % (x, p[1], z), 'v %.3f %.3f %.3f' % (x, p[1] + h, z)]
        for d in dist:
            self.lines += ['vt 0 %.4f' % (d / h), 'vt 1 %.4f' % (d / h)]
        b = self.base
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            a, c, e, f = b + i * 2 + 1, b + j * 2 + 1, b + j * 2 + 2, b + i * 2 + 2
            self.lines.append('f %d/%d %d/%d %d/%d %d/%d' % (a, a, c, c, e, e, f, f))
            self.lines.append('f %d/%d %d/%d %d/%d %d/%d' % (f, f, e, e, c, c, a, a))  # double face
        self.base += n * 2

# ------------------------------------------------------------------ marqueurs AC
def markers(pts, tg, dist, w, grid, pits, closed):
    total, line = dist[-1], 8 * grid + 10
    if total < line + 60: raise ValueError('Tracé trop court (%.0f m) : minimum %d m.' % (total, line + 60))
    side = w / 2 + 1.5
    M = []
    def add(name, d, lat=0.0):
        p, t = at(pts, tg, dist, d, lat)
        M.append({'name': name, 'x': p[0], 'y': p[1], 'z': p[2], 'hx': t[0], 'hz': t[1]})
    for i in range(grid): add('AC_START_%d' % i, line - 8 * (i + 1), 2.5 if i % 2 == 0 else -2.5)
    for i in range(pits): add('AC_PIT_%d' % i, line + 15 + 10 * i, w / 2 + 6)
    add('AC_HOTLAP_START_0', line - 10)
    add('AC_TIME_0_L', line, -side); add('AC_TIME_0_R', line, side)
    if not closed:
        add('AC_TIME_1_L', total - 10, -side); add('AC_TIME_1_R', total - 10, side)
    return M

# ------------------------------------------------------------------ PNG (stdlib)
def write_png(path, w, h, alpha):
    raw = b''.join(b'\x00' + b''.join(b'\xff\xff\xff' + bytes([alpha[y * w + x]]) for x in range(w))
                   for y in range(h))
    def chunk(t, d):
        c = zlib.crc32(t + d) & 0xffffffff
        return len(d).to_bytes(4, 'big') + t + d + c.to_bytes(4, 'big')
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', w.to_bytes(4, 'big') + h.to_bytes(4, 'big') + b'\x08\x06\x00\x00\x00')
                           + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))

def make_map(pts, closed, folder, size=1024, margin=40):
    xs, zs = [p[0] for p in pts], [p[2] for p in pts]
    scale = max(max(xs) - min(xs), max(zs) - min(zs)) / (size - 2 * margin)
    xo, zo = -min(xs) + margin * scale, -min(zs) + margin * scale
    wpx = int((max(xs) - min(xs)) / scale + 2 * margin); hpx = int((max(zs) - min(zs)) / scale + 2 * margin)
    a = bytearray(wpx * hpx)
    P = [((x + xo) / scale, (z + zo) / scale) for x, z in zip(xs, zs)]
    if closed: P.append(P[0])
    for (x0, y0), (x1, y1) in zip(P, P[1:]):
        for s in range(int(max(abs(x1 - x0), abs(y1 - y0))) + 1):
            f = s / max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
            cx, cy = int(x0 + (x1 - x0) * f), int(y0 + (y1 - y0) * f)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if 0 <= cx + dx < wpx and 0 <= cy + dy < hpx: a[(cy + dy) * wpx + cx + dx] = 255
    for n in ('map.png', 'ui/outline.png'): write_png(os.path.join(folder, n), wpx, hpx, a)
    open(os.path.join(folder, 'data/map.ini'), 'w').write(
        '[PARAMETERS]\nWIDTH=%d\nHEIGHT=%d\nMARGIN=20\nSCALE_FACTOR=%.4f\nX_OFFSET=%.2f\nZ_OFFSET=%.2f\nDRAWING_SIZE=10\n'
        % (wpx, hpx, scale, xo, zo))

# ------------------------------------------------------------------ fichiers de données
SURF = [('ROAD', .98, 1, 'NULL', 0), ('KERB', .93, 1, 'KERB', 1), ('GRASS', .60, 0, 'GRASS', 1),
        ('SAND', .55, 0, 'SAND', 1), ('WALL', .80, 0, 'NULL', 0)]
def surfaces():
    out = []
    for i, (k, fr, valid, ff, vib) in enumerate(SURF):
        out.append('[SURFACE_%d]\nKEY=%s\nFRICTION=%.2f\nDAMPING=0.0\nWAV=\nWAV_PITCH=0\nFF_EFFECT=%s\n'
                   'DIRT_ADDITIVE=%d\nIS_VALID_TRACK=%d\nBLACK_FLAG_TIME=%d\nSIN_HEIGHT=0\nSIN_LENGTH=0\n'
                   'IS_PITLANE=0\nVIBRATION_GAIN=%d\nVIBRATION_LENGTH=%d\n'
                   % (i, k, fr, ff, 0 if valid else 1, valid, 0 if valid else 3, vib * 0, vib * 0))
    return '\n'.join(out)

BLENDER = r'''# Lancé par Blender : blender -b -P build_fbx.py
import bpy, json, math, os
here = os.path.dirname(os.path.abspath(__file__))
name = json.load(open(os.path.join(here, 'markers.json')))['track']
M = json.load(open(os.path.join(here, 'markers.json')))['markers']
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=os.path.join(here, name + '.obj'), forward_axis='NEGATIVE_Z', up_axis='Y')
# --- matériaux : chaque objet reçoit sa texture (PNG à côté du .fbx, dans le dossier de la piste)
TEX = [('1ROAD', 'road'), ('1KERB', 'kerb'), ('1GRASS', 'grass'), ('1WALL', 'wall'), ('BUILDING_WALLS', 'building_wall'),
       ('BUILDING_ROOFS', 'building_roof'), ('TREE_LEAVES', 'tree_leaves'), ('TREE_TRUNKS', 'tree_trunk')]
root = os.path.abspath(os.path.join(here, '..'))
mats = {}
def material(key):
    if key not in mats:
        m = bpy.data.materials.new(key); m.use_nodes = True
        bsdf = m.node_tree.nodes.get('Principled BSDF')
        png = os.path.join(root, key + '.png')
        if bsdf is not None and os.path.isfile(png):
            t = m.node_tree.nodes.new('ShaderNodeTexImage'); t.image = bpy.data.images.load(png)
            m.node_tree.links.new(t.outputs['Color'], bsdf.inputs['Base Color'])
        mats[key] = m
    return mats[key]
for ob in bpy.data.objects:
    if ob.type == 'MESH':
        for pre, key in TEX:
            if ob.name.startswith(pre):
                ob.data.materials.clear(); ob.data.materials.append(material(key)); break
ROT_OFFSET = -math.pi / 2          # si les flèches AC pointent à l'envers dans KsEditor, change cette valeur
for m in M:
    e = bpy.data.objects.new(m['name'], None)
    e.empty_display_type = 'ARROWS'; e.empty_display_size = 2
    e.location = (m['x'], -m['z'], m['y'])
    e.rotation_euler = (0, 0, math.atan2(-m['hz'], m['hx']) + ROT_OFFSET)
    bpy.context.scene.collection.objects.link(e)
out = os.path.abspath(os.path.join(here, '..', name + '.fbx'))
bpy.ops.export_scene.fbx(filepath=out, axis_forward='-Z', axis_up='Y', global_scale=1.0,
                         apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL',
                         object_types={'MESH', 'EMPTY'}, bake_anim=False, mesh_smooth_type='FACE', path_mode='STRIP')
print('FBX écrit :', out)
'''

# ------------------------------------------------------------------ Blender
def detect_blender(explicit=None):
    cands = [explicit, os.environ.get('BLENDER'), shutil.which('blender')]
    for pat in ('C:/Program Files/Blender Foundation/Blender*/blender.exe', '/Applications/Blender.app/Contents/MacOS/Blender',
                os.path.expanduser('~/Applications/Blender.app/Contents/MacOS/Blender'), '/usr/bin/blender', '/snap/bin/blender'):
        cands += sorted(glob.glob(pat), reverse=True)
    return next((c for c in cands if c and os.path.isfile(c)), None)

# ------------------------------------------------------------------ construction (utilisée par la CLI et par le serveur)
def build_track(ll, name='ma_piste', out='build', width=9, step=3, smooth_passes=4, grass=15, kerb=1.0,
                walls=False, grid=4, pits=4, city='', country='', blender=None, scenery=None):
    """ll = liste de (lng, lat, altitude). scenery = dict d'options du décor OSM (ou None). Retourne un dict de résultats."""
    if len(ll) < 2: raise ValueError('Tracé vide.')
    pts = resample(project(ll), step)
    closed = math.dist((pts[0][0], pts[0][2]), (pts[-1][0], pts[-1][2])) < step * 3
    if closed: pts.pop()
    pts = smooth(pts, smooth_passes, closed)
    tg, dist = frames(pts, closed)
    total = dist[-1] + (math.dist((pts[-1][0], pts[-1][2]), (pts[0][0], pts[0][2])) if closed else 0)
    mk = markers(pts, tg, dist, width, grid, pits, closed)

    root = os.path.join(out, name)
    for d in ('source', 'data', 'ui'): os.makedirs(os.path.join(root, d), exist_ok=True)
    w, k, g = width, kerb, grass
    o = Obj()
    o.ribbon('1ROAD', pts, tg, dist, w / 2, -w / 2, 0, closed, w)
    o.ribbon('1KERB_R', pts, tg, dist, w / 2 + k, w / 2, .02, closed, k * 4)
    o.ribbon('1KERB_L', pts, tg, dist, -w / 2, -w / 2 - k, .02, closed, k * 4)
    o.ribbon('1GRASS_R', pts, tg, dist, w / 2 + k + g, w / 2 + k, -.05, closed, 10)
    o.ribbon('1GRASS_L', pts, tg, dist, -w / 2 - k, -w / 2 - k - g, -.05, closed, 10)
    if walls:
        o.wall('1WALL_L', pts, tg, dist, -(w / 2 + k + g), 1.2, closed)
        o.wall('1WALL_R', pts, tg, dist, (w / 2 + k + g), 1.2, closed)
    try:
        import ac_textures
        textures = ac_textures.copy_to(root)
    except Exception as e:
        textures = []
    scen_stats, scen_error = None, None
    if scenery and (scenery.get('buildings', True) or scenery.get('trees', True)):
        try:
            import ac_scenery
            scen_stats = ac_scenery.add_scenery(o, ll[0], pts, w / 2 + k + 2.0, dict(scenery, ll=ll))
        except Exception as e:
            scen_error = str(e)
    src = os.path.join(root, 'source')
    open(os.path.join(src, name + '.obj'), 'w').write('# 1 unité = 1 m, Y haut\n' + '\n'.join(o.lines) + '\n')
    json.dump({'track': name, 'markers': mk}, open(os.path.join(src, 'markers.json'), 'w'), indent=1)
    open(os.path.join(src, 'build_fbx.py'), 'w', encoding='utf-8').write(BLENDER)
    open(os.path.join(root, 'data/surfaces.ini'), 'w').write(surfaces())
    json.dump({'name': name.replace('_', ' ').title(), 'description': 'Piste générée avec AC Track Builder.',
               'tags': ['road', 'circuit' if closed else 'point to point'], 'country': country, 'city': city,
               'length': '%d m' % total, 'width': '%g m' % w, 'pitboxes': str(pits), 'run': 'clockwise',
               'author': '', 'version': '1.0', 'year': 2026}, open(os.path.join(root, 'ui/ui_track.json'), 'w'),
              indent=2, ensure_ascii=False)
    make_map(pts, closed, root)

    res = {'folder': root, 'total': total, 'closed': closed, 'points': len(pts), 'fbx': None, 'blender_error': None,
           'scenery': scen_stats, 'scenery_error': scen_error, 'textures': textures}
    script = os.path.join(src, 'build_fbx.py')
    if blender:
        try:
            r = subprocess.run([blender, '-b', '-P', script], capture_output=True, text=True, timeout=300)
            fbx = os.path.join(root, name + '.fbx')
            if r.returncode == 0 and os.path.isfile(fbx): res['fbx'] = fbx
            else: res['blender_error'] = (r.stderr or r.stdout or 'échec').strip()[-400:]
        except Exception as e:
            res['blender_error'] = str(e)
    return res

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input'); ap.add_argument('--name', default='ma_piste'); ap.add_argument('--out', default='build')
    ap.add_argument('--width', type=float, default=9); ap.add_argument('--step', type=float, default=3)
    ap.add_argument('--smooth', type=int, default=4, help='passes de lissage du tracé')
    ap.add_argument('--grass', type=float, default=15); ap.add_argument('--kerb', type=float, default=1.0)
    ap.add_argument('--walls', action='store_true'); ap.add_argument('--grid', type=int, default=4)
    ap.add_argument('--pits', type=int, default=4); ap.add_argument('--city', default=''); ap.add_argument('--country', default='')
    ap.add_argument('--blender', help='chemin de Blender (sinon détection automatique)')
    ap.add_argument('--scenery', action='store_true', help='ajoute bâtiments et végétation depuis OpenStreetMap')
    ap.add_argument('--margin', type=float, default=200, help='marge autour du tracé pour le décor (m)')
    ap.add_argument('--density', type=float, default=25, help='arbres par hectare dans les forêts')
    a = ap.parse_args()
    try:
        r = build_track(load(a.input), a.name, a.out, a.width, a.step, a.smooth, a.grass, a.kerb, a.walls,
                        a.grid, a.pits, a.city, a.country, detect_blender(a.blender),
                        {'margin': a.margin, 'density': a.density} if a.scenery else None)
    except ValueError as e:
        sys.exit(str(e))
    print('Piste : %.2f km, %s, %d points' % (r['total'] / 1000, 'boucle' if r['closed'] else 'point à point', r['points']))
    print('Dossier :', os.path.abspath(r['folder']))
    if a.scenery: print('Décor :', r['scenery'] or ('indisponible (' + str(r['scenery_error']) + ')'))
    print('Textures :', len(r['textures']), 'fichiers dans le dossier de la piste')
    print('FBX :', r['fbx'] or ('non généré' + (' (' + r['blender_error'] + ')' if r['blender_error'] else ' – Blender introuvable')))

if __name__ == '__main__':
    main()
