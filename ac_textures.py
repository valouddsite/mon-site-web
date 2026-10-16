#!/usr/bin/env python3
"""
Textures procédurales pour AC Track Studio (PNG, sans dépendance, parfaitement répétables / tileables).
  python ac_textures.py            -> (re)génère le dossier textures/
Pour personnaliser : remplace n'importe quel PNG de textures/ par le tien (même nom), il sera copié dans chaque piste.
"""
import os, random, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = ['road', 'kerb', 'grass', 'wall', 'building_wall', 'building_roof', 'tree_leaves', 'tree_trunk']


def write_png(path, w, h, rgb):
    raw = bytearray()
    for y in range(h):
        raw.append(0); raw += rgb[y * w * 3:(y + 1) * w * 3]
    def chunk(t, d):
        return len(d).to_bytes(4, 'big') + t + d + (zlib.crc32(t + d) & 0xffffffff).to_bytes(4, 'big')
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', w.to_bytes(4, 'big') + h.to_bytes(4, 'big') + b'\x08\x02\x00\x00\x00')
                           + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))


def vnoise(S, cells, seed):
    """Bruit de valeur répétable (la grille boucle sur elle-même) sur S×S pixels, valeurs 0..1."""
    rnd = random.Random(seed)
    g = [[rnd.random() for _ in range(cells)] for _ in range(cells)]
    ax = []
    for x in range(S):
        f = x * cells / S; i = int(f); t = f - i
        ax.append((i % cells, (i + 1) % cells, t * t * (3 - 2 * t)))
    out = [0.0] * (S * S)
    for y in range(S):
        y0, y1, ty = ax[y]
        r0, r1 = g[y0], g[y1]
        for x in range(S):
            x0, x1, tx = ax[x]
            a = r0[x0] + (r0[x1] - r0[x0]) * tx
            b = r1[x0] + (r1[x1] - r1[x0]) * tx
            out[y * S + x] = a + (b - a) * ty
    return out


def fbm(S, seed, base=4, octaves=4):
    tot, amp, norm = [0.0] * (S * S), 1.0, 0.0
    for o in range(octaves):
        n = vnoise(S, base * 2 ** o, seed + o * 17)
        tot = [t + amp * v for t, v in zip(tot, n)]
        norm += amp; amp *= .5
    return [t / norm for t in tot]


def mix(a, b, t): return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def render(S, fn):
    buf = bytearray(S * S * 3)
    for y in range(S):
        for x in range(S):
            c = fn(x, y, y * S + x)
            k = (y * S + x) * 3
            buf[k] = min(255, max(0, int(c[0] * 255))); buf[k + 1] = min(255, max(0, int(c[1] * 255))); buf[k + 2] = min(255, max(0, int(c[2] * 255)))
    return buf


def tex_road(S):
    n, f = fbm(S, 1, 6, 4), vnoise(S, S // 2, 2)
    def px(x, y, i):
        v = .20 + .09 * n[i] + .10 * (f[i] - .5) + (.10 if f[i] > .92 else 0)
        c = (v, v, v * 1.03)
        u, w = x / S, 1 - y / S
        line = (.034 < u < .054) or (.946 < u < .966) or (.488 < u < .512 and w < .34)    # bords pleins + axe pointillé
        return mix(c, (.86, .86, .83), .88 * (.85 + .15 * n[i])) if line else c
    return render(S, px)


def tex_kerb(S):
    n = fbm(S, 5, 8, 3)
    def px(x, y, i):
        band = (y * 4 // S) % 2                                          # 4 bandes de 1 m sur 4 m
        base = (.78, .08, .09) if band == 0 else (.88, .88, .86)
        return mix(base, (.3, .27, .24), .35 * (1 - n[i]) ** 2)
    return render(S, px)


def tex_grass(S):
    a, b, f = fbm(S, 9, 4, 4), fbm(S, 11, 16, 2), vnoise(S, S // 2, 12)
    def px(x, y, i):
        c = mix((.15, .30, .08), (.30, .48, .15), a[i])
        c = mix(c, (.42, .40, .18), .25 * max(0, b[i] - .6) * 2.5)
        v = .9 + .2 * (f[i] - .5)
        return (c[0] * v, c[1] * v, c[2] * v)
    return render(S, px)


def tex_wall(S):
    n, f = fbm(S, 21, 6, 4), vnoise(S, S // 2, 22)
    def px(x, y, i):
        v = .52 + .16 * n[i] + .06 * (f[i] - .5)
        c = (v, v, v * .97)
        return mix(c, (.2, .2, .2), .5) if abs(y - S * .5) < 2 else c   # joint horizontal
    return render(S, px)


def tex_building_wall(S):
    n, f = fbm(S, 31, 5, 4), vnoise(S, S // 2, 32)
    def px(x, y, i):
        u, v = x / S, 1 - y / S                                          # 1 tuile = 4 m × 3,2 m (1 étage)
        c = mix((.80, .74, .63), (.66, .60, .50), n[i] * .8)
        c = tuple(k * (.96 + .08 * f[i]) for k in c)
        if .28 < u < .72 and .32 < v < .82:
            if .31 < u < .69 and .35 < v < .79 and abs(u - .5) > .012 and abs(v - .57) > .012:
                g = (v - .35) / .44
                return mix((.10, .16, .24), (.38, .50, .62), g * .55)    # vitre
            return (.92, .92, .90)                                       # cadre
        if .26 < u < .74 and .29 < v <= .32: return (.5, .5, .5)         # appui de fenêtre
        if v < .06: return mix(c, (.35, .33, .30), .6)                   # soubassement
        return c
    return render(S, px)


def tex_building_roof(S):
    n, f = fbm(S, 41, 6, 3), vnoise(S, S, 42)
    rows, cols = 20, 16
    def px(x, y, i):
        r = y * rows // S; off = (S // cols // 2) if r % 2 else 0
        cx = ((x + off) % S) * cols // S
        h = ((r * 73856093) ^ (cx * 19349663)) % 100 / 100
        c = mix((.50, .20, .13), (.62, .30, .20), h)
        c = mix(c, (.28, .24, .22), .35 * n[i])
        e = (y % (S // rows)) / (S // rows)
        return mix(c, (.12, .07, .05), .55) if e > .88 else c
    return render(S, px)


def tex_tree_leaves(S):
    a, b, f = fbm(S, 51, 6, 4), fbm(S, 52, 24, 2), vnoise(S, S // 2, 53)
    def px(x, y, i):
        c = mix((.07, .20, .06), (.20, .42, .12), a[i])
        c = mix(c, (.30, .50, .15), .35 * max(0, b[i] - .55) * 2.2)
        v = .85 + .3 * f[i]
        return (c[0] * v, c[1] * v, c[2] * v)
    return render(S, px)


def tex_tree_trunk(S):
    a, f = fbm(S, 61, 4, 3), vnoise(S, S // 2, 62)
    col = [random.Random(63).random() for _ in range(S // 8)]
    def px(x, y, i):
        s = col[(x // 8) % len(col)]                                     # stries verticales
        v = .5 + .5 * (s * .6 + a[i] * .4)
        c = mix((.17, .11, .07), (.36, .25, .16), v)
        k = .9 + .2 * f[i]
        return (c[0] * k, c[1] * k, c[2] * k)
    return render(S, px)


MAKERS = {'road': (tex_road, 512), 'kerb': (tex_kerb, 256), 'grass': (tex_grass, 512), 'wall': (tex_wall, 256),
          'building_wall': (tex_building_wall, 512), 'building_roof': (tex_building_roof, 512),
          'tree_leaves': (tex_tree_leaves, 256), 'tree_trunk': (tex_tree_trunk, 256)}


def generate(folder, only_missing=False):
    os.makedirs(folder, exist_ok=True)
    for name in NAMES:
        p = os.path.join(folder, name + '.png')
        if only_missing and os.path.isfile(p): continue
        fn, S = MAKERS[name]
        write_png(p, S, S, fn(S))
    return folder


def copy_to(dest, src_dir=None):
    """Copie les textures (génère celles qui manquent) dans le dossier d'une piste. Retourne la liste copiée."""
    src = src_dir or os.path.join(HERE, 'textures')
    generate(src, only_missing=True)
    done = []
    for name in NAMES:
        with open(os.path.join(src, name + '.png'), 'rb') as a, open(os.path.join(dest, name + '.png'), 'wb') as b:
            b.write(a.read())
        done.append(name + '.png')
    return done


if __name__ == '__main__':
    print('Textures générées dans', generate(os.path.join(HERE, 'textures')))
