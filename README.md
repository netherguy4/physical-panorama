# physical-panorama

Span one wide image across several monitors so it lines up at **true physical scale**, even when the monitors differ in size, resolution, scaling and height on the desk.

Normal "span" modes stretch an image over the virtual desktop in logical pixels. A 27" 4K screen at 150% next to a 24" 1080p screen then shows the picture at different sizes, and lines break at every seam. physical-panorama reads each panel's real size in millimetres from its EDID, arranges the panels in physical space, and crops each monitor's part from one shared image coordinate system. Lines continue straight across the bezels.

Currently it supports the [COSMIC](https://system76.com/cosmic) desktop only. See [Other desktops](#other-desktops).

## Features

- Physical layout from EDID detailed timings (COSMIC's rounded sizes are the fallback), with rotation and fractional scaling.
- Pre-rendered native-resolution crops for each monitor. Displaying them is left to COSMIC's own wallpaper service; no resident process.
- Static mode (one fixed panorama) or slideshow mode (random panorama every 15 minutes).
- Re-fits automatically after login and whenever monitors are connected or rearranged.
- Uses images from any folder; subfolders become groups.
- Optional per-monitor calibration in millimetres for bezels and desk gaps.
- `restore` brings back the previous COSMIC wallpaper configuration.

## Requirements

- COSMIC desktop (`cosmic-randr` on `PATH`)
- Python 3.9+ and Pillow 9.1+
- systemd user session (for static/slideshow modes)

## Install

```sh
pipx install git+https://github.com/netherguy4/physical-panorama
```

`uv tool install git+https://github.com/netherguy4/physical-panorama` works as well. To run from a checkout instead, use a Python that has Pillow: `python3 physical_panorama.py ...` (Fedora: `python3-pillow`).

## Images

Put panoramas into `~/Pictures/Panoramas` (the localized XDG Pictures folder is detected), or set `"images"` in the config. JPEG, PNG, WebP, TIFF and AVIF are read; files in subfolders form a group named after the first-level subfolder, so `Panoramas/space/*.jpg` is group `space`. Files in the root are group `default`.

An image is used only if it fits the current layout well:

- it must keep at least 65% of its area after cropping to the combined physical aspect ratio (`min_retained`);
- it must not be upscaled more than 1.25× for the densest monitor (`max_resample`).

A typical three-monitor setup needs images around 7000+ pixels wide. Run `physical-panorama list` to see which images fit and why.

## Usage

```sh
physical-panorama list                  # which images fit this layout
physical-panorama static                # show a panorama permanently (keeps the current one if any)
physical-panorama static --id space/m42 # show a specific image (ID = path without extension)
physical-panorama slideshow             # random panorama every ~15 minutes
physical-panorama next                  # switch now (cache must be prepared)
physical-panorama prepare               # pre-render crops for all fitting images
physical-panorama reload --fast         # re-fit the current image, then prepare the rest in parallel
physical-panorama layout                # print the detected physical layout
physical-panorama status                # current panorama
physical-panorama restore               # disable automation, restore the previous wallpaper
```

`--group NAME` limits a run to one group (`all` means every image).

`static` and `slideshow` write their systemd user units to `~/.config/systemd/user/` and enable them:

| Unit | Purpose |
| --- | --- |
| `physical-panorama.timer` / `.service` | slideshow: prepare cache, then switch |
| `physical-panorama-reload.service` | static mode: re-fit after login |
| `physical-panorama-layout.path` | both modes: re-fit when `~/.local/state/cosmic-comp/outputs.ron` changes (monitor hotplug or rearrangement) |

The units start the same Python interpreter and script that ran the command, so re-run `static` or `slideshow` after moving the install.

## Configuration

`~/.config/physical-panorama/config.json` (all keys optional):

```json
{
  "images": "~/Pictures/Panoramas",
  "group": "all",
  "min_retained": 0.65,
  "max_resample": 1.25,
  "monitors": {
    "HDMI-A-1": {"offset_mm": [0, 5]},
    "DP-1": {"size_mm": [597, 336]},
    "eDP-1": {"position_mm": [1160, 150]}
  }
}
```

Per-connector calibration (connector names come from `physical-panorama layout`):

- `size_mm`: override the reported active-area size.
- `position_mm`: measured upper-left corner of the active area in one common desk coordinate system. Measure every panel for a fully measured setup; these values ignore the virtual arrangement.
- `offset_mm`: nudge the inferred position.

Without calibration, panels that touch in COSMIC's display settings are assumed to touch physically. Shared top or bottom edges stay aligned in millimetres; other overlaps are centred. Bezels and gaps cannot be detected, so measure them if seams look offset.

### Optional manifest

If the image folder contains `manifest.json`, it is used instead of scanning. Use it for curated collections:

```json
[
  {"id": "m42", "title": "Orion", "file": "space/m42.jpg", "width": 12000, "height": 3000,
   "group": "space", "focus": [0.5, 0.4], "min_retained": 0.5, "sha256": "..."}
]
```

`focus` is the crop centre as a fraction of width and height. `min_retained` overrides the global limit for one image.

## How it works

1. `cosmic-randr list --kdl` gives each enabled output's mode, scale, transform and logical position. EDID detailed timings from `/sys/class/drm` refine physical sizes.
2. Outputs are placed in millimetres, starting from the primary one, attaching each neighbour along the shared edge.
3. The image is scaled to cover the physical canvas. Every monitor's crop box is computed in floating-point source coordinates and resampled with Lanczos straight to the monitor's native pixels, so no rounding drift accumulates at seams.
4. Crops are cached in `~/.local/state/physical-panorama/generations/`, keyed by layout, image and framing. A complete generation is published through one atomic symlink, then a single COSMIC `backgrounds` config write reloads all outputs. COSMIC has no frame-atomic multi-output transaction, so monitors with different refresh rates may switch a frame apart.
5. Unchanged layouts reuse the cache. A new layout re-renders the current image first, then the rest at low CPU and I/O priority. Only the current layout's cache is kept.

## Other desktops

Contributions are welcome. The geometry and cropping core is desktop-agnostic. A new backend needs two pieces:

- **layout reader**: list enabled outputs with connector name, native pixels, logical position/size, scale, transform and physical mm (see `parse_layout`);
- **wallpaper writer**: show one image per output, atomically if possible (see `apply`), plus a hotplug trigger for the `-layout.path` unit.

Possible routes: GNOME (`org.gnome.Mutter.DisplayConfig` + spanned background), KDE Plasma (scripting API), wlroots compositors (`swaybg`/`swww`), X11 (`feh --xinerama`).

## Development

```sh
python3 tests/test_panorama.py
```

The test uses a recorded `cosmic-randr` layout and temporary state; it never touches the desktop.

## License

MIT. Images are not part of this project; respect the licences of the images you use.
