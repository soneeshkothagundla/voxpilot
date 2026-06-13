"""Pillow renderer for the VoxPilot "Aurora Glass Capsule" status overlay.

Renders each animation frame as a straight-alpha RGBA image: a light translucent
frosted-glass capsule with a soft drop shadow and a state-tinted halo, an
indigo->violet->cyan waveform while listening, and a traveling-dot wave while
working.

The renderer is pure Pillow (no Win32), so it can be unit-tested headlessly by
saving frames to PNG. Everything is drawn at ``scale * ss`` ("internal") size and
downscaled by ``ss`` at the end, which antialiases Pillow's otherwise hard edges.
All geometry constants below are in base (1.0x) pixels.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# -- base geometry (1.0x) --------------------------------------------------- #
CANVAS_W, CANVAS_H = 336, 112
BODY = (28, 28, 308, 84)  # x0, y0, x1, y1  -> 280 x 56 pill
RADIUS = 28
CENTER_Y = 56

# -- palette (R, G, B) ------------------------------------------------------ #
# Light translucent ("frosted white glass") theme.
_BASE = (249, 250, 253)
_TOP = (255, 255, 255)
_BOTTOM = (234, 238, 246)
_BODY_ALPHA = 208  # < 255 => the desktop shows through faintly (translucent)
_INDIGO = (79, 70, 229)
_VIOLET = (139, 92, 246)
_CYAN = (6, 182, 212)
_SWEEP = (99, 102, 241)
_HALO_LISTEN = (139, 92, 246)
_HALO_WORK = (124, 58, 237)
_LABEL = (32, 36, 46)
_REC = (255, 77, 79)
_RING = (196, 181, 253)

# -- viz geometry ----------------------------------------------------------- #
_N = 13
_BAR_W = 4
_BAR_GAP = 6
_PITCH = _BAR_W + _BAR_GAP  # 10
_FIELD = _N * _BAR_W + (_N - 1) * _BAR_GAP  # 124
_VIZ_CENTER_X = 223
_H_MIN = 4
_H_MAX = 34
_GLYPH_X = 64
_LABEL_X = 86


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(round(_lerp(c1[i], c2[i], t)) for i in range(3))


def _aurora_color(t: float) -> tuple:
    """Sample the indigo->violet->cyan gradient at ``t`` in [0, 1]."""
    t = max(0.0, min(1.0, t))
    if t <= 0.5:
        return _mix(_INDIGO, _VIOLET, t / 0.5)
    return _mix(_VIOLET, _CYAN, (t - 0.5) / 0.5)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("seguisb.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class AuroraRenderer:
    """Renders Aurora Glass Capsule frames at a given DPI scale."""

    def __init__(self, scale: float = 1.0, ss: int = 2) -> None:
        """Set up cached layers.

        Args:
            scale: DPI scale (e.g. 1.5 for 150%); multiplies all base geometry.
            ss: Supersampling factor used for antialiasing (downscaled at the end).
        """
        self.scale = scale
        self.ss = ss
        self.f = scale * ss
        self.tw = max(1, round(CANVAS_W * scale))
        self.th = max(1, round(CANVAS_H * scale))
        self.iw = max(1, round(CANVAS_W * self.f))
        self.ih = max(1, round(CANVAS_H * self.f))
        self._body_box = tuple(round(v * self.f) for v in BODY)
        self._font = _load_font(round(14 * self.f))
        self._body_mask = self._make_body_mask()
        self._chrome = self._build_chrome()
        self._halo_sil = self._build_halo_silhouette()
        self._labels = {
            "listening": self._render_label("Listening"),
            "working": self._render_label("Working"),
        }
        # Precompute everything blur-based so no Gaussian blur runs per frame.
        self._halos = {
            "listening": self._tinted_halo(_HALO_LISTEN),
            "working": self._tinted_halo(_HALO_WORK),
        }
        self._sweep_sprite, self._sweep_pad = self._build_sweep_sprite()
        self._listen_glyph = self._build_listen_glyph()

    # -- scaling helper ----------------------------------------------------- #
    def _s(self, v: float) -> float:
        return v * self.f

    # -- cached layers ------------------------------------------------------ #
    def _make_body_mask(self) -> Image.Image:
        x0, y0, x1, y1 = self._body_box
        mask = Image.new("L", (self.iw, self.ih), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [x0, y0, x1, y1], radius=round(RADIUS * self.f), fill=255
        )
        return mask

    def _build_chrome(self) -> Image.Image:
        """Glass capsule + black drop shadows (everything except the halo/interior)."""
        x0, y0, x1, y1 = self._body_box
        img = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))

        # Soft drop shadows (ambient + contact) for an airy, elevated light look.
        for dx, dy, blur, alpha in ((0, 8, 22, 42), (0, 2, 6, 60)):
            sm = Image.new("L", (self.iw, self.ih), 0)
            sm.paste(self._body_mask, (round(self._s(dx)), round(self._s(dy))))
            sm = sm.filter(ImageFilter.GaussianBlur(self._s(blur)))
            sm = sm.point(lambda a, al=alpha: a * al // 255)
            black = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 255))
            img = Image.alpha_composite(img, Image.composite(black, img, sm))

        # Vertical glass gradient, clipped to the body.
        bw, bh = x1 - x0, y1 - y0
        grad = Image.new("RGBA", (1, bh))
        gpx = grad.load()
        for y in range(bh):
            t = y / max(1, bh - 1)
            if t <= 0.55:
                col = _mix(_TOP, _BASE, t / 0.55)
                alpha = round(_lerp(_BODY_ALPHA + 8, _BODY_ALPHA, t / 0.55))
            else:
                col = _mix(_BASE, _BOTTOM, (t - 0.55) / 0.45)
                alpha = round(_lerp(_BODY_ALPHA, _BODY_ALPHA + 10, (t - 0.55) / 0.45))
            gpx[0, y] = (col[0], col[1], col[2], alpha)
        grad = grad.resize((bw, bh))
        body = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))
        body.paste(grad, (x0, y0), self._body_mask.crop((x0, y0, x1, y1)))
        img = Image.alpha_composite(img, body)

        # Strokes: outer dark definition + inner glass hairline + top sheen.
        draw = ImageDraw.Draw(img)
        r = round(RADIUS * self.f)
        draw.rounded_rectangle(
            [x0 - self._s(1), y0 - self._s(1), x1 + self._s(1), y1 + self._s(1)],
            radius=r,
            outline=(15, 23, 42, 38),
            width=max(1, round(self._s(1))),
        )
        draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=r,
            outline=(255, 255, 255, 190),
            width=max(1, round(self._s(1))),
        )
        draw.line(
            [x0 + self._s(44), y0 + self._s(2), x1 - self._s(44), y0 + self._s(2)],
            fill=(255, 255, 255, 150),
            width=max(1, round(self._s(1))),
        )
        return img

    def _build_halo_silhouette(self) -> Image.Image:
        """A blurred white body silhouette used to tint a per-frame colored halo."""
        x0, y0, _x1, _y1 = self._body_box
        sm = Image.new("L", (self.iw, self.ih), 0)
        sm.paste(self._body_mask, (0, round(self._s(4))))
        return sm.filter(ImageFilter.GaussianBlur(self._s(26)))

    def _render_label(self, text: str) -> Image.Image:
        layer = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        x = round(self._s(_LABEL_X))
        y = round(self._s(CENTER_Y))
        draw.text((x, y + 1), text, font=self._font, fill=(255, 255, 255, 150), anchor="lm")
        draw.text((x, y), text, font=self._font, fill=(*_LABEL, 240), anchor="lm")
        return layer

    # -- per-frame composition --------------------------------------------- #
    def frame(self, state: str, level: float, t: float) -> Image.Image:
        """Render one frame.

        Args:
            state: "listening" or "working".
            level: smoothed mic level in [0, 1] (used by the listening waveform).
            t: monotonic time in seconds (drives animation).

        Returns:
            A straight-alpha RGBA image at the target (scale) size.
        """
        working = state == "working"
        key = "working" if working else "listening"
        halo_alpha = (26 if working else 16 + round(level * 44)) / 255.0

        out = self._scaled_halo(key, halo_alpha)
        out = Image.alpha_composite(out, self._chrome)

        interior = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))
        if working:
            self._draw_working(interior, t)
        else:
            self._draw_listening(interior, level, t)
        # Clip interior to the body so nothing spills past the glass.
        interior.putalpha(
            Image.composite(
                interior.getchannel("A"), Image.new("L", (self.iw, self.ih), 0), self._body_mask
            )
        )
        out = Image.alpha_composite(out, interior)
        out = Image.alpha_composite(out, self._labels[key])
        if working:
            self._draw_glyph(out, t)
        else:
            out = Image.alpha_composite(out, self._listen_glyph)

        if self.ss != 1:
            out = out.resize((self.tw, self.th), Image.BOX)
        return out

    def _tinted_halo(self, accent: tuple) -> Image.Image:
        """Accent-colored halo whose alpha is the blurred body silhouette."""
        halo = Image.new("RGBA", (self.iw, self.ih), (*accent, 0))
        halo.putalpha(self._halo_sil)
        return halo

    def _scaled_halo(self, state: str, alpha: float) -> Image.Image:
        halo = self._halos[state].copy()
        halo.putalpha(halo.getchannel("A").point(lambda v, k=alpha: round(v * k)))
        return halo

    def _build_sweep_sprite(self) -> tuple:
        """A pre-blurred glow disc that slides across the working visualization."""
        pad = round(self._s(30))
        spr = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
        r = self._s(18)
        ImageDraw.Draw(spr).ellipse([pad - r, pad - r, pad + r, pad + r], fill=(*_SWEEP, 70))
        return spr.filter(ImageFilter.GaussianBlur(self._s(10))), pad

    def _build_listen_glyph(self) -> Image.Image:
        """The static red record dot (glow + ring + dot) for the listening state."""
        layer = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))
        cx, cy = self._s(_GLYPH_X), self._s(CENTER_Y)
        glow = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            [cx - self._s(9), cy - self._s(9), cx + self._s(9), cy + self._s(9)], fill=(*_REC, 110)
        )
        layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(self._s(6))))
        d = ImageDraw.Draw(layer)
        d.ellipse(
            [cx - self._s(8), cy - self._s(8), cx + self._s(8), cy + self._s(8)],
            outline=(*_REC, 90),
            width=max(1, round(self._s(1.5))),
        )
        r = self._s(5)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*_REC, 255))
        return layer

    def _draw_listening(self, layer: Image.Image, level: float, t: float) -> None:
        bars = Image.new("RGBA", (self.iw, self.ih), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bars)
        x_left = _VIZ_CENTER_X - _FIELD / 2
        for i in range(_N):
            env = 0.55 + 0.45 * math.cos((i - 6) / 6 * math.pi / 2)
            if level < 0.04:
                h = _H_MIN + 2.5 * (0.5 + 0.5 * math.sin(t * 2.0 - i * 0.5))
            else:
                jitter = 1 + 0.10 * math.sin(t * 3.1 + i * 0.9) + 0.05 * math.sin(t * 5.7 + i * 0.4)
                mag = max(0.0, min(1.0, level * env * jitter))
                h = _H_MIN + (_H_MAX - _H_MIN) * mag
            cx = x_left + i * _PITCH + _BAR_W / 2
            col = _aurora_color((cx - x_left) / _FIELD)
            x0 = self._s(cx - _BAR_W / 2)
            x1 = self._s(cx + _BAR_W / 2)
            y0 = self._s(CENTER_Y - h / 2)
            y1 = self._s(CENTER_Y + h / 2)
            draw.rounded_rectangle([x0, y0, x1, y1], radius=self._s(2), fill=(*col, 255))
        layer.alpha_composite(bars)

    def _draw_working(self, layer: Image.Image, t: float) -> None:
        phi = 2 * math.pi * 0.5 * t
        x_left = _VIZ_CENTER_X - _FIELD / 2
        # Pre-blurred sweeping glow disc (no per-frame blur).
        sweep_pos = math.sin(phi) * 0.5 + 0.5
        gx = x_left + sweep_pos * _FIELD
        layer.alpha_composite(
            self._sweep_sprite,
            (round(self._s(gx)) - self._sweep_pad, round(self._s(CENTER_Y)) - self._sweep_pad),
        )
        # Traveling dots (crisp; the BOX downscale antialiases them).
        dd = ImageDraw.Draw(layer)
        for i in range(_N):
            s = 0.5 + 0.5 * math.sin(phi - i * 0.55)
            cx = x_left + i * _PITCH + _BAR_W / 2
            cy = CENTER_Y + 7 * math.sin(phi - i * 0.55)
            rad = (4 + 1.6 * s) / 2
            alpha = 120 + round(120 * s)
            col = _aurora_color((cx - x_left) / _FIELD)
            dd.ellipse(
                [self._s(cx - rad), self._s(cy - rad), self._s(cx + rad), self._s(cy + rad)],
                fill=(*col, alpha),
            )

    def _draw_glyph(self, out: Image.Image, t: float) -> None:
        """Draw the rotating working-state spinner (the listening dot is cached)."""
        cx, cy = self._s(_GLYPH_X), self._s(CENTER_Y)
        phi = 2 * math.pi * 0.5 * t
        r = self._s(7)
        start = (math.degrees(phi) * 1.2) % 360
        ImageDraw.Draw(out).arc(
            [cx - r, cy - r, cx + r, cy + r],
            start=start,
            end=start + 270,
            fill=(*_SWEEP, 235),
            width=max(1, round(self._s(2))),
        )
