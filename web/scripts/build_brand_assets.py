"""Wrap the unchanged source PNG in self-contained SVG with the website palette.

This changes rendering, not source pixels: topology, edges and source alpha stay intact.
Run with Python's standard library; no image-generation or graphics dependency is needed.
"""

import base64
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
SOURCE = WEB / "assets" / "warpbuster-logo-original.png"
OUTPUT = WEB / "dist" / "assets"
FOREST = (32, 62, 49)  # #203e31, site --ink
TERRACOTTA = (181, 75, 35)  # #b54b23, site --orange


def svg_document(*, favicon: bool, payload: str) -> str:
    # The visible source mark occupies x=89..1159, y=341..916.
    # Viewports remove empty margins without resizing or redrawing the mark.
    view_box = "65 68 1120 1120" if favicon else "65 315 1120 625"
    background = (
        '<rect x="65" y="68" width="1120" height="1120" rx="160" fill="#f5f5ee"/>'
        if favicon
        else ""
    )
    channels = "\n".join(
        f'      <feFunc{channel} type="discrete" '
        f'tableValues="{green / 255:.9f} {orange / 255:.9f}"/>'
        for channel, green, orange in zip("RGB", FOREST, TERRACOTTA, strict=True)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">
  <title>WarpBuster</title>
  <defs>
    <filter id="brand-colors" color-interpolation-filters="sRGB"
            x="0" y="0" width="100%" height="100%">
      <!-- Red-minus-blue selects the two source colors; alpha is unchanged. -->
      <feColorMatrix type="matrix" values="1 0 -1 0 0
                                           1 0 -1 0 0
                                           1 0 -1 0 0
                                           0 0  0 1 0"/>
      <feComponentTransfer>
{channels}
      </feComponentTransfer>
    </filter>
  </defs>
{background}
  <image width="1254" height="1254" filter="url(#brand-colors)"
         href="data:image/png;base64,{payload}"/>
</svg>
'''


def main() -> None:
    payload = base64.b64encode(SOURCE.read_bytes()).decode("ascii")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, favicon in (("warpbuster-logo.svg", False), ("favicon.svg", True)):
        (OUTPUT / name).write_text(svg_document(favicon=favicon, payload=payload), encoding="utf-8")


if __name__ == "__main__":
    main()
