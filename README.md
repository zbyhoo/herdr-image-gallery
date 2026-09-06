# Herdr Image Gallery

A native [Herdr](https://herdr.dev) plugin for viewing generated images and local attachments without leaving the terminal.

## Features

- A dedicated gallery pane, with a thumbnail grid and a large preview.
- Arrow-key and mouse selection, filtering, and LIVE/HOLD modes.
- Direct publication from Codex through a companion skill.
- Ctrl-click handlers for local image hyperlinks.
- Persistent history, archived image copies, and restored selection/view.
- Cached previews and native Herdr graphics layers that follow pane layout changes. Moving the thumbnail selection does not resend images.
- Preview switching keeps the previous image until decoding completes; a centered spinner appears after 180 ms. Decode failures preserve the previous image.
- User-owned layout: updates never reposition an existing pane.

## Requirements

- macOS, Python 3.9+ and the system `/usr/bin/sips` image decoder.
- Herdr 0.8.2 or newer.
- An outer terminal supporting Kitty graphics, such as Ghostty.
- No pip or npm dependencies.

In Herdr's `~/.config/herdr/config.toml`, enable:

```toml
[experimental]
kitty_graphics = true
```

Then run `herdr server reload-config`, detach the client with Ctrl+B then Q, and reconnect with `herdr`. In Herdr 0.8.2 both server and client must load this setting. Do not stop the server.

## Installation

### From a local checkout

From the repository root:

```sh
herdr plugin link "$PWD"
herdr plugin action invoke local.image-gallery.open
```

### From GitHub

After this repository has been published (replace OWNER with the actual account):

```sh
herdr plugin install OWNER/herdr-image-gallery
herdr plugin action invoke local.image-gallery.open
```

The stable plugin ID is `local.image-gallery`, regardless of the installation source. If switching this same plugin from a local link to GitHub installation, unregister the local link first with `herdr plugin unlink local.image-gallery`. Do not close or rearrange user panes as part of installation.

## Controls

| Key / action | Result |
| --- | --- |
| Tab or g | Switch between thumbnails and large preview |
| Arrows / j k | Select or browse images |
| Enter or click a thumbnail | Open the selected image |
| PgUp / PgDn | Change thumbnail pages |
| / | Filter titles and paths |
| Esc | End filter editing or return to thumbnails; never closes the gallery |
| a | Toggle LIVE / HOLD |
| f | Toggle fullscreen for the current pane |
| s | Set up the bundled Codex skill |
| q | Close the gallery; history is retained |

Reopen at any time with:

```sh
herdr plugin action invoke local.image-gallery.open
```

The initial pane opens to the right. Once opened, the user's position and size are preserved. Source updates reload in the same process and pane.

## Send an image

From inside the target Herdr workspace, using the path to your checkout:

```sh
python3 /path/to/herdr-image-gallery/gallery.py show /absolute/image.png \
  --title 'Concept' --caption 'Art direction' --wait 10
```

`show` reuses the existing gallery without changing keyboard focus. It opens a pane only if the gallery is closed. `--no-open` queues the image without opening one. `--wait` exits nonzero if delivery is not confirmed or decoding fails. Native Herdr layers report `delivered: true` and `rendered: null`: inline streams do not acknowledge host pixels.

Other commands: `open`, `list`, and `status`.

LIVE follows explicit messages from the agent, not unrelated filesystem activity. HOLD postpones the latest request until resumed.

## Codex integration

The skill is bundled: no separate download is needed. On first opening, the gallery asks whether to register it with Codex. Press **y** to install, or **Enter / Esc / n** to skip. Press **s** in the gallery to revisit setup at any time. Skipping is remembered for this workspace.

Registration creates a symlink under `${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery` pointing into the installed plugin, including a GitHub-managed installation. Existing unrelated files or links are preserved and reported as a conflict; inspect and remove an obsolete link yourself before retrying. Run setup with the same `CODEX_HOME` as your Codex session.

Restart Codex after registration so it discovers the skill. The skill instructs Codex to publish final imagegen results and accessible local attachments. It does not intercept imagegen: Codex must load and follow the skill. The gallery also works without Codex through the CLI and local hyperlinks.

For setup outside the viewer, run `python3 /path/to/herdr-image-gallery/gallery.py setup-codex`. This asks for consent; `--yes` explicitly consents for scripted installation. Uninstalling the plugin does not delete gallery history; remove its skill symlink separately if no longer needed.

A portable helper is also available through the installed skill:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery/scripts/gallery.py" show /absolute/image.png --wait 10
```

## Clickable links

Ctrl-click a `file:///absolute/image.png` hyperlink or a `herdr-image://open?path=<URL-encoded absolute path>` hyperlink in Herdr. Control is used on macOS too. The link handler opens the image in the existing gallery and preserves its title/caption.

Only local images are handled; HTTP images are not downloaded. The terminal must expose the text as a hyperlink.

## Persistence and formats

History is stored under `~/.local/state/herdr-image-gallery/`, isolated by Herdr socket and workspace ID. Full image copies are stored in `images/`, deduplicated by content hash. Temporary source files can disappear without losing their archived images.

Reopening in the same workspace restores history, selection, filter and LIVE/HOLD state. A different socket or workspace identity has separate history. No network listener or upload is used. `HERDR_GALLERY_STATE_DIR` overrides storage for isolated tests.

PNG, JPEG, WebP, GIF, TIFF, BMP and HEIC are decoded by the macOS system codec. Animated formats display a static frame. Source files are not modified. Previews have a longest edge of at most 960 pixels and a 32-frame cache; final display latency also depends on Herdr and the outer terminal.


Known Herdr 0.8.2 limitation: images are hidden while PREFIX mode is active and return after leaving it (for example with Esc). The host renderer restricts graphics to terminal mode; fixing this requires a Herdr change. The plugin's resize refresh does not fix PREFIX-mode visibility.

## Development

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
```

Tests cover IPC, workspace isolation, image archiving, BMP decoding, links, thumbnail selection, layout reuse and interactive PTY restoration. Host rendering still requires visual verification in the actual terminal.

## Publishing this repository

Create an empty GitHub repository (without an initial README), then:

```sh
git remote add origin git@github.com:OWNER/herdr-image-gallery.git
git push -u origin main
```

Keep `herdr-plugin.toml` at the repository root. Adding the GitHub topic `herdr-plugin` makes the package eligible for discovery in the [Herdr marketplace](https://herdr.dev/docs/marketplace/).

