# COSMIC panel applet

Native COSMIC 1.8 frontend for the installed `~/.local/bin/physical-panorama` CLI. Left-click the wallpaper icon in the panel to open the popup: current image title, group and number of images suitable for the current monitor layout, **Назад** / **Пауза**/**Запустить** / **Далее**, and dropdowns for the group and the slideshow interval (5, 15, 30, 60 minutes plus the configured value).

Every command runs `physical-panorama <action>` and then `physical-panorama status --json`, one subprocess at a time, without a shell. Deadlines: 10 s for status, 120 s for actions (an action may render a missing cache). Status is read once at startup and every 5 s only while the popup is open; user commands are queued in order, including independent group and interval changes. Action errors stay beside the last known state across background reads; **Повторить** retries the failed action.

Build from this directory with project-scoped tools:

```sh
unset APPIMAGE APPDIR LD_LIBRARY_PATH
cargo test --locked --release --jobs 2
cargo build --locked --release --jobs 2
install -Dm755 target/release/physical-panorama-cosmic ~/.local/bin/physical-panorama-cosmic
install -Dm644 io.github.netherguy4.PhysicalPanorama.desktop ~/.local/share/applications/io.github.netherguy4.PhysicalPanorama.desktop
```

Then add **Physical Panorama** (Физическая панорама) in COSMIC Settings → Desktop → Panel → Applets. No service or root access is needed.

Headless review screenshots (tiny-skia, no display or window): `cargo test --locked --release -- --ignored` writes PNGs to `/tmp/physical-panorama-review`. The pinned libcosmic's own `iced_test` crate does not compile against its COSMIC fork, so the test drives the iced runtime directly.

`Cargo.lock` pins libcosmic and cosmic-config together at COSMIC 1.8 commit `d4d71fd53e5ed6bd3a430089114dffa2da3cd498`.
