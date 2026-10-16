#!/usr/bin/env python3
"""
AC Track Studio – serveur local : sert le site et appelle le système Python (et Blender s'il est installé).

  python server.py                 # ouvre http://localhost:8765
  python server.py --port 9000 --blender "/chemin/vers/blender"
"""
import argparse, io, json, os, re, shutil, tempfile, threading, webbrowser, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import ac_track_builder as B

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = None
MAX_BODY = 60 * 1024 * 1024


def zip_folder(folder):
    buf = io.BytesIO()
    base = os.path.dirname(folder)
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for dp, _, files in os.walk(folder):
            for f in files:
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, base))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('[%s] %s' % (self.log_date_time_string(), fmt % args))

    def _headers(self, code, ctype, length, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.send_header('Access-Control-Allow-Origin', '*')          # permet aussi d'ouvrir index.html directement
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Expose-Headers', 'X-AC-Fbx, X-AC-Total, X-AC-Closed, X-AC-Blender-Error, X-AC-Scenery')
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self._headers(code, 'application/json', len(b))
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._headers(204, 'text/plain', 0, {'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'})

    def do_GET(self):
        if self.path.split('?')[0] == '/api/status':
            return self._json(200, {'ok': True, 'blender': BLENDER, 'version': 1})
        if self.path.split('?')[0] in ('/', '/index.html'):
            b = open(os.path.join(HERE, 'index.html'), 'rb').read()
            self._headers(200, 'text/html; charset=utf-8', len(b))
            return self.wfile.write(b)
        self._json(404, {'error': 'introuvable'})

    def do_POST(self):
        if self.path != '/api/build':
            return self._json(404, {'error': 'introuvable'})
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {'error': 'Requête vide ou trop grosse.'})
        tmp = tempfile.mkdtemp(prefix='actrack_')
        try:
            d = json.loads(self.rfile.read(n))
            o = d.get('options', {})
            sc = d.get('scenery', {}) or {}
            name = re.sub(r'[^a-z0-9_]+', '_', str(d.get('name', 'ma_piste')).lower()).strip('_') or 'ma_piste'
            ll = [(float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0) for c in d['coords']]
            r = B.build_track(
                ll, name=name, out=tmp,
                width=float(o.get('width', 9)), step=max(1.0, float(o.get('step', 3))),
                smooth_passes=int(o.get('smooth', 5)), grass=float(o.get('grass', 15)), kerb=float(o.get('kerb', 1)),
                walls=bool(o.get('walls', False)), grid=int(o.get('grid', 4)), pits=int(o.get('pits', 4)),
                city=str(o.get('city', '')), country=str(o.get('country', '')), blender=BLENDER,
                scenery=({'buildings': bool(sc.get('buildings', True)), 'trees': bool(sc.get('trees', True)),
                          'margin': min(600.0, float(sc.get('margin', 200))), 'density': float(sc.get('density', 25)),
                          'max_buildings': int(sc.get('max_buildings', 3000)), 'max_trees': int(sc.get('max_trees', 4000))}
                         if sc.get('enabled') else None))
            data = zip_folder(r['folder'])
            self._headers(200, 'application/zip', len(data), {
                'Content-Disposition': 'attachment; filename="%s.zip"' % name,
                'X-AC-Fbx': '1' if r['fbx'] else '0', 'X-AC-Total': str(int(r['total'])),
                'X-AC-Closed': '1' if r['closed'] else '0',
                'X-AC-Blender-Error': quote((r['blender_error'] or '')[:300]),
                'X-AC-Scenery': quote(json.dumps({'stats': r['scenery'], 'error': r['scenery_error']}))})
            self.wfile.write(data)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self._json(400, {'error': str(e)})
        except Exception as e:
            self._json(500, {'error': 'Erreur serveur : %s' % e})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    global BLENDER
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--blender', help='chemin de Blender (sinon détection automatique)')
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()
    BLENDER = B.detect_blender(a.blender)
    host = os.environ.get('HOST', '127.0.0.1')
    if os.environ.get('PORT'):
        a.port = int(os.environ['PORT'])
        host = '0.0.0.0'
    srv = ThreadingHTTPServer((host, a.port), Handler)
    url = ('http://localhost:%d' % a.port) if host == '127.0.0.1' else ('http://%s:%d' % (host, a.port))
    print('AC Track Studio : ' + url)
    print('Blender : ' + (BLENDER or 'introuvable (le zip contiendra le script build_fbx.py à lancer toi-même)'))
    print('Ctrl+C pour arrêter.')
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nArrêt.')


if __name__ == '__main__':
    main()
