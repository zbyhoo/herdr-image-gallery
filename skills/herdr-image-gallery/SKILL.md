---
name: herdr-image-gallery
description: Show generated images, imagegen results, screenshots and locally accessible user-attached images in a live Herdr gallery pane. Use alongside image creation or image review when running inside Herdr, so the user can see graphics without leaving the terminal.
---

# Herdr Image Gallery

The user wants graphics displayed in a terminal gallery as soon as Codex creates or receives them. Check `HERDR_ENV=1` and `HERDR_WORKSPACE_ID`; outside Herdr use the normal image presentation flow.

After each final image is saved (including imagegen output), send its absolute local path immediately, before the final answer. Also send user-attached images when their local file paths are available. Do not claim an attachment is available when you only have its visual conversation representation; ask for a local path only if showing it in the gallery is necessary.

Use the bundled `scripts/gallery.py` helper, resolving this skill's directory from its actual installed location. It resolves symlinks and forwards to the plugin checkout. With the default Codex skill directory:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-image-gallery/scripts/gallery.py" show '/absolute/path/image.png' --title 'Short title' --caption 'Short description' --wait 10
```

Use proper shell quoting for paths and text. Send each image separately in a batch so it has its own title and description. `show` opens the registered plugin pane to the RIGHT when needed and otherwise updates it without changing keyboard focus. Preserve the user's chosen layout; do not force the gallery below the conversation. It routes by the caller's Herdr socket and workspace, so never override those to target a different project.

The user owns the gallery's position and size after it opens. Never move, swap, resize, close/recreate or refocus an existing gallery as part of displaying images, debugging or updating the plugin. A right split is only the default for the FIRST opening or after the user has closed the gallery. Code updates reload in the same terminal process and pane; preserve any location the user chose, including placement above/below other right-column tools. Only an explicit user request to rearrange the gallery authorizes layout changes.

Native Herdr streams report `delivered: true`, `rendered: null`, and `acknowledgement: "herdr-stream-submitted"`; this confirms submission, not host pixels. Do not claim visual verification from that result. For the raw terminal fallback, `rendered: true` means Herdr's pane terminal acknowledged the decoded graphics commands, not independently verified host pixels. A nonzero exit means delivery/rendering was not confirmed; inspect `gallery.py status` and report the actual error. A HOLD viewer intentionally delays requests until the user presses `a`; do not override their choice. Local Herdr/socket/state access may require the normal sandbox escalation; do not bypass it.

Herdr's outer client requires `[experimental] kitty_graphics = true` in its config and a Kitty-compatible outer terminal (for example Ghostty). After newly enabling it, run `herdr server reload-config` AND detach the client with Ctrl+B then Q and reattach with `herdr`; both server and client need the updated flag in Herdr 0.8.2. Never stop the server or kill the session to apply it.

For a clickable gallery link, use `herdr-image://open?path=<URL-encoded absolute path>`; Ctrl-click invokes the native Herdr link handler (Control also on macOS). `file://` image hyperlinks are also registered. Keep normal file/download links when useful; the gallery link is specifically for terminal viewing. Only link existing local images. Do not promise that a plain unlinked path or an HTTP image URL will invoke the handler.

Self-service opening: `herdr plugin action invoke local.image-gallery.open`. Controls: Tab or g toggles the thumbnail grid; arrows select; Enter or click opens a thumbnail. `/` filters, `a` toggles LIVE/HOLD, `f` toggles pane fullscreen, `q` closes the viewer without deleting history. Files are never uploaded or altered. Archival copies and view state survive reopening in the same Herdr socket/workspace; a different workspace has separate history.

Escape must never close the gallery: it ends filter editing or returns to thumbnails. Only q closes it. Preserve this explicit user preference when changing navigation.
