# Changelog

## 1.0.0 - 2026-09-20

First tagged release.

### Added
- Image gallery pane for Herdr that browses project and generated images in the terminal, with native Herdr image rendering.
- Workspace names shown in the gallery and improved public setup documentation.
- Automatic registration of gallery skills for Claude Code and Codex.
- Image clipboard copying with the `c` shortcut.
- Linux support: image decoding and clipboard integration.
- Whole-directory browsing with `d`, `r`, and the `browse` CLI.
- Newest-first sort toggle for directory browsing (name or newest).
- Gallery screenshot in the README.
- MIT license.
- CI running the unit tests on Linux and macOS.

### Changed
- The previous preview stays visible while the next image loads.
- Directories are scanned in the background so the gallery no longer freezes on large folders.

### Fixed
- macOS test failures: undecodable images from `sips` are handled and the temp dir is resolved.
