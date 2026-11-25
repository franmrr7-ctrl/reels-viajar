#!/usr/bin/env python3
"""Genera un Reel vertical (1440x2560, mp4) de Viajar por Viajar a partir de un JSON de guia de viaje.

Uso: python generate.py guia.json salida.mp4

Formato del JSON (ver examples/granada.json). "gancho" es el TITULAR grande de la portada (2-3 lineas de max. 14 caracteres); "titulo" es el destino (rotulo pequeno); cada paso lleva UN dato corto:
{
  "name": "granada-en-diciembre",
  "photo_url": "https://.../foto.jpg",
  "titulo": ["Granada", "en diciembre"],
  "gancho": ["Sin horno y sin prisa", "Se hace sola en la nevera"],
  "lugares": ["La Alhambra", "Albaicin", ...],
  "post_id": 89786,   (el generador toma las fotos del articulo por la API de WordPress; o bien "fotos": [urls])
  "pasos": [
    {"titulo": ["Alhambra", "sin colas"], "texto": ["...", "..."]},
    {"titulo": [...], "texto": [...]},
    {"titulo": [...], "texto": [...]}
  ],
  "cta": "viajarporviajar.com"
}
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import wave

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_TITLE = os.path.join(HERE, "fonts", "CormorantGaramond.ttf")  # titulos de la web
FONT_BODY = os.path.join(HERE, "fonts", "Lora.ttf")                 # texto de la web

# Paleta Viajar por Viajar: cobre del logo (brujula), marron cafe del texto y crema de la web
ORANGE = (190, 112, 62, 255)   # acento: cobre/terracota del logo (kicker, pastillas, vinetas, marco)
TEAL = (238, 172, 120, 255)    # cobre claro: kicker y texto destacado sobre fondo oscuro
INK = (255, 250, 244, 255)     # texto sobre el acento (crema)
CREAM = (255, 250, 244, 255)   # texto principal sobre el fondo oscuro
LIGHT = (26, 18, 16)           # fondo oscuro (marron casi negro) que se mezcla con la foto desenfocada
CORAL = (190, 112, 62, 255)
BRAND = "VIAJAR POR VIAJAR"

CARD_W, CARD_X = 960, 60
CARD_R, BORDER = 44, 6
PANEL_Y, PANEL_H = 170, 700   # tarjeta de foto de las escenas de ingredientes / pasos
HERO_MIN, HERO_MAX = 560, 820  # alto de la tarjeta de la portada segun la proporcion de la foto
SAFE_BOTTOM = 1690            # deja libre el borde inferior de la interfaz de Facebook

SS = 2  # supersampling del texto: se dibuja al doble y se reduce con Lanczos


def font(size, weight="Bold", kind="title"):
    if kind == "title":
        f = ImageFont.truetype(FONT_TITLE, int(size * 1.2 * SS))  # Cormorant tiene poca altura de x: se amplia
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    else:
        f = ImageFont.truetype(FONT_BODY, int(size * SS))
        try:  # Lora variable: peso 560
            f.set_variation_by_axes([560])
        except Exception:
            pass
    return f


def wrap(draw, text, fnt, maxw):
    """Ajusta el texto al ancho; evita lineas finales de una sola palabra corta (viudas como 'vel / 3')."""
    best = _wrap(draw, text, fnt, maxw)
    for k in (0.9, 0.82, 0.74, 0.66):
        if len(best) < 2 or len(best[-1]) >= 9:
            break
        best = _wrap(draw, text, fnt, maxw * k)
    return best


def _wrap(draw, text, fnt, maxw):
    lines, cur = [], ""
    # une la cifra con su unidad (20 €, 3 km...) para que no se separen al saltar de linea
    text = re.sub(r"(\d) (?=(?:€|°|º|min\b|h\b|km\b|días\b|dias\b|euros\b))", lambda m: m.group(1) + "\u00a0", text)
    for word in text.split(" "):
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=fnt) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


CHIP_RE = [
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|euros?\b)", re.I), lambda m: f"{m.group(1)} €"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:d[ií]as?\b)", re.I), lambda m: f"{m.group(1)} DÍAS" if m.group(1) != "1" else "1 DÍA"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:horas?\b|h\b)", re.I), lambda m: f"{m.group(1)} H"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:min(?:utos?)?\b)", re.I), lambda m: f"{m.group(1)} MIN"),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*km\b", re.I), lambda m: f"{m.group(1)} KM"),
]


def chips_from(lines, limit=4):
    """Extrae precio / duracion / distancia de las lineas del apartado."""
    found, seen = [], set()
    for ln in lines:
        for rx, fmt in CHIP_RE:
            for m in rx.finditer(ln):
                c = fmt(m)
                if c not in seen:
                    seen.add(c)
                    found.append(c)
    return found[:limit]


class _SD:
    """Dibujante que escala coordenadas y tamanos por SS."""
    def __init__(self, d):
        self.d = d

    def text(self, xy, t, font=None, fill=None, anchor=None):
        try:  # cifras de caja alta (lnum): en Cormorant las cifras por defecto son "elzevirianas" y bajan de la linea
            self.d.text((xy[0] * SS, xy[1] * SS), t, font=font, fill=fill, anchor=anchor, features=["lnum"])
        except Exception:
            self.d.text((xy[0] * SS, xy[1] * SS), t, font=font, fill=fill, anchor=anchor)

    def rectangle(self, xy, fill=None):
        self.d.rectangle(tuple(v * SS for v in xy), fill=fill)

    def ellipse(self, xy, fill=None):
        self.d.ellipse(tuple(v * SS for v in xy), fill=fill)

    def rounded_rectangle(self, xy, radius=0, fill=None):
        self.d.rounded_rectangle(tuple(v * SS for v in xy), radius=radius * SS, fill=fill)

    def textlength(self, t, font=None):
        return self.d.textlength(t, font=font) / SS


def fit_title(d, lines, size, maxw=960):
    while size > 60 and any(d.textlength(l, font=font(size)) > maxw for l in lines):
        size -= 4
    return size


def text_layer(path, kicker, title_lines, body, body_size=46, title_size=120, bullets=True,
               y0=720, chips=None, body_fill=CREAM, draw=True, only=None, skip_head=False):
    """Dibuja la capa de texto. Con draw=False solo mide y devuelve la y final (sin escribir nada)."""
    big = Image.new("RGBA", (W * SS, H * SS) if draw else (8, 8), (0, 0, 0, 0))
    d = _SD(ImageDraw.Draw(big))
    # rotulo de marca: banda naranja como la del logo
    bf = font(54)
    tw = d.textlength(BRAND, font=bf)
    if not skip_head:
        d.rounded_rectangle((60, 62, 60 + tw + 56, 62 + 84), radius=16, fill=ORANGE)
        d.text((88, 62 + 42), BRAND, font=bf, fill=INK, anchor="lm")
    y = y0
    if kicker:
        if not skip_head:
            d.text((60, y), kicker, font=font(66), fill=TEAL)
        y += 96
    if title_lines:
        ts = fit_title(d, title_lines, title_size)
        for ln in title_lines:
            if not skip_head:
                d.text((60, y), ln, font=font(ts), fill=CREAM)
            y += int(ts * 1.2)
        y += 40
    if chips:
        cf = font(60)
        x = 60
        for c in chips:
            cw = d.textlength(c, font=cf)
            if not skip_head:
                d.rounded_rectangle((x, y, x + cw + 52, y + 84), radius=42, fill=ORANGE)
                d.text((x + 26, y + 42), c, font=cf, fill=INK, anchor="lm")
            x += cw + 52 + 16
        y += 84 + 28
    bfont = font(body_size, kind="body")
    for bi, item in enumerate(body):
        lines = wrap(d, item, bfont, 860 if bullets else 960)
        show = only is None or only == bi
        if bullets and show:
            d.ellipse((60, y + body_size * 0.38, 60 + 20, y + body_size * 0.38 + 20), fill=ORANGE)
        for ln in lines:
            if show:
                d.text((105 if bullets else 60, y), ln, font=bfont, fill=body_fill)
            y += int(body_size * 1.32)
        y += 20 if bullets else 8
    if draw:
        big.resize((W, H), Image.LANCZOS).save(path)
    return y


def as_lines(v):
    if isinstance(v, str):
        return [s for s in v.replace("\\n", "\n").split("\n") if s.strip()]
    return list(v)


def cover(img, w, h):
    """Escala la foto para cubrir w x h y recorta al centro. Las fotos de la web son pequenas:
    se amplian con Lanczos y se afinan un poco para que no se vean blandas."""
    s = max(w / img.width, h / img.height)
    nw, nh = max(w, int(round(img.width * s))), max(h, int(round(img.height * s)))
    out = img.resize((nw, nh), Image.LANCZOS)
    if s > 1.05:
        out = out.filter(ImageFilter.UnsharpMask(radius=1.4, percent=55 if s > 1.35 else 35, threshold=2))
    l, t = (nw - w) // 2, (nh - h) // 2
    return out.crop((l, t, l + w, t + h))


def make_bg(base, card_y, card_h):
    """Fondo: foto desenfocada y oscurecida, con sombra y marco naranja alrededor de la tarjeta de foto."""
    bg = base.copy().convert("RGBA")
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((CARD_X, card_y + 18, CARD_X + CARD_W, card_y + card_h + 18),
                                         radius=CARD_R, fill=(0, 0, 0, 120))
    bg = Image.alpha_composite(bg, sh.filter(ImageFilter.GaussianBlur(30)))
    ImageDraw.Draw(bg).rounded_rectangle((CARD_X - BORDER, card_y - BORDER, CARD_X + CARD_W + BORDER,
                                          card_y + card_h + BORDER), radius=CARD_R + BORDER, fill=ORANGE)
    return bg.convert("RGB")


def make_mask(card_h):
    m = Image.new("L", (CARD_W * 2, card_h * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, CARD_W * 2 - 1, card_h * 2 - 1), radius=CARD_R * 2, fill=255)
    return m.resize((CARD_W, card_h), Image.LANCZOS)


def fetch_photo(url0, path):
    # WordPress guarda miniaturas "nombre-300x200.jpg": probamos primero el original a maxima resolucion
    cands = [re.sub(r"-\d{2,4}x\d{2,4}(\.[A-Za-z]+)$", r"\1", url0), url0]
    blob = None
    for u in dict.fromkeys(cands):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
            break
        except Exception as ex:
            print("foto no disponible:", u, ex)
    if blob is None:
        raise RuntimeError("sin foto: " + url0)
    open(path, "wb").write(blob)
    return Image.open(path).convert("RGB")


def article_photos(post_id, exclude, n=5):
    """Fotos del propio articulo (API de WordPress): grandes, sin duplicados y repartidas a lo largo del texto."""
    import html
    try:
        req = urllib.request.Request(
            f"https://viajarporviajar.com/wp-json/wp/v2/posts/{post_id}?_fields=content",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            content = json.load(r)["content"]["rendered"]
    except Exception as ex:
        print("no se pudo leer el articulo:", ex)
        return []
    key = lambda u: re.sub(r"-\d{2,4}x\d{2,4}(?=\.)", "", u).rsplit("/", 1)[-1].lower()
    seen, out = {key(exclude)}, []
    for m in re.finditer(r"<img[^>]*>", content):
        tag = m.group(0)
        src = re.search(r'\ssrc="([^"]+)"', tag)
        wd = re.search(r'\swidth="(\d+)"', tag)
        if not src or (wd and int(wd.group(1)) < 600):
            continue
        u = html.unescape(src.group(1))
        if key(u) in seen or "logo" in u.lower() or "avatar" in u.lower():
            continue
        seen.add(key(u))
        out.append(u)
    if len(out) > n:
        out = [out[round(i * (len(out) - 1) / (n - 1))] for i in range(n)]
    return out


def main(src, dst):
    data = json.load(open(src, encoding="utf-8"))
    tmp = tempfile.mkdtemp(prefix="reel_")

    photo = fetch_photo(data["photo_url"], os.path.join(tmp, "foto"))
    # una foto distinta del propio articulo para cada escena (si faltan, se repite la portada)
    extra = []
    fotos = data.get("fotos") or (article_photos(data["post_id"], data["photo_url"]) if data.get("post_id") else [])
    for i, u in enumerate(fotos[:6]):
        try:
            extra.append(fetch_photo(u, os.path.join(tmp, f"extra{i}")))
        except Exception as ex:
            print("foto extra no disponible:", u, ex)
    # fotos unicas del articulo; si faltan (articulos antiguos sin fotos propias) se reutilizan con otro encuadre
    uniq = [photo] + extra
    focus = [(0.5, 0.5, 1.0), (0.12, 0.45, 1.35), (0.88, 0.55, 1.35), (0.5, 0.15, 1.35), (0.5, 0.9, 1.35), (0.25, 0.7, 1.5)]
    scene_photos = []
    for si_ in range(5):
        k = si_ // len(uniq)
        img = uniq[si_ % len(uniq)]
        if k == 0:
            scene_photos.append(img)
        else:
            fx, fy, z = focus[(si_ + k) % len(focus)]
            if z == 1.0:
                fx, fy, z = focus[1]
            cw, chh = img.width / z, img.height / z
            x0, y0 = (img.width - cw) * fx, (img.height - chh) * fy
            scene_photos.append(img.crop((int(x0), int(y0), int(x0 + cw), int(y0 + chh))))

    def make_base(img):
        """Fondo de cada escena: su propia foto muy desenfocada y oscurecida."""
        sc = max(W / img.width, H / img.height)
        bb = img.resize((int(img.width * sc) + 1, int(img.height * sc) + 1), Image.LANCZOS)
        bb = bb.crop(((bb.width - W) // 2, (bb.height - H) // 2, (bb.width - W) // 2 + W,
                      (bb.height - H) // 2 + H)).filter(ImageFilter.GaussianBlur(40))
        return Image.blend(bb, Image.new("RGB", (W, H), LIGHT), 0.74)

    pasos = data["pasos"][:3]
    ing = as_lines(data["lugares"])[:5]
    cta = data.get("cta", "viajarporviajar.com")
    titulo, gancho = as_lines(data["titulo"]), as_lines(data["gancho"])
    destino = " ".join(titulo).upper()

    def words(lines):
        return sum(len(l.split()) for l in lines)

    # Estructura (≈20 s): GANCHO grande -> lista que se revela linea a linea -> 3 datos (uno por escena) -> cierre
    # que repite la foto y el encuadre de la portada (el bucle invita a volver a verlo).
    scenes = [
        dict(name="s1", kind="hook", dur=3.0, kicker=destino, title=gancho, body=[], tsz=150, bsz=60,
             bul=False, photo=scene_photos[0], zoom=0.07),
        dict(name="s2", kind="list", dur=round(0.6 + 0.55 * (len(ing) - 1) + 1.5, 2), kicker="NO TE PUEDES PERDER",
             title=[], body=ing, tsz=110, bsz=74, bul=True, photo=scene_photos[1], pan=(0.9, 0.3)),
    ]
    for i, p in enumerate(pasos, 1):
        body = as_lines(p["texto"])[:2]
        dur = max(2.8, min(4.2, 1.5 + 0.24 * words(body)))
        scenes.append(dict(name=f"s{i+2}", kind="panel", dur=dur, kicker=f"{i} DE {len(pasos)}",
                           title=as_lines(p["titulo"]), body=body, tsz=150, bsz=84, bul=False,
                           photo=scene_photos[i + 1] if i + 1 < len(scene_photos) else photo,
                           pan=((0.4, 0.8) if i % 2 else (0.8, 0.3)), chips=chips_from(as_lines(p["texto"]))))
    scenes.append(dict(name="s6", kind="hook", dur=3.0, kicker=cta.upper(), title=["Guía", "completa"],
                       body=["Todos los datos, precios y mapa", "Enlace en el primer comentario"], tsz=150, bsz=58,
                       bul=False, photo=photo, zoom=0.07))
    total = sum(sc["dur"] for sc in scenes)

    bar_path = os.path.join(tmp, "bar.png")
    Image.new("RGB", (W, 12), ORANGE[:3]).save(bar_path)

    files, offset = [], 0.0
    for si, sc in enumerate(scenes):
        name, dur, kind = sc["name"], sc["dur"], sc["kind"]
        kicker, title, body, tsz, bsz, bul = sc["kicker"], sc["title"], sc["body"], sc["tsz"], sc["bsz"], sc["bul"]
        chips = sc.get("chips")
        sphoto = sc["photo"]
        bodyfill = TEAL if kind == "hook" else CREAM
        if kind == "hook":
            # texto arriba (el gancho es lo primero que se lee) y la foto debajo, ocupando el resto
            while True:
                end = text_layer(None, kicker, title, body, body_size=bsz, title_size=tsz, bullets=bul, y0=190,
                                 draw=False, body_fill=bodyfill)
                cy = end + 24
                ch = int(min(900, SAFE_BOTTOM - cy)) // 2 * 2
                if ch >= 560 or tsz <= 90:
                    break
                tsz -= 10
            y0 = 190
        else:
            cy, ch = PANEL_Y, PANEL_H
            y0 = PANEL_Y + PANEL_H + 56
            while bsz > 34 and text_layer(None, kicker, title, body, body_size=bsz, title_size=tsz, bullets=bul,
                                          y0=y0, chips=chips, draw=False, body_fill=bodyfill) > SAFE_BOTTOM:
                bsz -= 2
        bg = os.path.join(tmp, f"bg_{name}.png")
        make_bg(make_base(sphoto), cy, ch).save(bg)
        mask = os.path.join(tmp, f"mask_{name}.png")
        make_mask(ch).save(mask)
        reveal = kind == "list"
        layer = os.path.join(tmp, f"{name}.png")
        text_layer(layer, kicker, title, body, body_size=bsz, title_size=tsz, bullets=bul, y0=y0, chips=chips,
                   body_fill=bodyfill, only=-1 if reveal else None)
        rlayers = []
        if reveal:
            for k in range(len(body)):
                rp = os.path.join(tmp, f"{name}_r{k}.png")
                text_layer(rp, kicker, title, body, body_size=bsz, title_size=tsz, bullets=bul, y0=y0, body_fill=bodyfill,
                           only=k, skip_head=True)
                rlayers.append((rp, 0.6 + 0.55 * k))
        src_img = cover(sphoto, int(CARD_W * 1.12 * 2), int(ch * 1.12 * 2))  # al doble: el zoom/paneo se reduce despues, sin temblor
        src_path = os.path.join(tmp, f"{name}_foto.jpg")
        src_img.save(src_path, quality=95)
        out = os.path.join(tmp, f"{name}.mp4")
        first = si == 0
        # la portada (escena 1) lleva el texto desde el primer fotograma: Facebook lo usa como miniatura
        text_fx = "format=rgba" if first else "format=rgba,fade=t=in:st=0.1:d=0.35:alpha=1"
        slide = "0" if first else "if(lt(t,0.45),36*(1-t/0.45),0)"
        fin = "" if first else "fade=t=in:st=0:d=0.2,"
        if kind == "hook":
            z = sc["zoom"]
            motion = (f"scale=w='iw*(1+{z}*t/{dur})':h=-1:eval=frame:flags=lanczos,crop={CARD_W*2}:{ch*2},"
                      f"scale={CARD_W}:{ch}:flags=lanczos+accurate_rnd")
        else:
            mx = src_img.width - CARD_W * 2
            x0, x1 = int(mx * sc["pan"][0]), int(mx * sc["pan"][1])
            motion = (f"crop={CARD_W*2}:{ch*2}:x='{x0}+({x1}-{x0})*t/{dur}':y='(ih-oh)/2',"
                      f"scale={CARD_W}:{ch}:flags=lanczos+accurate_rnd")
        n_in = 5
        fc = (f"[2:v]{text_fx}[t];[3:v]format=rgba[b];[4:v]format=gray[m];"
              f"[1:v]{motion},eq=saturation=1.12:contrast=1.05,unsharp=5:5:0.4[c0];[c0][m]alphamerge[c];"
              f"[0:v][c]overlay={CARD_X}:{cy}:format=gbrp[a];"
              f"[a][t]overlay=0:'{slide}':format=gbrp[x0];")
        prev = "x0"
        for k, (rp, st) in enumerate(rlayers):
            fc += (f"[{n_in+k}:v]format=rgba,fade=t=in:st={st}:d=0.3:alpha=1[r{k}];"
                   f"[{prev}][r{k}]overlay=0:0:format=gbrp[x{k+1}];")
            prev = f"x{k+1}"
        fc += (f"[{prev}][b]overlay=x='-{W}+{W}*({offset}+t)/{total}':y=0:format=gbrp,"
               f"scale=1440:2560:flags=lanczos+accurate_rnd+full_chroma_int:out_color_matrix=bt709,format=yuv420p,"
               f"{fin}fade=t=out:st={dur-0.2}:d=0.2[v]")
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for p in [bg, src_path, layer, bar_path, mask] + [r[0] for r in rlayers]:
            cmd += ["-framerate", "30", "-loop", "1", "-t", str(dur), "-i", p]
        cmd += ["-filter_complex", fc, "-map", "[v]", "-r", "30",
                "-c:v", "libx264", "-crf", "12", "-preset", "medium", "-tune", "film", "-g", "60", "-pix_fmt", "yuv420p",
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", out]
        subprocess.run(cmd, check=True)
        files.append(out)
        offset += dur

    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for p in files:
            f.write(f"file '{p}'\n")
    silent = os.path.join(tmp, "silent.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", silent], check=True)

    # musica: pista CC0 real (carpeta music/) rotando por receta; si falla, musica sintetica
    key = sum(ord(c) for c in str(data.get("name", data["titulo"])))
    tracks = sorted(glob.glob(os.path.join(HERE, "music", "*.m4a")))
    wav = os.path.join(tmp, "musica.wav")
    done = False
    if tracks:
        tr = tracks[key % len(tracks)]
        off = 4 + (key * 7) % 50  # segundo de inicio distinto en cada receta
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(off), "-i", tr,
                            "-t", str(total + 0.5), "-ac", "2", "-ar", "44100", wav])
        done = r.returncode == 0 and os.path.exists(wav)
    if not done:
        make_music(wav, total + 0.5, variant=key % len(MUSIC_PRESETS))
    part = dst + ".part.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", wav,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-af", f"loudnorm=I=-18:TP=-2:LRA=7,afade=t=in:st=0:d=0.8,afade=t=out:st={total-1.5}:d=1.5",
                    "-t", str(total), "-movflags", "+faststart", part], check=True)
    os.replace(part, dst)
    print(f"ok: {dst} ({total:.1f} s)")


MUSIC_PRESETS = [
    # (bpm, acordes (4 por ciclo), raices, swing del hi-hat, nombre)
    (84, [[60, 64, 67, 71], [57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 62, 64]], [36, 33, 29, 31], 0.14),  # Cmaj7 Am7 Fmaj7 G6
    (92, [[62, 65, 69, 72], [58, 62, 65, 69], [55, 58, 62, 65], [57, 61, 64, 67]], [38, 34, 31, 33], 0.10),  # Dm7 Bbmaj7 Gm7 A7
    (78, [[65, 69, 72, 76], [62, 65, 69, 72], [58, 62, 65, 69], [60, 64, 67, 70]], [41, 38, 34, 36], 0.17),  # Fmaj7 Dm7 Bbmaj7 C7
    (88, [[67, 71, 74, 78], [64, 67, 71, 74], [60, 64, 67, 71], [62, 66, 69, 72]], [43, 40, 36, 38], 0.12),  # Gmaj7 Em7 Cmaj7 D7
]


def make_music(path, seconds, sr=32000, variant=0):
    """Base lo-fi tranquila generada por codigo (sin derechos): piano electrico, bajo y percusion suave."""
    import numpy as np

    bpm, chords, roots, swing = MUSIC_PRESETS[variant % len(MUSIC_PRESETS)]
    beat = 60.0 / bpm
    n = int(seconds * sr)
    t = np.arange(n) / sr
    mix = np.zeros(n)
    rng = np.random.default_rng(7)

    def midi(m):
        return 440.0 * 2 ** ((m - 69) / 12)

    # una barra de 4 tiempos por acorde

    def add(start, sig):
        i = int(start * sr)
        if i >= n:
            return
        seg = sig[: n - i]
        mix[i : i + len(seg)] += seg

    def epiano(freq, dur, vel):
        tt = np.arange(int(dur * sr)) / sr
        env = np.exp(-2.6 * tt) * (1 - np.exp(-tt * 90))
        mod = np.sin(2 * np.pi * freq * tt) * 1.4 * np.exp(-5 * tt)
        return vel * env * (np.sin(2 * np.pi * freq * tt + mod) + 0.25 * np.sin(2 * np.pi * freq * 2 * tt) * np.exp(-4 * tt))

    bar = 0
    while bar * 4 * beat < seconds:
        k = bar % 4
        t0 = bar * 4 * beat
        notes = chords[k]
        # acorde en el tiempo 1 y sincopado en el "y" del 2, con rasgueo suave
        for hit, vel in ((0.0, 0.16), (1.5, 0.11), (2.75, 0.09)):
            for j, m in enumerate(notes):
                add(t0 + hit * beat + j * 0.018, epiano(midi(m), 2.2, vel))
        # bajo en 1 y 3
        for hit in (0.0, 2.0):
            d = np.arange(int(1.4 * sr)) / sr
            f = midi(roots[k])
            add(t0 + hit * beat, 0.34 * np.exp(-2.2 * d) * (np.sin(2 * np.pi * f * d) + 0.3 * np.sin(4 * np.pi * f * d)))
        # bombo en 1 y 3
        for hit in (0.0, 2.0):
            d = np.arange(int(0.3 * sr)) / sr
            add(t0 + hit * beat, 0.42 * np.exp(-14 * d) * np.sin(2 * np.pi * (48 + 90 * np.exp(-30 * d)) * d))
        # caja suave en 2 y 4
        for hit in (1.0, 3.0):
            d = np.arange(int(0.22 * sr)) / sr
            noise = rng.standard_normal(len(d))
            noise = np.convolve(noise, np.ones(6) / 6, mode="same")
            add(t0 + hit * beat, 0.16 * np.exp(-18 * d) * noise)
        # hi-hat en corcheas con swing
        for q in range(8):
            off = (q * 0.5) + (swing if q % 2 else 0.0)
            d = np.arange(int(0.06 * sr)) / sr
            noise = rng.standard_normal(len(d))
            noise = noise - np.convolve(noise, np.ones(4) / 4, mode="same")
            add(t0 + off * beat, (0.07 if q % 2 else 0.045) * np.exp(-60 * d) * noise)
        bar += 1

    # filtro paso bajo suave (sonido calido) y normalizacion
    kern = np.ones(3) / 3
    mix = np.convolve(mix, kern, mode="same")
    peak = np.max(np.abs(mix)) or 1.0
    mix = mix / peak * 0.55
    pcm = (mix * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
