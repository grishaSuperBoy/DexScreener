import math
import os
import struct
import zlib
from pathlib import Path

def create_png_rgba(width, height, get_pixel):
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0: None
        for x in range(width):
            r, g, b, a = get_pixel(x, y)
            raw.extend([
                max(0, min(255, int(r))),
                max(0, min(255, int(g))),
                max(0, min(255, int(b))),
                max(0, min(255, int(a))),
            ])

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def create_ico(images_png_with_size):
    """
    images_png_with_size is a list of tuples: (png_bytes, width, height)
    Creates a standards-compliant multi-resolution ICO file.
    """
    count = len(images_png_with_size)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16

    entries = []
    data_blobs = []

    for png_data, w, h in images_png_with_size:
        size = len(png_data)
        entry = struct.pack(
            "<BBBBHHII",
            w if w < 256 else 0,
            h if h < 256 else 0,
            0,  # color palette count (0 for 24/32bpp)
            0,  # reserved
            1,  # color planes
            32,  # bits per pixel
            size,
            offset,
        )
        entries.append(entry)
        data_blobs.append(png_data)
        offset += size

    return header + b"".join(entries) + b"".join(data_blobs)


def render_green_on_black(w, h):
    """
    Renders a glowing neon-green droplet & pulse wave icon on a deep black rounded background.
    """
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    corner_r = w * 0.28

    def pixel(x, y):
        # 1. Background: rounded squircle
        dx = abs(x - cx)
        dy = abs(y - cy)
        dist_corner = math.hypot(max(0, dx - (cx - corner_r)), max(0, dy - (cy - corner_r)))
        if dist_corner > corner_r + 0.5:
            return (0, 0, 0, 0)  # transparent outside rounded corners

        # Antialiasing on squircle border
        alpha_bg = 255
        if dist_corner > corner_r - 0.5:
            alpha_bg = int(255 * (corner_r + 0.5 - dist_corner))

        # Deep black background (#0B0E14) with subtle radial gradient
        norm_r = math.hypot((x - cx) / cx, (y - cy) / cy)
        bg_r = int(11 + 6 * (1 - norm_r))
        bg_g = int(14 + 10 * (1 - norm_r))
        bg_b = int(20 + 12 * (1 - norm_r))

        # 2. Draw Neon Green Droplet and Chart Pulse
        # Normalized coordinates [-1, 1]
        nx = (x - cx) / (w * 0.42)
        ny = (y - cy) / (h * 0.42)

        # Droplet center is slightly below middle (ny ~ 0.2)
        # Teardrop distance function:
        # circle at (0, 0.2) with radius R, tapering to sharp point at (0, -0.9)
        d_circle = math.hypot(nx, ny - 0.2) - 0.58
        d_top = abs(nx) - (0.9 + ny) * 0.55 if ny < 0.2 else 1.0

        in_droplet = (d_circle <= 0 and ny >= 0.2) or (d_top <= 0 and -0.9 <= ny <= 0.2)

        # Glow field
        glow_dist = math.hypot(nx, ny - 0.1)
        glow = math.exp(-glow_dist * 2.2)

        if in_droplet:
            # Bright neon green (#00FFA3 / #10B981) with inner gradient to white-green
            intensity = 1.0 - math.hypot(nx + 0.15, ny - 0.05) * 0.9
            intensity = max(0.0, min(1.0, intensity))
            # Color: bright mint/emerald green (#00F59B -> #22C55E)
            r = int(16 + intensity * 180)
            g = int(245 + intensity * 10)
            b = int(135 + intensity * 120)
            return (r, g, b, alpha_bg)
        elif glow > 0.08:
            # Neon green glow around the droplet on black background
            g_factor = min(1.0, glow * 1.5)
            r = int(bg_r * (1 - g_factor) + 16 * g_factor)
            g = int(bg_g * (1 - g_factor) + 210 * g_factor)
            b = int(bg_b * (1 - g_factor) + 120 * g_factor)
            return (r, g, b, alpha_bg)

        # Border rim
        if dist_corner > corner_r - 1.2:
            return (35, 48, 68, alpha_bg)

        return (bg_r, bg_g, bg_b, alpha_bg)

    return create_png_rgba(w, h, pixel)


def generate_svg():
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="neonGreen" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00FFA3"/>
      <stop offset="50%" stop-color="#10B981"/>
      <stop offset="100%" stop-color="#059669"/>
    </linearGradient>
    <radialGradient id="glowPulse" cx="50%" cy="55%" r="50%">
      <stop offset="0%" stop-color="#00FFA3" stop-opacity="0.4"/>
      <stop offset="70%" stop-color="#10B981" stop-opacity="0.1"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0"/>
    </radialGradient>
    <filter id="neonBlur" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>

  <!-- Dark Obsidian Squircle Background -->
  <rect x="2" y="2" width="60" height="60" rx="16" fill="#0B0E14" stroke="#1E293B" stroke-width="2"/>

  <!-- Radial Neon Glow -->
  <circle cx="32" cy="35" r="24" fill="url(#glowPulse)"/>

  <!-- Neon Green Liquidity Radar Pulse Waves -->
  <path d="M18 42 A16 16 0 0 1 46 42" fill="none" stroke="#00FFA3" stroke-width="2.5" stroke-linecap="round" opacity="0.35"/>
  <path d="M22 42 A11 11 0 0 1 42 42" fill="none" stroke="#00FFA3" stroke-width="2" stroke-linecap="round" opacity="0.6"/>

  <!-- Glowing Neon Teardrop Symbol -->
  <path d="M32 14 C32 14 45 28 45 38 A13 13 0 1 1 19 38 C19 28 32 14 32 14 Z" 
        fill="url(#neonGreen)" 
        filter="url(#neonBlur)"/>

  <!-- Highlight Sparkle inside droplet -->
  <ellipse cx="28" cy="34" rx="3.5" ry="5.5" transform="rotate(-25 28 34)" fill="#FFFFFF" opacity="0.65"/>
  <circle cx="32" cy="41" r="2.5" fill="#FFFFFF" opacity="0.8"/>
</svg>"""


def main():
    web_dir = Path("d:/GitHub/DexScreener/web")
    brain_dir = Path("C:/Users/Vitali/.gemini/antigravity/brain/e4b8c924-d6cf-4ea3-a080-c39cdc616462")
    web_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate SVG
    svg_content = generate_svg()
    (web_dir / "favicon.svg").write_text(svg_content, encoding="utf-8")
    (brain_dir / "favicon.svg").write_text(svg_content, encoding="utf-8")
    print("[✓] favicon.svg создан")

    # 2. Render multi-resolution PNGs
    print("[*] Рендеринг PNG 16x16, 32x32, 48x48, 64x64...")
    png16 = render_green_on_black(16, 16)
    png32 = render_green_on_black(32, 32)
    png48 = render_green_on_black(48, 48)
    png64 = render_green_on_black(64, 64)

    # Save favicon.png
    (web_dir / "favicon.png").write_bytes(png64)
    (brain_dir / "favicon.png").write_bytes(png64)
    print("[✓] favicon.png сохранен")

    # 3. Package into multi-resolution ICO
    ico_bytes = create_ico([
        (png16, 16, 16),
        (png32, 32, 32),
        (png48, 48, 48),
        (png64, 64, 64),
    ])
    (web_dir / "favicon.ico").write_bytes(ico_bytes)
    (brain_dir / "favicon.ico").write_bytes(ico_bytes)
    print(f"[✓] favicon.ico успешно сгенерирован ({len(ico_bytes)} байт)!")


if __name__ == "__main__":
    main()
