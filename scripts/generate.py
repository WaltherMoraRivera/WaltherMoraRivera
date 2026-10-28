#!/usr/bin/env python3
"""Genera las tarjetas SVG del README de perfil.

Lee data/profile.json (textos editables) y consulta la API de GitHub para los datos vivos
(commits, lenguajes, calendario de contribuciones). Solo usa la biblioteca estándar.

    python scripts/generate.py

Token: variable GH_TOKEN / GITHUB_TOKEN, o `gh auth token` si no hay ninguna.
Escribe assets/*.svg y preview.html (la vista previa local no se publica).
"""
import base64
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

W = 840          # ancho de cada SVG, incluido el margen
M = 6            # margen transparente alrededor de cada tarjeta
CARD_W = W // 2  # ancho de cada tarjeta en pares (proyectos)
PX = M + 16      # inicio del panel interior dentro del marco
IX = PX + 24     # inicio del contenido dentro del panel
IW = W - 2 * IX  # ancho del contenido de las tarjetas de ancho completo

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

LANG_COLORS = {
    "Python": "#3572A5", "TypeScript": "#1fa7c9", "JavaScript": "#d4a72c", "C++": "#f34b7d",
    "HTML": "#e34c26", "CSS": "#7b5ea7", "PLpgSQL": "#336790", "GDScript": "#355570",
}
LANG_LABELS = {"PLpgSQL": "PL/pgSQL"}

# Paleta: claro por defecto, oscuro con prefers-color-scheme. Los brillos son degradados radiales.
STYLE = """<style>
.f{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif}
.m{font-family:ui-monospace,SFMono-Regular,"SF Mono",Consolas,"Liberation Mono",monospace}
.frame{fill:#f6f8fa;stroke:#bcd0e6}
.panel{fill:#fff;fill-opacity:.72;stroke:#c3d4e8}
.pline{fill:none;stroke:#c3d4e8}
.tile{fill:#fff;fill-opacity:.85;stroke:#c3d4e8}
.chip{fill:#eef3f9;stroke:#cdd9e6}
.pill{fill:#fff;fill-opacity:.6;stroke:#7fb2f0}
.track{fill:#e2e9f1}.trk{stroke:#e2e9f1}
.t1{fill:#1f2328}.t2{fill:#59636e}.ac{fill:#0969da}
.c-blue{fill:#0969da}.c-cyan{fill:#0e7490}.c-purple{fill:#8250df}.c-green{fill:#1a7f37}
.n1{stop-color:#0969da}.n2{stop-color:#0e7490}
.sep{stroke:#d0dbe8}.ring{stroke:#1f6feb;fill:none}
.d-prod{fill:#1a7f37}.d-proto{fill:#bf8700}
.gba{stop-color:#3b82f6;stop-opacity:.16}.gbb{stop-color:#3b82f6;stop-opacity:0}
.gga{stop-color:#22c55e;stop-opacity:.10}.ggb{stop-color:#22c55e;stop-opacity:0}
.gta{stop-color:#14b8a6;stop-opacity:.14}.gtb{stop-color:#14b8a6;stop-opacity:0}
.gpa{stop-color:#8b5cf6;stop-opacity:.12}.gpb{stop-color:#8b5cf6;stop-opacity:0}
.h0{fill:#e6ebf1}.h1{fill:#c3dcff}.h2{fill:#86b8ff}.h3{fill:#4a90ee}.h4{fill:#1f6feb}
@media (prefers-color-scheme:dark){
.frame{fill:#0d131b;stroke:#2a4a70}
.panel{fill:#070a0f;fill-opacity:.78;stroke:#26466b}
.pline{stroke:#26466b}
.tile{fill:#090d13;fill-opacity:.9;stroke:#2b4f78}
.chip{fill:#0e151e;stroke:#27415f}
.pill{fill:#0b121b;fill-opacity:.7;stroke:#2f6bb3}
.track{fill:#1d242e}.trk{stroke:#1d242e}
.t1{fill:#f0f6fc}.t2{fill:#9198a1}.ac{fill:#58a6ff}
.c-blue{fill:#58a6ff}.c-cyan{fill:#22d3ee}.c-purple{fill:#a371f7}.c-green{fill:#3fb950}
.n1{stop-color:#7dd3fc}.n2{stop-color:#38bdf8}
.sep{stroke:#26364a}.ring{stroke:#2f81f7}
.d-prod{fill:#3fb950}.d-proto{fill:#d29922}
.gba{stop-opacity:.38}.gga{stop-opacity:.22}.gta{stop-opacity:.30}.gpa{stop-opacity:.26}
.h0{fill:#1b222c}.h1{fill:#0e3a73}.h2{fill:#1456b3}.h3{fill:#2f81f7}.h4{fill:#79c0ff}}
</style>"""

GLOW_IDS = (("gb", 0.10, 0.0), ("gg", 0.60, 0.0), ("gt", 0.98, 1.0), ("gp", 0.38, 1.05))


# ───────────────────────── utilidades ─────────────────────────

def esc(s):
    return html.escape(str(s), quote=True)


def text_width(s, size, bold=False):
    """Ancho estimado en px. Conservador: las fuentes varían entre sistemas."""
    return len(s) * size * (0.6 if bold else 0.55)


def wrap(s, size, width, bold=False):
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and text_width(trial, size, bold) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fmt_date(iso):
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"{d.day} {MESES[d.month - 1]}"


def t(x, y, s, size=13, cls="t1", weight=None, anchor=None, extra="", mono=False):
    font = "m" if mono else "f"
    attrs = f'x="{x}" y="{y}" font-size="{size}" class="{font} {cls}"'
    if weight:
        attrs += f' font-weight="{weight}"'
    if anchor:
        attrs += f' text-anchor="{anchor}"'
    return f"<text {attrs} {extra}>{esc(s)}</text>"


def svg(w, h, title, body, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>{STYLE}'
        f"<defs>{defs}</defs>{body}</svg>\n"
    )


def shell(w, h, rx=16):
    """Marco con brillos de color. Devuelve (defs, body)."""
    fw, fh = w - 2 * M, h - 2 * M
    defs = f'<clipPath id="fc"><rect x="{M}" y="{M}" width="{fw}" height="{fh}" rx="{rx}"/></clipPath>'
    defs += "".join(
        f'<radialGradient id="{i}"><stop offset="0" class="{i}a"/><stop offset="1" class="{i}b"/></radialGradient>'
        for i, _, _ in GLOW_IDS
    )
    rx_, ry_ = min(w * 0.30, 280), h * 0.85
    glows = "".join(
        f'<ellipse cx="{w * fx:.0f}" cy="{h * fy:.0f}" rx="{rx_:.0f}" ry="{ry_:.0f}" fill="url(#{i})"/>'
        for i, fx, fy in GLOW_IDS
    )
    body = (
        f'<rect x="{M}.5" y="{M}.5" width="{fw - 1}" height="{fh - 1}" rx="{rx}" class="frame" stroke-width="1"/>'
        f'<g clip-path="url(#fc)">{glows}</g>'
    )
    return defs, body


def panel(h):
    return f'<rect x="{PX}.5" y="{PX}.5" width="{W - 2 * PX - 1}" height="{h - 1}" rx="12" class="panel" stroke-width="1"/>'


def layout_chips(labels, x0, max_x, size=12, pad=9, gap=6):
    x, row, out = x0, 0, []
    for label in labels:
        w = text_width(label, size) * 0.95 + 2 * pad
        if x + w > max_x and x > x0:
            row += 1
            x = x0
        out.append((label, x, row, w))
        x += w + gap
    return out, row + 1


def draw_chips(items, y0, h=24, row_gap=6, size=12, pill=False, tcls="t1"):
    parts = []
    for label, x, row, w in items:
        y = y0 + row * (h + row_gap)
        rx, cls = (h / 2, "pill") if pill else (6, "chip")
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" rx="{rx}" class="{cls}" stroke-width="1"/>')
        parts.append(t(f"{x + w / 2:.1f}", y + h / 2 + 4.2, label, size, tcls, "600" if pill else None, anchor="middle"))
    return "".join(parts)


def milestone(v):
    for m in (10, 25, 50, 100, 250, 500, 1000, 2500):
        if v <= m:
            return m
    return v


def bar(x, y, w, h, frac, cls=None, color=None):
    frac = min(1, max(frac, 0.03))
    fill = f'class="{cls}"' if cls else f'fill="{color}"'
    return (
        f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" rx="{h / 2}" class="track"/>'
        f'<rect x="{x:.1f}" y="{y}" width="{w * frac:.1f}" height="{h}" rx="{h / 2}" {fill}/>'
    )


def donut(cx, cy, r, frac, color):
    c = 2 * math.pi * r
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke-width="6" class="trk"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round" '
        f'stroke-dasharray="{c * frac:.1f} {c:.1f}" transform="rotate(-90 {cx} {cy})"/>'
        + t(cx, cy + 3.8, f"{frac * 100:.0f}%", 10.5, "t1", "700", "middle")
    )


# ───────────────────────── API de GitHub ─────────────────────────

def get_token():
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Sin token: define GH_TOKEN o inicia sesión con `gh auth login`.")


TOKEN = None


def request(url, data=None):
    headers = {"Authorization": f"Bearer {TOKEN}", "User-Agent": "profile-cards", "Accept": "application/vnd.github+json"}
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), resp.headers
    except urllib.error.HTTPError as e:
        sys.exit(f"Error {e.code} en {url}: {e.read().decode()[:200]}")


def rest(path):
    return request("https://api.github.com" + path)


def graphql(query, variables):
    body, _ = request("https://api.github.com/graphql", {"query": query, "variables": variables})
    if "errors" in body:
        sys.exit(f"Error GraphQL: {body['errors']}")
    return body["data"]


def commit_count(repo):
    body, headers = rest(f"/repos/{repo}/commits?per_page=1")
    link = headers.get("Link", "")
    m = re.search(r'[?&]page=(\d+)>; rel="last"', link)
    return int(m.group(1)) if m else len(body)


# ───────────────────────── tarjetas ─────────────────────────

def lang_segments(langs, ignore):
    """Lista [(nombre, fracción)] ordenada, sin los lenguajes ignorados."""
    langs = {k: v for k, v in langs.items() if k not in ignore}
    total = sum(langs.values()) or 1
    return [(k, v / total) for k, v in sorted(langs.items(), key=lambda kv: -kv[1])]


def jpeg_size(raw):
    """(ancho, alto) de un JPEG leyendo su marcador SOF, sin dependencias."""
    i = 2
    while i < len(raw):
        marker = raw[i + 1]
        length = int.from_bytes(raw[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(raw[i + 7:i + 9], "big"), int.from_bytes(raw[i + 5:i + 7], "big")
        i += 2 + length
    raise ValueError("JPEG sin marcador SOF")


def build_header(cfg, handle):
    """Texto a la izquierda; ilustración a la derecha, recortada al panel y con borde difuminado."""
    img_w, fade, shift = 330, 130, 50  # ancho visible, ancho del degradado, recorte por la izquierda
    box_x = W - PX - img_w
    text_w = box_x + 40 - IX
    bio = wrap(cfg["bio"], 13, text_w - 20)
    pills, _ = layout_chips(cfg["pills"], IX, IX + text_w - 20, size=12.5, pad=12, gap=8)
    top = PX
    pills_y = top + 136 + (len(bio) - 1) * 19 + 14
    ph = pills_y + 28 + 22 - top
    h = top + ph + 16 + M
    defs, body = shell(W, h)
    defs += '<linearGradient id="ng" x1="0" x2="1"><stop offset="0" class="n1"/><stop offset="1" class="n2"/></linearGradient>'
    body += panel(ph)
    if cfg.get("portrait"):
        raw = (ROOT / cfg["portrait"]).read_bytes()
        data = base64.b64encode(raw).decode()
        pw, phh = jpeg_size(raw)
        scaled_w = ph * pw / phh  # la ilustración se ajusta al alto del panel
        shift = min(shift, scaled_w - img_w)
        defs += (
            f'<clipPath id="pc"><rect x="{PX}" y="{top}" width="{W - 2 * PX}" height="{ph}" rx="12"/></clipPath>'
            f'<linearGradient id="pf" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
            f'<stop offset="{fade / img_w:.2f}" stop-color="#fff" stop-opacity="1"/></linearGradient>'
            f'<mask id="pm"><rect x="{box_x}" y="{top}" width="{img_w}" height="{ph}" fill="url(#pf)"/></mask>'
        )
        body += (
            f'<g clip-path="url(#pc)"><image href="data:image/jpeg;base64,{data}" x="{box_x - shift:.0f}" '
            f'y="{top}" width="{scaled_w:.0f}" height="{ph}" preserveAspectRatio="none" mask="url(#pm)"/></g>'
        )
        body += f'<rect x="{PX}.5" y="{top}.5" width="{W - 2 * PX - 1}" height="{ph - 1}" rx="12" class="pline" stroke-width="1"/>'
    body += t(IX, top + 38, "@" + handle.lower(), 12.5, "t2", "700", extra='letter-spacing="2.4"', mono=True)
    body += t(IX, top + 84, cfg["name"], 38, "", "800", extra='fill="url(#ng)"')
    body += t(IX, top + 110, cfg["role"], 16, "ac", "500")
    for i, line in enumerate(bio):
        body += t(IX, top + 138 + i * 19, line, 13, "t2")
    body += draw_chips(pills, pills_y + 2, h=28, size=12.5, pill=True, tcls="ac")
    return svg(W, h, f"{cfg['name']}: {cfg['role']}", body, defs)


def project_layout(p, inner):
    desc = wrap(p["desc"], 13, inner)
    chips, rows = layout_chips(p["stack"], M + 22, M + 22 + inner)
    return desc, chips, rows


def card_height(max_lines, max_rows):
    chips_y = M + 106 + (max_lines - 1) * 19 + 18
    sep_y = chips_y + max_rows * 30 - 6 + 16
    return sep_y + 24 + 12 + 6 + M + 4


def build_project(p, info, layout):
    desc_lines, chips, max_lines, chip_rows, h = layout
    pad = M + 22
    defs, body = shell(CARD_W, h, rx=14)
    body += t(pad, M + 32, p["label"], 10.5, "ac", "700", extra='letter-spacing="1.2"')
    body += t(pad, M + 60, p["title"], 19, "t1", "700")
    body += t(pad, M + 79, p["repo"], 11.5, "t2", mono=True)
    if info["bar"]:
        top_lang, _, w = info["bar"][0]
        body += donut(CARD_W - pad - 22, M + 46, 20, w / (CARD_W - 2 * M), LANG_COLORS.get(top_lang, "#8b949e"))
    y = M + 108
    for line in desc_lines:
        body += t(pad, y, line, 13, "t1")
        y += 19
    chips_y = M + 106 + (max_lines - 1) * 19 + 18  # misma altura en todas las tarjetas
    body += draw_chips(chips, chips_y)
    sep_y = chips_y + chip_rows * 30 - 6 + 16
    body += f'<line x1="{pad}" y1="{sep_y}" x2="{CARD_W - pad}" y2="{sep_y}" class="sep" stroke-width="1"/>'
    body += f'<circle cx="{pad + 4}" cy="{sep_y + 20}" r="4" class="d-{p["kind"]}"/>'
    body += t(pad + 16, sep_y + 24, p["status"], 12, "t1")
    meta = f"{info['commits']} commits · {fmt_date(info['pushed_at'])}"
    body += t(CARD_W - pad, sep_y + 24, meta, 11.5, "t2", anchor="end")
    bar_y = h - M - 5
    body += (
        '<g clip-path="url(#fc)">'
        + "".join(
            f'<rect x="{x:.2f}" y="{bar_y}" width="{w + 0.5:.2f}" height="5" fill="{LANG_COLORS.get(n, "#8b949e")}"/>'
            for n, x, w in info["bar"]
        )
        + "</g>"
    )
    title = f"{p['title']} ({p['repo']}): {p['desc']} Tecnologías: {', '.join(p['stack'])}."
    return svg(CARD_W, h, title, body, defs)


def titles(top, title, subtitle):
    return t(IX, top + 38, title, 24, "t1", "800") + t(IX, top + 60, subtitle, 12.5, "t2", "600")


def build_profile_signal(tiles, today):
    top, ph = PX, 218
    h = top + ph + 16 + M
    defs, body = shell(W, h)
    body += panel(ph) + titles(top, "Señal de perfil", "Estadísticas en vivo de GitHub")
    gap = 14
    tw = (IW - 3 * gap) / 4
    for i, (label, value, color) in enumerate(tiles):
        x, y = IX + i * (tw + gap), top + 78
        body += f'<rect x="{x + 0.5:.1f}" y="{y + 0.5}" width="{tw:.1f}" height="104" rx="14" class="tile" stroke-width="1"/>'
        body += t(x + 18, y + 28, label.upper(), 11, "t2", "700", extra='letter-spacing="1"')
        body += t(x + 18, y + 70, str(value), 38, color, "800")
        body += bar(x + 18, y + 84, tw - 36, 8, value / milestone(value), cls=color)
    body += t(W - IX, top + ph - 10, f"Actualizado {today.day} {MESES[today.month - 1]} {today.year}", 10.5, "t2", anchor="end")
    return svg(W, h, "Señal de perfil: " + ", ".join(f"{v} {k}" for k, v, _ in tiles), body, defs)


def build_languages(segments):
    top_n = segments[:6]
    top, ph = PX, 82 + len(top_n) * 32 + 14
    h = top + ph + 16 + M
    defs, body = shell(W, h)
    body += panel(ph) + titles(top, "Stack de lenguajes", "Ponderado por código en repos públicos")
    body += t(W - IX, top + 38, "stack.scan", 13, "ac", "700", anchor="end", mono=True)
    bx = IX + 200
    for i, (name, frac) in enumerate(top_n):
        y = top + 92 + i * 32
        color = LANG_COLORS.get(name, "#8b949e")
        body += f'<circle cx="{IX + 6}" cy="{y - 4}" r="5" fill="{color}"/>'
        body += t(IX + 22, y, LANG_LABELS.get(name, name), 13.5, "t1", "700")
        body += t(IX + 168, y, f"{frac * 100:.0f}%", 13.5, "t1", "800", anchor="end", extra=f'fill="{color}"').replace('class="f t1"', 'class="f"')
        body += bar(bx, y - 10, W - IX - bx, 9, frac, color=color)
    return svg(W, h, "Stack de lenguajes: " + ", ".join(f"{LANG_LABELS.get(n, n)} {f * 100:.0f}%" for n, f in top_n), body, defs)


def build_toolkit(groups):
    label_w, x0, right = 112, IX, W - IX
    top = PX
    y = top + 84
    rows = []
    for g in groups:
        chips, n = layout_chips(g["items"], x0 + label_w, right)
        block_h = n * 30 - 6
        rows.append((g["label"], chips, y, block_h))
        y += block_h + 18
    ph = y - 18 + 24 - top
    h = top + ph + 16 + M
    defs, body = shell(W, h)
    body += panel(ph) + titles(top, "Herramientas", "Tecnologías que uso en mis proyectos")
    for i, (label, chips, ytop, block_h) in enumerate(rows):
        body += t(x0, ytop + 16, label, 12.5, "t2", "700")
        body += draw_chips(chips, ytop)
        if i < len(rows) - 1:
            sy = ytop + block_h + 9
            body += f'<line x1="{x0}" y1="{sy}" x2="{right}" y2="{sy}" class="sep" stroke-width="1" stroke-opacity="0.7"/>'
    return svg(W, h, "Herramientas: " + "; ".join(f"{g['label']}: {', '.join(g['items'])}" for g in groups), body, defs)


def level(n, mx):
    if n <= 0:
        return 0
    r = n / mx
    return 1 if r <= 0.25 else 2 if r <= 0.5 else 3 if r <= 0.75 else 4


def build_consistency(weeks, total):
    top, ph = PX, 258
    h = top + ph + 16 + M
    defs, body = shell(W, h)
    counts = [d["contributionCount"] for w in weeks for d in w["contributionDays"]]
    body += panel(ph) + titles(top, "Actividad de contribuciones", f"{total} contribuciones en el último año")
    mx = max(counts + [1])
    # leyenda arriba a la derecha
    lx = W - IX
    body += t(lx, top + 40, "Más", 11.5, "t2", anchor="end")
    for lv in range(4, -1, -1):
        lx -= 14 + (24 if lv == 4 else 0)
        body += f'<rect x="{lx}" y="{top + 31}" width="11" height="11" rx="2.5" class="h{lv}"/>'
    body += t(lx - 6, top + 40, "Menos", 11.5, "t2", anchor="end")
    cell, gap, y0 = 11, 3, top + 82
    x0 = IX + (IW - (len(weeks) * (cell + gap) - gap)) // 2
    for ci, w in enumerate(weeks):
        for d in w["contributionDays"]:
            row = (dt.date.fromisoformat(d["date"]).weekday() + 1) % 7  # domingo = 0
            lv = level(d["contributionCount"], mx)
            body += (
                f'<rect x="{x0 + ci * (cell + gap)}" y="{y0 + row * (cell + gap)}" width="{cell}" height="{cell}" '
                f'rx="2.5" class="h{lv}"><title>{d["date"]}: {d["contributionCount"]}</title></rect>'
            )
    active = sum(1 for w in weeks if sum(d["contributionCount"] for d in w["contributionDays"]) > 0)
    streak = best = 0
    for n in counts:
        streak = streak + 1 if n > 0 else 0
        best = max(best, streak)
    col = IW // 3
    sy = y0 + 7 * (cell + gap) + 36
    for i, (num, label, color) in enumerate(
        [(f"{active}/{len(weeks)}", "semanas activas", "c-blue"), (f"{best} días", "racha más larga", "c-cyan"), (str(max(counts)), "máximo en un día", "c-purple")]
    ):
        body += t(IX + 2 + i * col, sy, num, 24, color, "800") + t(IX + 2 + i * col, sy + 18, label, 12, "t2", "600")
    return svg(W, h, f"Actividad de contribuciones: {total} en el último año, {active} de {len(weeks)} semanas activas, racha más larga de {best} días", body, defs)


# ───────────────────────── vista previa ─────────────────────────

PREVIEW = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vista previa del perfil</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1f2328;--bd:#d1d9e0}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#f0f6fc;--bd:#3d444d}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:880px;margin:0 auto;padding:32px 16px 64px}
h2{font-size:1.5em;font-weight:600;padding-bottom:.3em;border-bottom:1px solid var(--bd);margin:28px 0 12px}
img{max-width:100%;vertical-align:baseline}
a{color:inherit}
</style></head><body><main>
{{CONTENT}}
</main></body></html>
"""


def build_preview(readme):
    def inline(m):  # el visor local no resuelve rutas relativas: se incrustan las imágenes
        data = base64.b64encode((ROOT / m.group(1)).read_bytes()).decode()
        return f'src="data:image/svg+xml;base64,{data}"'

    readme = re.sub(r'src="(assets/[^"]+\.svg)"', inline, readme)
    out = []
    for line in readme.splitlines():
        m = re.match(r"##\s+(.*)", line)
        out.append(f"<h2>{m.group(1)}</h2>" if m else line)
    (ROOT / "preview.html").write_text(PREVIEW.replace("{{CONTENT}}", "\n".join(out)), encoding="utf-8")


# ───────────────────────── principal ─────────────────────────

def main():
    global TOKEN
    TOKEN = get_token()
    cfg = json.loads((ROOT / "data" / "profile.json").read_text(encoding="utf-8"))
    login = cfg["login"]
    ignore = set(cfg["languages_ignore"])
    today = dt.datetime.now(dt.timezone.utc).date()
    ASSETS.mkdir(exist_ok=True)

    user = graphql(
        """query($login:String!){user(login:$login){
            contributionsCollection{totalPullRequestContributions
              contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}}}""",
        {"login": login},
    )["user"]["contributionsCollection"]

    owned, _ = rest(f"/users/{login}/repos?per_page=100&type=owner")
    repos = {r["full_name"] for r in owned if not r["fork"]}
    repos |= {p["repo"] for p in cfg["projects"]}

    totals = {}
    for repo in sorted(repos):
        langs, _ = rest(f"/repos/{repo}/languages")
        for k, v in langs.items():
            totals[k] = totals.get(k, 0) + v
    segments = lang_segments(totals, ignore)

    # Proyectos destacados: datos vivos + diseño uniforme
    infos, layouts = {}, {}
    inner = CARD_W - 2 * (M + 22)
    for p in cfg["projects"]:
        meta, _ = rest(f"/repos/{p['repo']}")
        langs, _ = rest(f"/repos/{p['repo']}/languages")
        x, bar_segs = M, []
        for name, frac in lang_segments(langs, ignore):
            w = (CARD_W - 2 * M) * frac
            bar_segs.append((name, x, w))
            x += w
        infos[p["repo"]] = {"commits": commit_count(p["repo"]), "pushed_at": meta["pushed_at"], "bar": bar_segs}
        layouts[p["repo"]] = project_layout(p, inner)
    max_lines = max(len(l[0]) for l in layouts.values())
    max_rows = max(l[2] for l in layouts.values())
    h = card_height(max_lines, max_rows)

    files = {"header.svg": build_header(cfg, login)}
    for i, p in enumerate(cfg["projects"], 1):
        desc, chips, _ = layouts[p["repo"]]
        files[f"project-{i}.svg"] = build_project(p, infos[p["repo"]], (desc, chips, max_lines, max_rows, h))
    files["languages.svg"] = build_languages(segments)
    files["toolkit.svg"] = build_toolkit(cfg["toolkit"])

    calendar = user["contributionCalendar"]
    tiles = [
        ("Repos públicos", len(repos), "c-blue"),
        ("Contribuciones · 12 m", calendar["totalContributions"], "c-cyan"),
        ("Pull requests · 12 m", user["totalPullRequestContributions"], "c-purple"),
        ("En producción", sum(1 for p in cfg["projects"] if p["kind"] == "prod"), "c-green"),
    ]
    files["profile-signal.svg"] = build_profile_signal(tiles, today)
    files["consistency-signal.svg"] = build_consistency(calendar["weeks"][-52:], calendar["totalContributions"])

    for name, content in files.items():
        (ASSETS / name).write_text(content, encoding="utf-8")
        print(f"  assets/{name}  ({len(content) // 1024 or 1} KB)")
    build_preview((ROOT / "README.md").read_text(encoding="utf-8"))
    print("  preview.html")


if __name__ == "__main__":
    main()
