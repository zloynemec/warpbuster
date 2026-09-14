# WarpBuster logo

`warpbuster-logo-original.png` is the user's original PNG, copied without modification.
It is identical to the existing `docs/assets/warpbuster-logo.png`.

SHA-256: `234ae3f6b6e0841a3e9f95d69ab88fef584933d76fae20e3d2136f2099829dc1`.

The website uses `dist/assets/warpbuster-logo.svg` and `dist/assets/favicon.svg`.
Both embed the original PNG and apply an SVG color filter: blue becomes forest green
`#203e31`; orange/red becomes terracotta `#b54b23`. Source alpha and geometry are
preserved. The favicon adds a pale `#f5f5ee` background for contrast on browser tabs.
These are raster-backed SVG files, not vector tracings.

Regenerate the wrappers from the repository root:

```bash
python3 web/scripts/build_brand_assets.py
```

An ImageGen recoloring was evaluated but rejected because it changed the edges and
baked in a checkerboard background. No generated logo is used on the website.
