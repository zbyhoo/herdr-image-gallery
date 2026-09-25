---
name: herdr-image-gallery
description: Show generated images, imagegen results, screenshots and locally accessible user-attached images in a live Herdr gallery pane. Use alongside image creation or image review when running inside Herdr, so the user can see graphics without leaving the terminal.
---

# Herdr Image Gallery

The user wants graphics displayed in a terminal gallery as soon as the agent (Codex or Claude Code) creates or receives them. Check `HERDR_ENV=1` and `HERDR_WORKSPACE_ID`; outside Herdr use the normal image presentation flow.

After each final image is saved (including imagegen output), send its absolute local path immediately, before the final answer. When you hold image BYTES with no file the user's machine can read, for example base64 `image` content from an MCP tool result or the output of a remote renderer, send them straight to `show -` (see below) instead of asking anyone to save a file first. Also send user-attached images when their local file paths are available. Do not claim an attachment is available when you only have its visual conversation representation; ask for a local path only if showing it in the gallery is necessary.

Use the bundled `scripts/gallery.py` helper, resolving this skill's directory from its actual installed location. It resolves symlinks and forwards to the plugin checkout. Resolve the helper relative to the directory containing this SKILL.md; never assume the caller uses Codex. In Claude Code use:

```sh
python3 "${CLAUDE_SKILL_DIR}/scripts/gallery.py" show '/absolute/path/image.png' --title 'Short title' --caption 'Short description' --wait 10
```

`CLAUDE_SKILL_DIR` is substituted by Claude Code in this skill, not assumed to be a shell environment variable. If unavailable, use the actual skill directory from the loaded skill path. In Codex, with the default skill directory:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery/scripts/gallery.py" show '/absolute/path/image.png' --title 'Short title' --caption 'Short description' --wait 10
```

Use proper shell quoting for paths and text. Give every image its own title and description: repeat `--title` and `--caption` once per image (`show a.png b.png --title 'A' --title 'B' --caption 'ca' --caption 'cb'`), or use `--stdin-json` below; a single `--title`/`--caption` still applies to all of them. Publishing many images in ONE call is preferred over one process per image.

**Images you have as bytes, with no file:** pipe them into `show -`. Always through stdin, never in an argument: one argument is capped at 128 KiB, far below a real image. Replace `SCRIPT` with the helper path resolved as above.

```sh
# raw bytes straight from a producer
some-renderer --format png | python3 SCRIPT show - --title 'Contact sheet'

# base64 you fetched yourself, so the bytes pass through your shell
curl -fsS "$endpoint" | jq -r '.data' | python3 SCRIPT show - --stdin-base64 --title 'Axe small' --caption 'Rotation, top view' --wait 10

# many images in ONE call, each with its own title and caption
printf '%s' "$entries" | python3 SCRIPT show - --stdin-json --wait 10
```

**An image you can see is not an image you can pipe.** When a tool result reaches
you as conversation content — an MCP `image` block, a rendered screenshot, a user
attachment shown inline — those bytes live in the transcript, not on disk and not
in any shell variable, and you cannot transcribe them: base64 of a real image is
megabytes of text you do not hold as text. `--stdin-base64` and `--stdin-json`
need bytes that a command of yours can actually emit. So publish what you
produced or fetched: render it locally, `curl` it from an endpoint that serves
it, read it off disk. If the only copy sits on a remote host you cannot reach,
say that plainly and ask for what would give you the bytes; do not invent a path
or claim the image was shown.

`--stdin-json` takes a JSON array (or JSON Lines) of entries `{"data": "<base64>", "mimeType": "image/png", "title": "...", "caption": "..."}`; an MCP `image` content block passes through unchanged, and an entry may use `"path"` instead of `"data"` to mix files into the same call. A bad entry is named by index and nothing is published.

The bytes are copied into the gallery's own state directory under a sha256 name, so nothing is written into the user's project, republishing identical bytes updates one history entry instead of adding another, and `status`/`list` show such images exactly like published files. The format comes from the magic bytes; pass `--format` only to declare it (content wins, a mismatch is a warning). Supported: PNG, JPEG, WebP, GIF, TIFF, BMP, HEIC; anything else is refused. Limits: 64 MiB per image, 256 MiB per call, exceeded as a clear error and never a silent truncation. The gallery never fetches images from URLs: download the bytes with your own tools if the user asks for that, then pipe them in.

`show` opens the registered plugin pane to the RIGHT when needed and otherwise updates it without changing keyboard focus. Preserve the user's chosen layout; do not force the gallery below the conversation. It routes by the caller's Herdr socket and workspace, so never override those to target a different project.

The user owns the gallery's position and size after it opens. Never move, swap, resize, close/recreate or refocus an existing gallery as part of displaying images, debugging or updating the plugin. A right split is only the default for the FIRST opening or after the user has closed the gallery. Code updates reload in the same terminal process and pane; preserve any location the user chose, including placement above/below other right-column tools. Only an explicit user request to rearrange the gallery authorizes layout changes.

Native Herdr streams report `delivered: true`, `rendered: null`, and `acknowledgement: "herdr-stream-submitted"`; this confirms submission, not host pixels. Do not claim visual verification from that result. For the raw terminal fallback, `rendered: true` means Herdr's pane terminal acknowledged the decoded graphics commands, not independently verified host pixels. A nonzero exit means delivery/rendering was not confirmed; inspect `gallery.py status` and report the actual error. A HOLD viewer intentionally delays requests until the user presses `a`; do not override their choice. Local Herdr/socket/state access may require the normal sandbox escalation; do not bypass it.

Herdr's outer client requires `[experimental] kitty_graphics = true` in its config and a Kitty-compatible outer terminal (for example Ghostty). After newly enabling it, run `herdr server reload-config` AND detach the client with Ctrl+B then Q and reattach with `herdr`; both server and client need the updated flag. Never stop the server or kill the session to apply it.

For a clickable gallery link, use `herdr-image://open?path=<URL-encoded absolute path>`; Ctrl-click invokes the native Herdr link handler (Control also on macOS). `file://` image hyperlinks are also registered. Keep normal file/download links when useful; the gallery link is specifically for terminal viewing. Only link existing local images. Do not promise that a plain unlinked path or an HTTP image URL will invoke the handler.

Self-service opening: `herdr plugin action invoke local.image-gallery.open`. Controls: Tab or g toggles the thumbnail grid; arrows select; Enter or click opens a thumbnail. `c` copies the selected image to the system clipboard at full resolution, with transparency preserved (a static frame for animations); `/` filters, `d` browses a directory, `r` toggles recursion in directory view, `o` toggles directory sort (name / newest), `a` toggles LIVE/HOLD, `f` toggles pane fullscreen, `q` closes the viewer without deleting history. While filtering or entering a directory path, `c` types into the prompt; in agent setup it selects Codex. On Linux, decoding requires ImageMagick; copying requires `wl-copy` on Wayland or `xclip` on X11. Files are never uploaded or altered. Archival copies and view state survive reopening in the same Herdr socket/workspace; a different workspace has separate history.

When the user asks to see the images in a folder, run `scripts/gallery.py browse '/absolute/path/to/dir'` (add `--recursive` for subfolders). This lists every supported image in that directory in the gallery pane; it does not publish them into history. Directory scans run in the background so the pane stays responsive; the footer shows `scanning...` while a new directory is first read, and `2000+ images, limited` when a huge tree (e.g. `~` or `/`) is capped. Use `show` for individual generated or attached files.

Escape must never close the gallery: it ends filter editing, cancels the directory prompt, or returns to thumbnails. Only q closes it. Preserve this explicit user preference when changing navigation.

The gallery does not provide an image-generation service. Use the agent's available tools to create images; publish any resulting local files. The shared gallery is scoped to the Herdr workspace, not to one agent, so Codex and Claude Code can contribute to the same history. If another agent publishes a new image, LIVE follows it; HOLD preserves the user's selection.

If Herdr reports that local.image-gallery is not installed, explain that the gallery skill needs its native Herdr plugin. When the user has requested gallery setup, run `herdr plugin install zbyhoo/herdr-image-gallery --yes`, then `herdr plugin action invoke local.image-gallery.open`. Do not replace an already linked plugin or change its source automatically. Installing the skill alone does not grant permission for unrelated installs or to alter the terminal layout.
