# Herdr Image Gallery

View AI-generated images, screenshots, and local artwork inside [Herdr](https://herdr.dev), without leaving your terminal. Browse a thumbnail grid, open a larger preview, or let Codex or Claude Code send images to the gallery as it creates them.

The plugin includes a shared Codex / Claude Code skill and works independently through its CLI and local image links.

## Features

- **Thumbnail browsing:** select with arrow keys or click to open a preview.
- **Smooth preview switching:** the current image stays visible while the next one loads, with a centered loading indicator for slower requests.
- **Agent integration:** a bundled skill instructs Codex and Claude Code to send generated images and accessible local attachments to the gallery.
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

### 3. Use it with Codex or Claude Code

Opening the gallery automatically registers its bundled skill for detected agents. No separate skill download or manual symlink is required. Detection checks the agent executable on PATH or its existing configuration directory:

| Agent | Skill location |
| --- | --- |
| Codex | `${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery` |
| Claude Code | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/herdr-image-gallery` |

The plugin links the skill to its installed checkout. It preserves conflicting files and reports their paths. Registration also runs when a Herdr server starts; it does not restart agents or change their permission settings. GitHub installation alone does not run the startup hook: use the **open command above** to complete immediate setup. No server restart is needed.

For **Claude Code**, ask it to show an image, or invoke `/herdr-image-gallery`. For **Codex**, restart it if the skill is not loaded, then ask it to use `$herdr-image-gallery`. If Claude does not list the newly registered skill, restart Claude as well. Both agents must run inside the intended Herdr workspace. An already running agent may need to load the skill before automatic use begins.

Example prompt for either agent:

> Show each image you create or review using the herdr-image-gallery skill. Preserve the gallery's pane position.

Codex and Claude share gallery history within a workspace. LIVE follows whichever agent publishes the latest image; HOLD preserves your selection. The gallery does not add image generation to an agent: it displays local files produced by tools that agent already has. Attachments without an accessible local path cannot be imported automatically.

**Let an agent install the gallery:** paste this into Claude Code or Codex running in Herdr:

```text
Install zbyhoo/herdr-image-gallery using `herdr plugin install zbyhoo/herdr-image-gallery --yes`,
then run `herdr plugin action invoke local.image-gallery.open`.
If it is already installed or linked, reuse that installation instead of replacing it.
Load the herdr-image-gallery skill and use it to display local images.
Preserve existing pane positions and my agent permissions.
```

The first installation still needs this instruction or the quick-start commands; an agent cannot discover a plugin that has never been installed.

To opt out of automatic registration, set `HERDR_GALLERY_AUTO_SETUP=0` in the environment used to launch Herdr. This prevents future automatic registration and does not remove existing links. Press **s** in the gallery for manual setup: **y** both agents, **c** Codex, **l** Claude Code, **Esc / Enter / n** cancel. Explicit setup works even with automatic registration disabled.

Run setup with the same `CODEX_HOME` / `CLAUDE_CONFIG_DIR` as your agents. If a server started with different environment settings, run the explicit setup command from the agent's shell instead.

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
| s | Set up Codex / Claude Code |
| q | Close the gallery without deleting history |

**LIVE** follows newly published images. **HOLD** keeps your current selection; switching back to LIVE displays the latest pending image.

To reopen the gallery:

```sh
herdr plugin action invoke local.image-gallery.open
```

## Send images from scripts or the command line

After registering the bundled skill, run this (Codex example) from the target Herdr workspace:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery/scripts/gallery.py" \
  show /absolute/path/to/image.png \
  --title 'Concept art' --caption 'A city at dusk' --wait 10
```

For Claude Code, use the same arguments with this helper:

```sh
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/herdr-image-gallery/scripts/gallery.py" \
  show /absolute/path/to/image.png --wait 10
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
| `setup-agents --agent both --yes` | Register the skill for both agents |
| `setup-claude --yes` | Register only the Claude Code skill |
| `setup-codex --yes` | Register only the Codex skill (existing command retained) |
| `auto-setup` | Register detected agents unless automatic setup is disabled |
| `agent-status` | Report skill installation status for both agents |

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

**An agent does not send images.** Press `s` in the gallery or run `gallery.py agent-status`. Verify the relevant skill is registered in the agent's configuration directory. Ask Claude Code to invoke `/herdr-image-gallery`, or restart Codex and load the skill. Restart Claude if it does not discover a new skill. Keep the agent inside the target Herdr workspace.

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

Tests cover image history, decoding, preview replacement, loading indicators, agent setup, links, and terminal navigation. Visual behavior also needs checking in Herdr with a compatible outer terminal.

To report a problem, [open an issue](https://github.com/zbyhoo/herdr-image-gallery/issues) with your macOS, Herdr, and terminal versions, reproduction steps, and any error shown in the gallery. Review diagnostic output and screenshots for private paths or image content before sharing them.

Agent integration follows the documented [Claude Code skill directories and invocation](https://code.claude.com/docs/en/skills). Normal agent permissions still apply; installing the gallery does not grant unrestricted shell access.
