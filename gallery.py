#!/usr/bin/env python3
"""Herdr image pane and workspace-scoped CLI; Python stdlib + macOS sips."""
import argparse
import base64
from collections import deque
import fcntl
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty
from urllib.parse import parse_qs, unquote, urlsplit
import zlib

PLUGIN = "local.image-gallery"
IMAGE_ID = 71031
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".heic"}


def herdr(*args):
    result = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), *args],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def connect():
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if os.environ.get("HERDR_ENV") != "1" or not workspace or not socket:
        raise RuntimeError("Run inside a Herdr workspace (HERDR_ENV, WORKSPACE_ID and SOCKET_PATH required).")
    root = Path(os.environ.get("HERDR_GALLERY_STATE_DIR", str(Path.home() / ".local/state/herdr-image-gallery")))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256((socket + "\0" + workspace).encode()).hexdigest()[:24]
    db = sqlite3.connect(str(root / (key + ".sqlite3")), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE, title TEXT, caption TEXT, updated REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    if "cached_path" not in {row[1] for row in db.execute("PRAGMA table_info(images)")}:
        db.execute("ALTER TABLE images ADD COLUMN cached_path TEXT")
    db.commit()
    for row in db.execute("SELECT id,path FROM images WHERE cached_path IS NULL").fetchall():
        try:
            cached = archive(db, image_path(row["path"]))
        except (OSError, ValueError):
            continue
        with db:
            db.execute("UPDATE images SET cached_path=? WHERE id=?", (str(cached), row["id"]))
    return db


def get(db, key, default=""):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def put(db, **values):
    with db:
        db.executemany("INSERT OR REPLACE INTO state VALUES (?,?)", [(k, str(v)) for k, v in values.items()])


def clean(text):
    return "".join(c if c.isprintable() else " " for c in text)


def image_path(value):
    p = Path(value).expanduser().resolve(strict=True)
    if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
        raise ValueError("Unsupported image: " + str(p))
    if p.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Image exceeds 64 MiB: " + str(p))
    return p


def archive(db, path):
    directory = Path(db.execute("PRAGMA database_list").fetchone()[2]).parent / "images"
    directory.mkdir(exist_ok=True, mode=0o700)
    data = path.read_bytes()
    destination = directory / (hashlib.sha256(data).hexdigest() + path.suffix.lower())
    if not destination.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
            temporary = Path(out.name)
            out.write(data)
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def display_path(item):
    return Path(item["cached_path"] or item["path"])


def publish(db, path, title="", caption=""):
    path = image_path(path)
    cached = archive(db, path)
    prior = db.execute("SELECT title,caption FROM images WHERE path=?", (str(path),)).fetchone()
    title = title or (prior["title"] if prior else path.stem)
    caption = caption or (prior["caption"] if prior else "")
    token = str(time.time_ns())
    with db:
        db.execute("INSERT INTO images(path,title,caption,updated,cached_path) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(path) DO UPDATE SET title=excluded.title,caption=excluded.caption,updated=excluded.updated,cached_path=excluded.cached_path",
                   (str(path), title, caption, time.time(), str(cached)))
        db.executemany("INSERT OR REPLACE INTO state VALUES (?,?)",
                       [("requested_path", str(path)), ("request", token)])
    return token


def link_path(url):
    parsed = urlsplit(url)
    if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
        path = unquote(parsed.path)
    elif parsed.scheme == "herdr-image" and parsed.netloc == "open":
        path = parse_qs(parsed.query).get("path", [""])[0]
    else:
        raise ValueError("Gallery links must reference a local image.")
    if not Path(path).is_absolute():
        raise ValueError("Gallery links require an absolute image path.")
    return image_path(path)


def open_pane(db):
    if time.time() - float(get(db, "heartbeat", "0")) < 4:
        return {"existing_pane": get(db, "pane")}
    existing = get(db, "pane")
    if existing:
        try:
            herdr("pane", "get", existing)["result"]["pane"]
        except RuntimeError as exc:
            try:
                code = json.loads(str(exc)).get("error", {}).get("code")
            except (ValueError, TypeError):
                raise exc
            if code != "pane_not_found":
                raise
        else:
            # Also preserve a pane the user renamed. A slow render/reload is not
            # permission to create a duplicate or alter the layout.
            return {"existing_pane": existing}
    target = os.environ.get("HERDR_PANE_ID")
    if not target:
        raise RuntimeError("No caller pane; cannot open a gallery safely.")
    return herdr("plugin", "pane", "open", "--plugin", PLUGIN, "--entrypoint", "gallery",
                 "--placement", "split", "--direction", "right",
                 "--target-pane", target, "--no-focus")


def decode_bmp(data):
    if data[:2] != b"BM" or len(data) < 54:
        raise ValueError("Decoder did not produce BMP.")
    offset = struct.unpack_from("<I", data, 10)[0]
    _, width, signed_height, planes, bits, compression = struct.unpack_from("<IiiHHI", data, 14)
    if width <= 0 or signed_height == 0 or planes != 1 or bits not in (24, 32) or compression not in (0, 3):
        raise ValueError("Unsupported system BMP format.")
    if compression == 3:
        if bits != 32 or len(data) < 66 or struct.unpack_from("<III", data, 54) != (0xFF0000, 0xFF00, 0xFF):
            raise ValueError("Unsupported BMP channel masks.")
    height, bpp = abs(signed_height), bits // 8
    stride = ((width * bpp + 3) // 4) * 4
    if offset + stride * height > len(data):
        raise ValueError("Truncated system BMP.")
    rgb = bytearray(width * height * 3)
    for y in range(height):
        sy = y if signed_height < 0 else height - y - 1
        row = data[offset + sy * stride:offset + sy * stride + width * bpp]
        start, end = y * width * 3, (y + 1) * width * 3
        rgb[start:end:3] = row[2::bpp]
        rgb[start + 1:end:3] = row[1::bpp]
        rgb[start + 2:end:3] = row[0::bpp]
    return bytes(rgb), width, height


def image_data(path, max_pixels):
    # Decode and resize using the system codec, without altering source files.
    with tempfile.TemporaryDirectory(prefix="herdr-gallery-") as tmp:
        output = Path(tmp) / "preview.bmp"
        result = subprocess.run(["/usr/bin/sips", "-s", "format", "bmp", "-Z", str(max_pixels),
                                 str(path), "--out", str(output)], capture_output=True, timeout=20)
        if result.returncode or not output.exists():
            raise ValueError("System image decoder could not read this file.")
        data = output.read_bytes()
    return decode_bmp(data)


@lru_cache(maxsize=32)
def preview(path, modified, available_width, available_height):
    # PNG dimensions cost one tiny read; other codecs use the system metadata reader.
    with open(path, "rb") as source:
        header = source.read(24)
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", header[16:24])
    else:
        metadata = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                                  capture_output=True, text=True, check=True, timeout=10).stdout
        width = int(re.search(r"pixelWidth:\s*(\d+)", metadata).group(1))
        height = int(re.search(r"pixelHeight:\s*(\d+)", metadata).group(1))
    scale = min(available_width / width, available_height / height, 960 / max(width, height), 1)
    data, width, height = image_data(path, max(1, round(max(width, height) * scale)))
    # Keep compressed, display-sized frames for back/forward navigation.
    return base64.b64encode(zlib.compress(data, 1)), width, height


def fit(width, height, columns, rows, cell_w=8, cell_h=16):
    scale = min(columns * cell_w / width, rows * cell_h / height)
    return max(1, min(columns, int(width * scale / cell_w))), max(1, min(rows, int(height * scale / cell_h)))


def transmit(encoded, width, height, columns, rows, image_id=IMAGE_ID):
    chunks = [encoded[i:i + 4096] for i in range(0, len(encoded), 4096)]
    for i, chunk in enumerate(chunks):
        header = ("a=T,f=24,o=z,s=%d,v=%d,t=d,i=%d,c=%d,r=%d,q=%d,C=1," % (width, height, image_id, columns, rows, 0 if image_id == IMAGE_ID else 2)) if i == 0 else ""
        sys.stdout.buffer.write(b"\x1b_G" + (header + "m=%d" % (i < len(chunks) - 1)).encode() + b";" + chunk + b"\x1b\\")
    sys.stdout.buffer.flush()


def delete_image():
    for image_id in range(IMAGE_ID, IMAGE_ID + 25):
        sys.stdout.write("\x1b_Ga=d,d=I,i=%d,q=2;\x1b\\" % image_id)


def terminal_size():
    rows, cols, pxw, pxh = struct.unpack("HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
    return max(cols, 10), max(rows, 10), pxw / cols if pxw and cols else 8, pxh / rows if pxh and rows else 16


def grid_geometry(cols, rows, count, selected):
    columns = min(4, max(1, (cols - 2) // 28))
    grid_rows = min(6, max(1, (rows - 7) // 9))
    page_size = columns * grid_rows
    start = selected // page_size * page_size
    return columns, grid_rows, page_size, start


def draw(items, index, query, searching, paused, cache, mode="preview"):
    cols, rows, cw, ch = terminal_size()
    signature = None
    if mode == "grid" and items:
        columns, grid_rows, page_size, start = grid_geometry(cols, rows, len(items), index)
        signature = (cols, rows, cw, ch, start, tuple((r["path"], r["updated"]) for r in items[start:start + page_size]))
    reuse_grid = signature is not None and signature == cache.get("grid_signature")
    if not reuse_grid:
        delete_image()
        sys.stdout.write("\x1b[2J\x1b[H")
    cache["grid_signature"] = signature

    def line(row, text, color="37"):
        sys.stdout.write("\x1b[%d;1H\x1b[2K\x1b[%sm%s\x1b[0m" % (row, color, clean(text)[:cols - 1]))

    line(1, "IMAGE GALLERY  |  " + os.environ["HERDR_WORKSPACE_ID"] + "  |  " + ("HOLD" if paused else "LIVE"), "1;36")
    line(2, "Tab: thumbnails  arrows: select  Enter: open  /: filter  a: live/hold  f: zoom  q: close", "90")
    if not items:
        line(4, "Waiting for Codex to send an image." if not query else "No matching images.")
        line(rows, "/ " + query if searching else "Codex: gallery.py show /absolute/path/image.png")
        sys.stdout.flush()
        return ""
    item = items[index]
    if mode == "grid":
        columns, grid_rows, page_size, start = grid_geometry(cols, rows, len(items), index)
        cell_cols, cell_rows = (cols - 2) // columns, max(1, (rows - 7) // grid_rows)
        line(3, "THUMBNAILS  %d/%d  |  page %d/%d" % (index + 1, len(items), start // page_size + 1,
                                                        (len(items) + page_size - 1) // page_size), "1;37")
        for slot, thumb in enumerate(items[start:start + page_size]):
            x, y = 2 + slot % columns * cell_cols, 5 + slot // columns * cell_rows
            active = start + slot == index
            label = ("> " if active else "  ") + str(start + slot + 1) + " " + clean(thumb["title"])
            sys.stdout.write("\x1b[%d;%dH\x1b[%sm%s\x1b[0m" % (y, x, "1;30;46" if active else "90", label[:cell_cols - 1].ljust(cell_cols - 1)))
            if reuse_grid:
                continue
            try:
                path = display_path(thumb)
                data, width, height = preview(str(path), path.stat().st_mtime_ns,
                                             max(1, round((cell_cols - 2) * cw)), max(1, round((cell_rows - 2) * ch)))
                ic, ir = fit(width, height, max(1, cell_cols - 2), max(1, cell_rows - 2), cw, ch)
                sys.stdout.write("\x1b[%d;%dH" % (y + 1 + max(0, (cell_rows - 2 - ir) // 2), x + max(0, (cell_cols - ic) // 2)))
                sys.stdout.flush()
                transmit(data, width, height, ic, ir, IMAGE_ID + 1 + slot)
            except (OSError, ValueError, subprocess.SubprocessError):
                sys.stdout.write("\x1b[%d;%dH\x1b[31mMissing image\x1b[0m" % (y + 2, x))
        line(rows - 1, item["title"], "36")
        line(rows, "/ " + query if searching else "Arrows: select  Enter/click: open  Tab: switch view  PgUp/PgDn: pages", "90")
        sys.stdout.flush()
        return ""
    line(3, "%d/%d  %s" % (index + 1, len(items), item["title"]), "1;37")
    error = ""
    try:
        path = display_path(item)
        started = time.monotonic()
        data, width, height = preview(str(path), path.stat().st_mtime_ns,
                                     max(1, round((cols - 2) * cw)), max(1, round((rows - 9) * ch)))
        cache["metrics"] = json.dumps({"preview_ms": round((time.monotonic() - started) * 1000, 1),
                                       "bytes": len(data), "width": width, "height": height})
        ic, ir = fit(width, height, cols - 2, max(1, rows - 9), cw, ch)
        sys.stdout.write("\x1b[%d;%dH" % (5 + max(0, (rows - 9 - ir) // 2), 1 + (cols - ic) // 2))
        sys.stdout.flush()
        transmit(data, width, height, ic, ir)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        error = str(exc)
        line(6, "Cannot display: " + error, "31")
    line(rows - 3, item["caption"], "37")
    line(rows - 2, item["path"], "90")
    line(rows, "/ " + query if searching else "New Codex images appear automatically in LIVE mode.", "36")
    sys.stdout.flush()
    return error


def gallery(db):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("The viewer requires an interactive terminal pane.")
    if time.time() - float(get(db, "heartbeat", "0")) < 4:
        raise RuntimeError("A gallery is already running for this workspace.")
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    resume = json.loads(os.environ.pop("HERDR_GALLERY_RESUME", get(db, "view_state", "{}")))
    query, selected, last_request = resume.get("query", ""), resume.get("path", get(db, "requested_path")), resume.get("request", "")
    searching = paused = False
    paused = resume.get("paused", False)
    mode = resume.get("mode", "preview")
    cache, previous, heartbeat = {}, None, 0
    running = True
    pending = deque()
    source_revision = Path(__file__).stat().st_mtime_ns
    reload_source = False

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")
    sys.stdout.flush()
    tty.setraw(fd)
    try:
        while running:
            if time.time() - heartbeat > 1:
                if Path(__file__).stat().st_mtime_ns != source_revision:
                    reload_source = True
                    break
                heartbeat = time.time()
                put(db, heartbeat=heartbeat, pane=os.environ.get("HERDR_PANE_ID", ""))
            request = get(db, "request")
            if request != last_request and not paused:
                selected, query = get(db, "requested_path"), ""
                searching, last_request, mode = False, request, "preview"
            items = [r for r in db.execute("SELECT * FROM images ORDER BY id")
                     if query.casefold() in (r["title"] + " " + r["path"]).casefold()]
            index = next((i for i, r in enumerate(items) if r["path"] == selected), 0)
            if items:
                selected = items[index]["path"]
            state = ([(r["path"], r["updated"]) for r in items], index, query, searching, paused, terminal_size(), last_request, mode)
            if state != previous:
                error = draw(items, index, query, searching, paused, cache, mode)
                put(db, displayed_path=selected, rendered_request=last_request, error=error,
                    preview_metrics=cache.get("metrics", ""),
                    view_state=json.dumps({"path": selected, "query": query, "request": last_request, "paused": paused, "mode": mode}))
                previous = state
            if not pending:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                incoming = os.read(fd, 4096).decode("utf-8", "ignore")
                if incoming == "\x1b" and select.select([fd], [], [], 0.04)[0]:
                    incoming += os.read(fd, 4096).decode("utf-8", "ignore")
                pending.extend(re.findall(r"\x1b_G.*?\x1b\\|\x1b\[[0-?]*[ -/]*[@-~]|[^\x1b]|\x1b", incoming, re.S))
            if not pending:
                continue
            key = pending.popleft()
            # Kitty replies are APC control strings, never user input.
            if key.startswith("\x1b_G"):
                match = re.search(r"\x1b_G[^;]*;([^\x1b]+)", key)
                if match:
                    reply = match.group(1)
                    put(db, terminal_reply=reply, terminal_request=last_request)
                    if reply != "OK":
                        put(db, error=reply)
                continue
            if key == "\x03":
                break
            mouse = re.fullmatch(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])", key)
            if mouse:
                button, x, y = map(int, mouse.groups()[:3])
                if mode == "grid" and button == 0 and mouse.group(4) == "M" and items:
                    cols, rows = terminal_size()[:2]
                    columns, grid_rows, page_size, start = grid_geometry(cols, rows, len(items), index)
                    cell_cols, cell_rows = (cols - 2) // columns, max(1, (rows - 7) // grid_rows)
                    gx, gy = (x - 2) // cell_cols, (y - 5) // cell_rows
                    target = start + gy * columns + gx
                    if 0 <= gx < columns and 0 <= gy < grid_rows and target < len(items):
                        selected, mode = items[target]["path"], "preview"
                continue
            if searching:
                if key in ("\r", "\n", "\x1b"):
                    searching = False
                elif key in ("\x7f", "\b"):
                    query = query[:-1]
                elif key.isprintable():
                    query += key
                continue
            if key == "\x1b":
                mode = "grid"
                continue
            if key == "q":
                break
            if key in ("\t", "g"):
                mode = "preview" if mode == "grid" else "grid"
            elif key in ("\r", "\n") and mode == "grid":
                mode = "preview"
            elif key == "/":
                searching, query = True, ""
            elif key == "a":
                paused = not paused
                if not paused:
                    last_request = ""
            elif key == "f":
                try:
                    herdr("pane", "zoom", "--current", "--toggle")
                except RuntimeError:
                    pass
                previous = None
            elif items and key in ("j", "l", "\x1b[C", "\x1b[B", "k", "h", "\x1b[D", "\x1b[A", "\x1b[5~", "\x1b[6~"):
                step = 1 if key in ("j", "l", "\x1b[C", "\x1b[B") else -1
                if mode == "grid":
                    columns, _, page_size, _ = grid_geometry(*terminal_size()[:2], len(items), index)
                    if key in ("j", "\x1b[B", "k", "\x1b[A"):
                        step *= columns
                    elif key in ("\x1b[5~", "\x1b[6~"):
                        step = page_size if key == "\x1b[6~" else -page_size
                selected = items[(index + step) % len(items)]["path"]
    finally:
        put(db, heartbeat=0)
        delete_image()
        sys.stdout.write("\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    if reload_source:
        os.environ["HERDR_GALLERY_RESUME"] = json.dumps({"path": selected, "query": query, "request": last_request, "paused": paused, "mode": mode})
        db.close()
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="view", choices=["view", "show", "open", "list", "status", "link"])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--title", default="")
    parser.add_argument("--caption", default="")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--open", action="store_true", dest="open_action")
    parser.add_argument("--wait", type=float, default=0)
    args = parser.parse_args()
    if args.command == "link":
        context = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}"))
        url = os.environ.get("HERDR_PLUGIN_CLICKED_URL") or context.get("clicked_url", "")
        args.paths = [str(link_path(url))]
        args.command = "show"
    db = connect()
    try:
        if args.open_action or args.command == "open":
            print(json.dumps(open_pane(db)))
        elif args.command == "show":
            if not args.paths:
                parser.error("show requires at least one image path")
            paths = [image_path(p) for p in args.paths]
            for p in paths:
                token = publish(db, p, args.title, args.caption)
            if not args.no_open:
                open_pane(db)
            end = time.monotonic() + args.wait
            while args.wait and get(db, "terminal_request") != token and time.monotonic() < end:
                if get(db, "rendered_request") == token and get(db, "error"):
                    break
                time.sleep(0.1)
            rendered = get(db, "terminal_request") == token and get(db, "terminal_reply") == "OK"
            error = get(db, "error") if get(db, "rendered_request") == token else ""
            print(json.dumps({"request": token, "path": str(paths[-1]), "rendered": rendered and not error,
                              "error": error, "workspace": os.environ["HERDR_WORKSPACE_ID"]}))
            if args.wait and (not rendered or get(db, "error")):
                return 2
        elif args.command == "list":
            print(json.dumps([dict(r) for r in db.execute("SELECT * FROM images ORDER BY id")]))
        elif args.command == "status":
            print(json.dumps(dict(db.execute("SELECT key,value FROM state"))))
        else:
            gallery(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Image Gallery: " + str(exc), file=sys.stderr)
        sys.exit(1)
