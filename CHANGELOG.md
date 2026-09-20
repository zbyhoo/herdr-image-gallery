# Changelog

## Unreleased

### Added
- `show -` publishes an image from bytes on stdin, base64 (`--stdin-base64`) or raw, so an agent holding an image with no local file (for example base64 in an MCP tool result) can display it without writing one.
- `show - --stdin-json` publishes many images in one call from a JSON array or JSON Lines, each entry with its own title, caption and format; an MCP `image` content block can be piped through unchanged.
- `--title` and `--caption` can be repeated once per image to pair them by position.
- Image format is detected from magic bytes (PNG, JPEG, WebP, GIF, TIFF, BMP, HEIC); `--format` only declares it and content that is not a supported image is refused.
- Bytes are archived under their sha256, so republishing the same image updates one history entry instead of adding another, and `status`/`list` report it exactly like a published file.
- The `show` output lists every published image with its archive path, title, sha256, format and source.

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
