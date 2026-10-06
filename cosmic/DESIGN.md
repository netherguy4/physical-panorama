---
name: Physical Panorama COSMIC Applet
description: Native panel controls for physically aligned panoramic wallpapers.
typography:
  title:
    fontSize: "20px"
    fontWeight: 700
    lineHeight: "30px"
  caption:
    fontSize: "12px"
    lineHeight: "17px"
  button-label:
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "20px"
spacing:
  status-gap: "4px"
  action-gap: "8px"
  section-gap: "12px"
  content-inset: "16px"
---
# Design System: Physical Panorama COSMIC Applet

## Overview
**Creative North Star: "Native COSMIC panel control"**
A native COSMIC panel control for physical-panorama. Compact status and controls follow the installed desktop theme and interface font; this applet adds no independent brand palette or display face.
**Key Characteristics:**
- Native theme and symbolic icons.
- Status first, transport second, configuration below.
- Explicit busy, empty, loading and retry states.

## Colors
Runtime COSMIC background/on-background, divider and standard-button roles supply both light and dark palettes. Errors use readable text plus Retry; there is no custom error-color override.
**The Native Authority Rule.** Theme roles stay live; screenshot colors are evidence of a fixture, not fixed app tokens.

## Typography
The configured COSMIC interface font is inherited. `title4` supplies the bold title; captions supply collection/count, slideshow mode and busy guidance; standard buttons supply regular labels. Ordinary messages and settings labels use native text defaults. The panorama title ends with an ellipsis after two lines.

## Layout
The popup content is 360 logical px wide and shrink-height, with a 16 px inset. A 12 px column rhythm separates sections; the status stack uses 4 px and the transport row 8 px. Title, collection/count and mode precede Previous/Pause-or-Start/Next; a divider precedes group and interval settings. No mobile layout or breakpoint is implemented.

## Elevation & Depth
Native popup background and its 1 px theme divider border establish the surface. libcosmic's popup container uses the default shadow; the app adds no custom shadow or animation. Headless fixtures render content on a background, without the live popup shell.

## Shapes
Native standard buttons use COSMIC `radius_xl`; the popup shell uses `radius_m`. The app does not override their runtime values. Icons come from the symbolic desktop theme, including `preferences-desktop-wallpaper-symbolic` for the panel entry.

## Components
Standard transport buttons pair labels with symbolic icons and disable during actions or queued work. Previous follows history availability; Next and Start require eligible images; Pause remains available for an active slideshow with an empty collection.
Native popup dropdowns retain group counts and the configured interval alongside 5/15/30/60-minute presets. Dropdowns remain usable while work runs; independent choices execute in FIFO order.
Loading shows title and status text. Empty collections explain the monitor-layout constraint while leaving collection selection available. Busy text explains preparation can take two minutes. Failures retain prior status and expose Retry for the failed action, or status read when no action failed; automatic reads preserve action failures. Status refresh runs every five seconds only while the popup is open.

## Do's and Don'ts
- Do retain native hover, focus and disabled states.
- Do keep group and interval choices independent when queued.
- Do preserve action errors across automatic status refreshes until retry or another action.
- Don't freeze theme colors, fonts or corner radii into app-specific replacements.
- Don't treat headless fixtures as proof of live panel or dropdown-surface integration.
Verification: source and headless 2x light/dark fixtures cover normal, busy long title, error, empty and loading content. Installed upper-panel process and icon were verified live. Popup opening, keyboard/focus interactions and separate Wayland dropdown surfaces remain unverified; mixed-scale desktop input cannot reliably target layer-shell surfaces.
