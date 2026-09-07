# Herdr Image Gallery

View AI-generated images, screenshots, and local artwork inside [Herdr](https://herdr.dev), without leaving your terminal. Browse a thumbnail grid, open a larger preview, or let Codex send images to the gallery as it creates them.

The plugin includes an optional Codex skill and works independently through its CLI and local image links.

## Features

- **Thumbnail browsing:** select with arrow keys or click to open a preview.
- **Smooth preview switching:** the current image stays visible while the next one loads, with a centered loading indicator for slower requests.
- **Codex integration:** a bundled skill instructs Codex to send generated images and accessible local attachments to the gallery.
- **Persistent history:** archived copies remain available even after temporary source files disappear.
- **Flexible layout:** the gallery initially opens on the right and preserves your chosen pane position and size.
- **Local storage:** images are not uploaded by the plugin, and source files are not modified.

## Requirements

- macOS with Python 3.9 or later and the built-in `sips` image decoder.
- Herdr 0.8.2 or later, with experimental Kitty graphics enabled.
- A Kitty graphics-compatible terminal, such as Ghostty.

No pip or npm dependencies are required. Linux and Windows are not currently supported.

## Quick start

### 1. Enable graphics in Herdr

Add the following to `~/.config/herdr/config.toml`. If an `[experimental]` section already exists, add the setting to that section.

```toml
[experimental]
kitty_graphics = true
```

Reload the server configuration:

```sh
herdr server reload-config
```

Detach the Herdr client with **Ctrl+B, then Q**, and reconnect with `herdr`. These are the default shortcuts; use your configured detach binding if different. Herdr 0.8.2 requires both the server and client to load the setting. A server restart is not needed.

### 2. Install and open the gallery

Run inside Herdr:

```sh
herdr plugin install zbyhoo/herdr-image-gallery
herdr plugin action invoke local.image-gallery.open
```

The plugin ID is `local.image-gallery` for both GitHub and local installations.

### 3. Enable Codex integration (optional)

On first opening, the gallery offers to register its bundled skill:

- Press **y** to install it.
- Press **Enter**, **Esc**, or **n** to skip.
- Press **s** in the gallery to revisit setup later.

Restart Codex after installing the skill, then use Codex from the same Herdr workspace. For example:

> Generate an image and show it using the herdr-image-gallery skill.

Registration creates a symlink at `${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery`. Use the same `CODEX_HOME` for setup and your Codex session. Existing files or unrelated links are never overwritten.

The skill supplies instructions to Codex; it does not intercept image generation or automatically watch folders. Codex must load and follow the skill to publish images.

## Controls

Focus the gallery pane to use these keys.

| Key or action | Result |
| --- | --- |
| Tab or g | Switch between thumbnails and preview |
| Arrow keys / j k | Select or browse images |
| Enter or click a thumbnail | Open the selected image |
| PgUp / PgDn | Change thumbnail pages |
| / | Filter titles and paths |
| Esc | Finish filtering or return to thumbnails; never closes the gallery |
| a | Toggle LIVE / HOLD |
| f | Toggle fullscreen for the gallery pane |
| s | Set up the Codex skill |
| q | Close the gallery without deleting history |

**LIVE** follows newly published images. **HOLD** keeps your current selection; switching back to LIVE displays the latest pending image.

To reopen the gallery:

```sh
herdr plugin action invoke local.image-gallery.open
```

## Send images from scripts or the command line

After registering the bundled skill, run this from the target Herdr workspace:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery/scripts/gallery.py" \
  show /absolute/path/to/image.png \
  --title 'Concept art' --caption 'A city at dusk' --wait 10
```

Without the skill, run `gallery.py` directly from the plugin directory. Locate the installed directory with `herdr plugin list --json`.

| Command or option | Purpose |
| --- | --- |
| `show PATH` | Publish an image and open the gallery if needed |
| `show PATH --no-open` | Queue an image without opening a pane |
| `show PATH --wait 10` | Wait up to 10 seconds for delivery status |
| `open` | Open or reuse the gallery |
| `list` | Print image history as JSON |
| `status` | Print viewer state and diagnostics as JSON |
| `setup-codex` | Prompt to register the bundled Codex skill |
| `setup-codex --yes` | Register the skill with explicit command-line consent |

Publishing reuses the existing pane without changing keyboard focus. Native streams report `delivered: true` and `rendered: null`: submission does not independently confirm pixels on the host screen. `--wait` exits nonzero when delivery is not confirmed or decoding fails.

## Open local image links

Ctrl-click a hyperlink in either of these forms inside Herdr:

- `file:///absolute/path/to/image.png`
- `herdr-image://open?path=<URL-encoded absolute image path>`

Use **Control** on macOS too. The terminal must recognize the text as a hyperlink. HTTP images are not downloaded, and plain unlinked paths do not invoke the handler.

## Storage and supported formats

The gallery supports PNG, JPEG, WebP, GIF, TIFF, BMP, and HEIC through the macOS system decoder. Animated images display a static frame.

History and archived image copies are stored in `~/.local/state/herdr-image-gallery/`. Reopening in the same Herdr workspace restores history, selection, filter, and LIVE/HOLD state. History is separated by Herdr socket and workspace ID; a new workspace identity has its own history.

Archived copies use disk space independently of the source files. Removing the plugin does not remove this history. `HERDR_GALLERY_STATE_DIR` overrides the storage directory.

## Troubleshooting

**The pane opens but images are blank.** Check Kitty graphics support in your outer terminal, enable the Herdr setting above, and detach/reconnect after reloading the configuration.

**Images disappear while PREFIX mode is active.** Herdr 0.8.2 hides graphics in PREFIX mode. Leave that mode with Esc to restore them. This is a Herdr renderer limitation that the plugin cannot fix.

**Codex does not send images.** Press `s` in the gallery, verify skill registration, and restart Codex. Run Codex inside the target Herdr workspace and ask it to use the gallery skill.

**Skill setup reports an existing destination.** Inspect the reported path. If it is an obsolete symlink from an earlier installation, remove that link and retry setup. Preserve any files you still need.

**Installation conflicts with a linked development checkout.** Unregister the local link with `herdr plugin unlink local.image-gallery`, then run the GitHub installation command. If the skill still points to the old checkout, update its registration before removing that checkout.

## Development

Clone the repository and link it to Herdr:

```sh
git clone https://github.com/zbyhoo/herdr-image-gallery.git
cd herdr-image-gallery
herdr plugin link "$PWD"
herdr plugin action invoke local.image-gallery.open
```

Run the tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
```

Tests cover image history, decoding, preview replacement, loading indicators, Codex setup, links, and terminal navigation. Visual behavior also needs checking in Herdr with a compatible outer terminal.

To report a problem, [open an issue](https://github.com/zbyhoo/herdr-image-gallery/issues) with your macOS, Herdr, and terminal versions, reproduction steps, and any error shown in the gallery. Review diagnostic output and screenshots for private paths or image content before sharing them.
