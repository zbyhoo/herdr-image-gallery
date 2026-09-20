import hashlib
import os
from pathlib import Path
import tempfile
import struct
import subprocess
import sys
import base64
import fcntl
import io
import json
import pty
import select
import termios
import time
import zlib
from urllib.parse import quote
import unittest
from unittest.mock import patch

import gallery


def make_png(width=2, height=2, color=b"\xff\x00\x00"):
    """A minimal valid truecolor PNG; a different width gives different bytes and a different sha."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = (b"\0" + color * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux image integration")
class LinuxImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "image with spaces.png"
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        self.path.write_bytes(b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1100, 2, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0\x80" * 1100) * 2)) + chunk(b"IEND", b""))

    def test_preview_decodes_png_jpeg_and_first_gif_frame(self):
        for extension in ("png", "jpg", "gif", "webp", "tiff", "bmp"):
            with self.subTest(extension=extension):
                path = self.path.with_suffix("." + extension)
                if extension != "png":
                    subprocess.run([gallery.imagemagick(), str(self.path), str(path)], check=True, capture_output=True)
                data, width, height = gallery.preview(str(path), path.stat().st_mtime_ns, 550, 100)
                self.assertEqual((width, height), (550, 1))
                self.assertEqual(len(zlib.decompress(base64.b64decode(data))), width * height * 3)
        animation = self.path.with_name("animated.gif")
        subprocess.run([gallery.imagemagick(), "-size", "2x2", "xc:red", "xc:blue", str(animation)], check=True)
        rgb, width, height = gallery.image_data(animation, 20)
        self.assertEqual((width, height, rgb), (2, 2, b"\xff\0\0" * 4))

    def test_copy_uses_archived_png_with_full_resolution_and_alpha(self):
        item = {"path": "/deleted/original.png", "cached_path": str(self.path)}
        real_run = subprocess.run
        for environment, executable, expected in (
            ({"WAYLAND_DISPLAY": "wayland-test", "DISPLAY": ":0"}, "/mock/wl-copy", ["--type", "image/png"]),
            ({"WAYLAND_DISPLAY": "", "DISPLAY": ":0"}, "/mock/xclip", ["-selection", "clipboard", "-t", "image/png", "-i"]),
        ):
            copied = []
            def run(command, **kwargs):
                if command[0] == executable:
                    self.assertEqual(command[1:], expected)
                    copied.append(kwargs["input"])
                    return subprocess.CompletedProcess(command, 0, b"", b"")
                return real_run(command, **kwargs)
            real_which = gallery.shutil.which
            with patch.dict(os.environ, environment), patch.object(gallery.subprocess, "run", side_effect=run), \
                 patch.object(gallery.shutil, "which", side_effect=lambda name: executable if name in ("wl-copy", "xclip") else real_which(name)):
                gallery.copy_image(item)
            output = self.path.with_name("copied.png")
            output.write_bytes(copied[0])
            metadata = real_run([gallery.imagemagick(), str(output), "-format", "%w %h %[pixel:p{0,0}]", "info:"],
                                check=True, capture_output=True, text=True).stdout
            self.assertTrue(metadata.startswith("1100 2 srgba(255,0,0,0.50196"), metadata)

    def test_stdin_published_bytes_decode_through_preview(self):
        original = self.path.read_bytes()
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as state:
            with patch.dict(os.environ, {"HERDR_GALLERY_STATE_DIR": state, "HERDR_ENV": "1",
                                         "HERDR_GALLERY_AUTO_SETUP": "0", "HERDR_WORKSPACE_ID": "w1",
                                         "HERDR_SOCKET_PATH": "/tmp/test-herdr.sock"}):
                db = gallery.connect()
                try:
                    data, fmt = gallery.decode_image_bytes(base64.b64encode(original), True)
                    published = gallery.publish_many(db, [{"data": data, "format": fmt, "title": "Stdin"}])[1]
                finally:
                    db.close()
            archived = Path(published[0]["path"])
            self.assertEqual(archived.read_bytes(), original)
            pixels, width, height = gallery.preview(str(archived), archived.stat().st_mtime_ns, 550, 100)
            self.assertEqual((width, height), (550, 1))
            self.assertEqual(len(zlib.decompress(base64.b64decode(pixels))), width * height * 3)

    def test_missing_decoder_reports_dependency(self):
        with patch.object(gallery.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install ImageMagick"):
                gallery.image_data(self.path, 50)

    def test_missing_clipboard_tool_reports_dependency(self):
        real_which = gallery.shutil.which
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-test"}), \
             patch.object(gallery.shutil, "which", side_effect=lambda name: None if name == "wl-copy" else real_which(name)):
            with self.assertRaisesRegex(RuntimeError, "Install wl-clipboard"):
                gallery.copy_image({"path": str(self.path), "cached_path": None})


class GalleryTests(unittest.TestCase):
    def setUp(self):
        # Resolve the temp root (macOS: /var -> /private/var) so it matches the resolved paths gallery stores.
        self.temp = tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir()))
        self.env = patch.dict(os.environ, {"HERDR_GALLERY_STATE_DIR": self.temp.name,
                             "HERDR_GALLERY_AUTO_SETUP": "0", "CLAUDE_CONFIG_DIR": str(Path(self.temp.name) / "claude"),
                             "HERDR_PANE_ID": "", "CODEX_HOME": str(Path(self.temp.name) / "codex"), "HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w1",
                             "HERDR_SOCKET_PATH": "/tmp/test-herdr.sock"})
        self.env.start()
        self.db = gallery.connect()
        gallery.put(self.db, codex_setup_dismissed="1")
        self.path = Path(self.temp.name) / "image with spaces.png"
        self.path.write_bytes(b"test fixture")

    def tearDown(self):
        self.db.close()
        self.env.stop()
        self.temp.cleanup()

    @unittest.skipUnless(sys.platform == "darwin", "macOS pasteboard integration")
    def test_copy_preserves_archived_resolution_and_alpha_on_macos_pasteboard(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1100, 2, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0\x80" * 1100) * 2)) + chunk(b"IEND", b"")
        self.path.write_bytes(png)
        gallery.publish(self.db, self.path)
        item = self.db.execute("SELECT * FROM images").fetchone()
        self.path.unlink()
        # Exercise the real macOS bridge without touching the user's clipboard.
        board = '$.NSPasteboard.pasteboardWithName(' + json.dumps("herdr-gallery-test-" + self.temp.name) + ')'
        script = gallery.COPY_IMAGE_SCRIPT.replace("$.NSPasteboard.generalPasteboard", board)
        try:
            with patch.object(gallery, "COPY_IMAGE_SCRIPT", script):
                gallery.copy_image(item)
            result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", """
                ObjC.import('AppKit');
                const data = BOARD.dataForType($.NSPasteboardTypePNG);
                const rep = $.NSBitmapImageRep.imageRepWithData(data);
                JSON.stringify([Number(rep.pixelsWide), Number(rep.pixelsHigh), !!rep.hasAlpha,
                                Number(rep.colorAtXY(0, 0).alphaComponent)]);
            """.replace("BOARD", board)], capture_output=True, text=True, check=True, timeout=10)
            width, height, alpha, opacity = json.loads(result.stdout)
            self.assertEqual((width, height, alpha), (1100, 2, True))
            self.assertAlmostEqual(opacity, 128 / 255, places=3)
        finally:
            subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e",
                            "ObjC.import('AppKit'); " + board + ".releaseGlobally;"],
                           capture_output=True, timeout=10)

    def test_copy_decode_failure_does_not_write_clipboard(self):
        gallery.publish(self.db, self.path)
        item = self.db.execute("SELECT * FROM images").fetchone()
        real_run = subprocess.run
        with patch.object(gallery.subprocess, "run", wraps=real_run) as run:
            with self.assertRaises(ValueError):
                gallery.copy_image(item)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0],
                         "/usr/bin/sips" if sys.platform == "darwin" else gallery.imagemagick())

    def test_copy_empty_selection_and_failure_report_without_redrawing(self):
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch.object(gallery, "copy_image", side_effect=RuntimeError("clipboard unavailable")) as copy, \
             patch.object(gallery, "transmit") as send, patch("sys.stdout", new=io.StringIO()) as output:
            gallery.copy_selection([], 0)
            copy.assert_not_called()
            self.assertIn("No image selected", output.getvalue())
            gallery.copy_selection([{}], 0)
            self.assertIn("Cannot copy: clipboard unavailable", output.getvalue())
            send.assert_not_called()

    def test_automatic_setup_detects_claude_without_codex(self):
        gallery.agent_skill_paths("claude")[1].parent.parent.mkdir()
        with patch.dict(os.environ, {"HERDR_GALLERY_AUTO_SETUP": "1"}), patch.object(gallery.shutil, "which", return_value=None):
            results = gallery.auto_setup_agents()
        self.assertEqual(set(results), {"claude"})
        self.assertEqual(gallery.agent_skill_status("claude"), "installed")
        self.assertEqual(gallery.agent_skill_status("codex"), "missing")
        helper = gallery.agent_skill_paths("claude")[1] / "scripts" / "gallery.py"
        result = subprocess.run([sys.executable, str(helper), "show", str(self.path), "--no-open"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(gallery.get(self.db, "requested_path"), str(self.path.resolve()))

    def test_auto_setup_preserves_conflict_and_installs_other_agent(self):
        target = gallery.agent_skill_paths("claude")[1]
        target.mkdir(parents=True)
        (target / "custom.txt").write_text("keep")
        results = gallery.setup_agents("both")
        self.assertEqual(results["claude"]["status"], "error")
        self.assertEqual((target / "custom.txt").read_text(), "keep")
        self.assertEqual(gallery.agent_skill_status("codex"), "installed")
        self.assertFalse(gallery.setup_agents("codex")["codex"]["changed"])

    def test_auto_setup_opt_out_and_no_agents_create_nothing(self):
        with patch.object(gallery.shutil, "which", return_value="/bin/fake-agent"):
            self.assertEqual(gallery.auto_setup_agents(), {})
        with patch.dict(os.environ, {"HERDR_GALLERY_AUTO_SETUP": "1"}), patch.object(gallery.shutil, "which", return_value=None):
            self.assertEqual(gallery.auto_setup_agents(), {})
        for agent in gallery.AGENTS:
            self.assertFalse(gallery.agent_skill_paths(agent)[1].exists())

    def test_claude_setup_cli_works_without_herdr_or_codex(self):
        env = dict(os.environ)
        for key in ("HERDR_ENV", "HERDR_WORKSPACE_ID", "HERDR_SOCKET_PATH"):
            env.pop(key, None)
        result = subprocess.run([sys.executable, gallery.__file__, "setup-claude", "--yes"], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(gallery.agent_skill_status("claude"), "installed")
        self.assertEqual(gallery.agent_skill_status("codex"), "missing")

    def test_preview_replacement_prepares_before_touching_previous_layer(self):
        gallery.publish(self.db, self.path)
        items = self.db.execute("SELECT * FROM images").fetchall()
        events = []
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p2"}), \
             patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch.object(gallery, "prepare_preview", side_effect=lambda *a: events.append("ready") or (b"pixels", 100, 60)), \
             patch.object(gallery, "transmit", side_effect=lambda *a, **kw: events.append("replace")), \
             patch.object(gallery, "delete_image", side_effect=lambda **kw: events.append(("cleanup", kw))), \
             patch("sys.stdout", new=io.StringIO()):
            gallery.draw(items, 0, "", False, False, {})
        self.assertEqual(events, ["ready", "replace", ("cleanup", {"keep": ("image-gallery-71031",)})])

    def test_failed_load_preserves_current_layer(self):
        gallery.publish(self.db, self.path)
        items = self.db.execute("SELECT * FROM images").fetchall()
        with patch.object(gallery, "NATIVE_LAYERS", {"image-gallery-71031", "image-gallery-loading"}), \
             patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch.object(gallery, "prepare_preview", side_effect=ValueError("bad image")), \
             patch.object(gallery, "transmit") as send, patch.object(gallery, "delete_image") as clear, \
             patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(gallery.draw(items, 0, "", False, False, {}), "bad image")
            send.assert_not_called()
            clear.assert_called_once_with(keep=("image-gallery-71031",))

    def test_slow_decode_shows_loader_but_cache_hit_does_not(self):
        item = {"path": str(self.path), "cached_path": str(self.path)}
        def slow(*args):
            time.sleep(0.23)
            return b"pixels", 1, 1
        with patch.object(gallery, "preview", side_effect=slow), patch.object(gallery, "loading_indicator") as spinner:
            self.assertEqual(gallery.prepare_preview(item, 99, 28, 8, 16), (b"pixels", 1, 1))
            self.assertGreater(spinner.call_count, 0)
        with patch.object(gallery, "preview", return_value=(b"pixels", 1, 1)), patch.object(gallery, "loading_indicator") as spinner:
            gallery.prepare_preview(item, 99, 28, 8, 16)
            spinner.assert_not_called()

    def test_native_layer_uses_pane_coordinates_and_owned_cleanup(self):
        encoded = base64.b64encode(zlib.compress(b"\xff\0\0"))
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p2"}), patch.object(gallery, "native_frame") as api, patch.object(gallery, "graphics_api") as clear:
            gallery.transmit(encoded, 1, 1, 10, 5, col=8, row=4)
            args = api.call_args.kwargs
            self.assertEqual(args["placement"], {"viewport_col": 8, "viewport_row": 4, "grid_cols": 10, "grid_rows": 5})
            self.assertEqual(api.call_args.args[1], b"\xff\0\0")
            with patch("sys.stdout", new=io.StringIO()):
                gallery.delete_image()
            self.assertEqual(clear.call_args.args, ("clear",))
            self.assertEqual(clear.call_args.kwargs, {"layer_id": "image-gallery-71031"})
            self.assertFalse(gallery.NATIVE_LAYERS)

    def test_resize_refresh_debounces_and_stops_after_two_replays(self):
        refresh = gallery.ResizeRefresh()
        with patch.object(gallery.time, "monotonic", return_value=10) as clock:
            self.assertFalse(refresh.due((99, 28)))
            self.assertFalse(refresh.due((80, 28)))
            clock.return_value = 10.2
            self.assertFalse(refresh.due((79, 28)))
            clock.return_value = 10.4
            self.assertFalse(refresh.due((79, 28)))
            clock.return_value = 10.6
            self.assertTrue(refresh.due((79, 28)))
            self.assertFalse(refresh.due((79, 28)))
            clock.return_value = 11.3
            self.assertTrue(refresh.due((79, 28)))
            clock.return_value = 20
            self.assertFalse(refresh.due((79, 28)))
            refresh.notify()  # SIGWINCH can arrive even with unchanged final dimensions.
            clock.return_value = 21.1
            self.assertTrue(refresh.due((79, 28)))
            self.assertFalse(refresh.due((79, 28)))

    def test_setup_declining_does_not_create_skill(self):
        for key in ("n", "\r", "\x1b", "N"):
            self.assertFalse(gallery.setup_key(key)[0])
            self.assertFalse(gallery.codex_skill_paths()[1].exists())
        self.assertTrue(gallery.setup_key("x")[0])

    def test_setup_explicit_consent_installs_idempotently(self):
        pending, message = gallery.setup_key("y")
        self.assertFalse(pending)
        self.assertIn("Restart Codex", message)
        source, target = gallery.codex_skill_paths()
        self.assertEqual(target.resolve(), source.resolve())
        self.assertTrue(target.is_symlink())
        gallery.install_codex_skill()
        self.assertEqual(gallery.codex_skill_status(), "installed")

    def test_setup_preserves_existing_directory_and_broken_link(self):
        source, target = gallery.codex_skill_paths()
        target.mkdir(parents=True)
        marker = target / "user-file"
        marker.write_text("keep")
        self.assertIn("preserved", gallery.setup_key("y")[1])
        self.assertEqual(marker.read_text(), "keep")
        marker.unlink()
        target.rmdir()
        target.symlink_to(target.parent / "missing")
        self.assertIn("preserved", gallery.setup_key("y")[1])
        self.assertTrue(target.is_symlink())

    def test_setup_cli_requires_consent_without_tty(self):
        result = subprocess.run([sys.executable, gallery.__file__, "setup-codex"],
                                input="", text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(gallery.codex_skill_paths()[1].exists())
        result = subprocess.run([sys.executable, gallery.__file__, "setup-codex", "--yes"],
                                input="", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(gallery.codex_skill_status(), "installed")

    def test_separate_reader_receives_repeated_show_without_duplicate_history(self):
        first = gallery.publish(self.db, self.path, "First")
        reader = gallery.connect()
        self.addCleanup(reader.close)
        self.assertEqual(gallery.get(reader, "request"), first)
        second = gallery.publish(self.db, self.path, "Updated")
        self.assertNotEqual(first, second)
        self.assertEqual(gallery.get(reader, "request"), second)
        self.assertEqual(reader.execute("SELECT count(*) FROM images").fetchone()[0], 1)
        self.assertEqual(reader.execute("SELECT title FROM images").fetchone()[0], "Updated")

    def test_cli_publishes_to_running_reader(self):
        subprocess.run([sys.executable, str(Path(gallery.__file__)), "show", str(self.path),
                        "--title", "From Codex", "--no-open"], check=True, capture_output=True)
        self.assertEqual(gallery.get(self.db, "requested_path"), str(self.path.resolve()))
        self.assertEqual(self.db.execute("SELECT title FROM images").fetchone()[0], "From Codex")

    def test_bmp_decoder_handles_orientation_padding_and_rgb_channels(self):
        # 1x2, 24-bit BGR, padded rows; top should be red, bottom blue.
        header = bytearray(54)
        header[:2] = b"BM"
        struct.pack_into("<I", header, 10, 54)
        for height, pixels in ((2, b"\xff\0\0\0\0\0\xff\0"), (-2, b"\0\0\xff\0\xff\0\0\0")):
            struct.pack_into("<IiiHHI", header, 14, 40, 1, height, 1, 24, 0)
            data, w, h = gallery.decode_bmp(bytes(header) + pixels)
            self.assertEqual((w, h), (1, 2))
            self.assertEqual(data, b"\xff\0\0\0\0\xff")
        with self.assertRaises(ValueError):
            gallery.decode_bmp(bytes(header))

    def test_workspace_and_session_isolation(self):
        gallery.publish(self.db, self.path)
        for change in ({"HERDR_WORKSPACE_ID": "w2"}, {"HERDR_SOCKET_PATH": "/tmp/other.sock"}):
            with patch.dict(os.environ, change):
                other = gallery.connect()
                self.assertEqual(gallery.get(other, "request"), "")
                other.close()

    def test_macos_32bit_bmp_bitfields_screenshot(self):
        header = bytearray(138)
        header[:2] = b"BM"
        struct.pack_into("<I", header, 10, 138)
        struct.pack_into("<IiiHHI", header, 14, 124, 2, -1, 1, 32, 3)
        struct.pack_into("<IIII", header, 54, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
        data, w, h = gallery.decode_bmp(bytes(header) + b"\x03\x02\x01\xff\x06\x05\x04\xff")
        self.assertEqual((data, w, h), (b"\x01\x02\x03\x04\x05\x06", 2, 1))

    def test_existing_slow_gallery_is_reused_without_any_layout_operation(self):
        gallery.put(self.db, pane="w1:p7", heartbeat=0)
        with patch.object(gallery, "herdr", return_value={"result": {"pane": {"label": "Image Gallery"}}}) as cli:
            self.assertEqual(gallery.open_pane(self.db), {"existing_pane": "w1:p7"})
            cli.assert_called_once_with("pane", "get", "w1:p7")

    def test_connection_error_does_not_create_another_gallery(self):
        gallery.put(self.db, pane="w1:p7", heartbeat=0)
        with patch.object(gallery, "herdr", side_effect=RuntimeError("Permission denied")) as cli:
            with self.assertRaises(RuntimeError):
                gallery.open_pane(self.db)
            cli.assert_called_once_with("pane", "get", "w1:p7")

    def test_image_survives_original_deletion_and_reconnection(self):
        gallery.publish(self.db, self.path, "Keep me")
        self.path.unlink()
        reopened = gallery.connect()
        self.addCleanup(reopened.close)
        item = reopened.execute("SELECT * FROM images").fetchone()
        self.assertEqual(gallery.display_path(item).read_bytes(), b"test fixture")
        self.assertEqual(item["title"], "Keep me")

    def test_content_archive_deduplicates_equal_files(self):
        gallery.publish(self.db, self.path)
        other = self.path.with_name("other.png")
        other.write_bytes(self.path.read_bytes())
        gallery.publish(self.db, other)
        copies = [r[0] for r in self.db.execute("SELECT cached_path FROM images")]
        self.assertEqual(copies[0], copies[1])

    def test_grid_pages_keep_selection_visible(self):
        for cols, rows in ((30, 10), (99, 28), (198, 59)):
            for index in range(65):
                columns, grid_rows, size, start = gallery.grid_geometry(cols, rows, 65, index)
                self.assertLessEqual(size, 24)
                self.assertGreaterEqual(index, start)
                self.assertLess(index, start + size)
                self.assertEqual(size, columns * grid_rows)

    def test_local_image_links_decode_spaces_and_reject_network_paths(self):
        self.assertEqual(gallery.link_path(self.path.as_uri()), self.path.resolve())
        self.assertEqual(gallery.link_path("herdr-image://open?path=" + quote(str(self.path))), self.path.resolve())
        for url in ("https://example.com/image.png", "file://server/share/image.png", "herdr-image://open?path=relative.png"):
            with self.assertRaises(ValueError):
                gallery.link_path(url)

    def test_click_preserves_existing_art_direction(self):
        gallery.publish(self.db, self.path, "Noir", "Concrete after rain")
        gallery.publish(self.db, self.path)
        item = self.db.execute("SELECT * FROM images").fetchone()
        self.assertEqual((item["title"], item["caption"]), ("Noir", "Concrete after rain"))

    def test_moving_grid_selection_does_not_retransmit_thumbnails(self):
        gallery.publish(self.db, self.path)
        other = self.path.with_name("other.png")
        other.write_bytes(self.path.read_bytes())
        gallery.publish(self.db, other)
        items = self.db.execute("SELECT * FROM images ORDER BY id").fetchall()
        cache = {}
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch.object(gallery, "preview", return_value=(b"pixels", 100, 60)), \
             patch.object(gallery, "transmit") as send, patch("sys.stdout", new=io.StringIO()):
            gallery.draw(items, 0, "", False, False, cache, "grid")
            self.assertEqual(send.call_count, 2)
            send.reset_mock()
            gallery.draw(items, 1, "", False, False, cache, "grid")
            send.assert_not_called()

    def test_tui_grid_selection_and_session_restore_with_deleted_original(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\0\xff\0" * 2)) + chunk(b"IEND", b"")
        self.path.write_bytes(png)
        other = self.path.with_name("second.png")
        other.write_bytes(png)
        gallery.publish(self.db, self.path, "First")
        gallery.publish(self.db, other, "Second")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 99, 792, 448))
        env = dict(os.environ)
        env.pop("HERDR_GALLERY_RESUME", None)
        copied = Path(self.temp.name) / "copied.jsonl"
        bootstrap = """
import json, gallery
def copy_image(item):
    with open(%r, 'a') as log:
        log.write(json.dumps(dict(item)) + '\\n')
gallery.copy_image = copy_image
gallery.gallery(gallery.connect())
""" % str(copied)

        def start():
            return subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave, env=env)

        output = bytearray()

        def wait_for(proc, predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                if predicate():
                    return
                if proc.poll() is not None:
                    self.fail("Viewer exited unexpectedly: %s" % proc.returncode)
            self.fail("Viewer state timed out")

        def state():
            return json.loads(gallery.get(self.db, "view_state", "{}"))

        gallery.put(self.db, codex_setup_dismissed="")
        proc = start()
        try:
            wait_for(proc, lambda: state().get("path") == str(other.resolve()))
            os.write(master, b"\x1b")
            wait_for(proc, lambda: gallery.get(self.db, "codex_setup_dismissed") == "1")
            self.assertFalse(gallery.codex_skill_paths()[1].exists())
            self.assertIsNone(proc.poll())
            os.write(master, b"sy")
            wait_for(proc, lambda: gallery.codex_skill_status() == "installed")
            before = output.count(b"a=T,f=24")
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 85, 680, 448))
            wait_for(proc, lambda: output.count(b"a=T,f=24") >= before + 3)
            self.assertEqual(state().get("path"), str(other.resolve()))
            os.write(master, b"c")
            wait_for(proc, lambda: b"Image copied to clipboard." in output)
            self.assertEqual(json.loads(copied.read_text())["path"], str(other.resolve()))
            os.write(master, b"\t")
            wait_for(proc, lambda: state().get("mode") == "grid")
            os.write(master, b"c")
            wait_for(proc, lambda: len(copied.read_text().splitlines()) == 2)
            os.write(master, b"/c")
            wait_for(proc, lambda: state().get("query") == "c")
            self.assertEqual(len(copied.read_text().splitlines()), 2)
            os.write(master, b"\x7f\r")
            wait_for(proc, lambda: state().get("query") == "")
            os.write(master, b"\x1b[C\r")
            wait_for(proc, lambda: state().get("mode") == "preview" and state().get("path") == str(self.path.resolve()))
            os.write(master, b"\x1b")
            wait_for(proc, lambda: state().get("mode") == "grid")
            os.write(master, b"\x1b\t")
            wait_for(proc, lambda: state().get("mode") == "preview")
            self.assertIsNone(proc.poll(), "Escape must not close the gallery")
            os.write(master, b"q")
            wait_for(proc, lambda: proc.poll() is not None)
            self.assertEqual(proc.returncode, 0)
            self.path.unlink()
            put_marker = "not drawn yet"
            gallery.put(self.db, error=put_marker)
            proc = start()
            wait_for(proc, lambda: gallery.get(self.db, "error") != put_marker)
            self.assertEqual(state()["path"], str(self.path.resolve()))
            self.assertEqual(gallery.get(self.db, "error"), "")
            os.write(master, b"q")
            wait_for(proc, lambda: proc.poll() is not None)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    wait_for(proc, lambda: proc.poll() is not None)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_scan_directory_filters_sorts_caps_and_recursion(self):
        root = Path(self.temp.name) / "album"
        nested = root / "nested"
        hidden = root / ".secret"
        nested.mkdir(parents=True)
        hidden.mkdir()
        (root / "b.png").write_bytes(b"b")
        (root / "A.PNG").write_bytes(b"A")
        (root / "notes.txt").write_bytes(b"no")
        (root / ".dot.png").write_bytes(b"dot")
        (nested / "z.png").write_bytes(b"z")
        (hidden / "x.png").write_bytes(b"x")
        (root / "link.jpg").symlink_to(root / "b.png")
        huge = root / "huge.png"
        huge.write_bytes(b"h")
        os.truncate(huge, 64 * 1024 * 1024 + 1)

        with self.assertRaises(FileNotFoundError):
            gallery.scan_directory(root / "missing")
        with self.assertRaises(NotADirectoryError):
            gallery.scan_directory(self.path)

        items = gallery.scan_directory(root)
        self.assertEqual([i["title"] for i in items], ["A.PNG", "b.png", "link.jpg"])
        self.assertTrue(all(i["cached_path"] is None and i["caption"] == "" for i in items))
        self.assertEqual(items[0]["path"], str(root / "A.PNG"))
        self.assertEqual(gallery.scan_directory(root, recursive=True)[-1]["title"], "nested/z.png")
        self.assertEqual([i["title"] for i in gallery.scan_directory(root, recursive=True, limit=2)],
                         ["A.PNG", "b.png"])

    def test_scan_directory_newest_sort_keeps_the_newest_entries_under_the_cap(self):
        root = Path(self.temp.name) / "byage"
        root.mkdir()
        now = time.time()
        # Alphabetical order is a,b,c,d; mtime order (newest first) is d,c,b,a - the reverse.
        names = ["a.png", "b.png", "c.png", "d.png"]
        for offset, name in enumerate(reversed(names)):
            path = root / name
            path.write_bytes(b"x")
            os.utime(path, (now - offset, now - offset))
        newest_first = gallery.scan_directory(root, sort="newest")
        self.assertEqual([i["title"] for i in newest_first], ["d.png", "c.png", "b.png", "a.png"])
        capped = gallery.scan_directory(root, sort="newest", limit=2)
        self.assertEqual([i["title"] for i in capped], ["d.png", "c.png"])
        name_sorted = gallery.scan_directory(root, sort="name", limit=2)
        self.assertEqual([i["title"] for i in name_sorted], ["a.png", "b.png"])

    def test_cli_browse_writes_state_refuses_file_and_skips_open(self):
        album = Path(self.temp.name) / "album"
        album.mkdir()
        (album / "shot.png").write_bytes(b"png")
        with patch.object(gallery, "open_pane") as opener, patch("sys.stdout", new=io.StringIO()) as out:
            with patch.object(sys, "argv", [gallery.__file__, "browse", str(album), "--no-open"]):
                self.assertEqual(gallery.main(), 0)
            opener.assert_not_called()
            payload = json.loads(out.getvalue())
        self.assertEqual(payload["directory"], str(album.resolve()))
        self.assertFalse(payload["recursive"])
        self.assertEqual(gallery.get(self.db, "browse_dir"), str(album.resolve()))
        self.assertTrue(gallery.get(self.db, "browse_request"))
        self.assertEqual(gallery.get(self.db, "browse_recursive"), "")

        with patch.object(gallery, "open_pane") as opener, patch("sys.stdout", new=io.StringIO()):
            with patch.object(sys, "argv", [gallery.__file__, "browse", str(album), "--recursive", "--no-open"]):
                self.assertEqual(gallery.main(), 0)
            opener.assert_not_called()
        self.assertEqual(gallery.get(self.db, "browse_recursive"), "1")

        result = subprocess.run([sys.executable, gallery.__file__, "browse", str(self.path), "--no-open"],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Not a directory", result.stderr)

    def test_tui_directory_browse_live_hold_and_copy(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\0\xff\0" * 2)) + chunk(b"IEND", b"")
        album = Path(self.temp.name) / "album"
        nested = album / "nested"
        nested.mkdir(parents=True)
        first = album / "a.png"
        later = album / "b.png"
        nested_image = nested / "z.png"
        first.write_bytes(png)
        later.write_bytes(png)
        nested_image.write_bytes(png)
        live = Path(self.temp.name) / "live.png"
        held = Path(self.temp.name) / "held.png"
        live.write_bytes(png)
        held.write_bytes(png)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 99, 792, 448))
        env = dict(os.environ)
        env.pop("HERDR_GALLERY_RESUME", None)
        copied = Path(self.temp.name) / "copied.jsonl"
        bootstrap = """
import json, gallery
def copy_image(item):
    with open(%r, 'a') as log:
        log.write(json.dumps(dict(item)) + '\\n')
gallery.copy_image = copy_image
gallery.gallery(gallery.connect())
""" % str(copied)

        def start():
            return subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave, env=env)

        output = bytearray()

        def wait_for(proc, predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                if predicate():
                    return
                if proc.poll() is not None:
                    self.fail("Viewer exited unexpectedly: %s" % proc.returncode)
            self.fail("Viewer state timed out")

        def state():
            return json.loads(gallery.get(self.db, "view_state", "{}"))

        proc = start()
        try:
            wait_for(proc, lambda: gallery.get(self.db, "heartbeat") not in ("", "0"))
            os.write(master, b"d")
            wait_for(proc, lambda: state().get("browsing") is True)
            os.write(master, b"\x1b")
            wait_for(proc, lambda: state().get("browsing") is False)
            self.assertEqual(state().get("source") or "", "")
            self.assertIsNone(proc.poll(), "Escape must not close the gallery")
            os.write(master, b"d" + str(album).encode() + b"\r")
            wait_for(proc, lambda: state().get("source") == str(album.resolve())
                     and state().get("path") == str(first))
            self.assertFalse(state().get("recursive"))
            os.write(master, b"r")
            wait_for(proc, lambda: state().get("recursive") is True and state().get("scanning") is False)
            os.write(master, b"c")
            wait_for(proc, lambda: copied.is_file() and copied.read_text().strip())
            self.assertEqual(json.loads(copied.read_text().splitlines()[0])["path"], str(first))
            gallery.publish(self.db, live, "Live")
            wait_for(proc, lambda: state().get("source") == "" and state().get("path") == str(live.resolve()))
            os.write(master, b"d\r")
            wait_for(proc, lambda: state().get("source") == str(album.resolve()))
            os.write(master, b"a")
            wait_for(proc, lambda: state().get("paused") is True)
            gallery.publish(self.db, held, "Held")
            beat = float(gallery.get(self.db, "heartbeat") or 0)
            wait_for(proc, lambda: float(gallery.get(self.db, "heartbeat") or 0) != beat)
            self.assertEqual(state().get("source"), str(album.resolve()))
            os.write(master, b"q")
            wait_for(proc, lambda: proc.poll() is not None)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    wait_for(proc, lambda: proc.poll() is not None)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_directory_scan_runs_in_background_without_blocking_input(self):
        album = Path(self.temp.name) / "slowalbum"
        album.mkdir()
        (album / "a.png").write_bytes(b"a")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 99, 792, 448))
        env = dict(os.environ)
        env.pop("HERDR_GALLERY_RESUME", None)
        bootstrap = """
import time, gallery
_real_scan_directory = gallery.scan_directory
def slow_scan_directory(*args, **kwargs):
    time.sleep(1.0)
    return _real_scan_directory(*args, **kwargs)
gallery.scan_directory = slow_scan_directory
gallery.gallery(gallery.connect())
"""

        def start():
            return subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave, env=env)

        output = bytearray()

        def wait_for(proc, predicate, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                if predicate():
                    return
                if proc.poll() is not None:
                    self.fail("Viewer exited unexpectedly: %s" % proc.returncode)
            self.fail("Viewer state timed out")

        def state():
            return json.loads(gallery.get(self.db, "view_state", "{}"))

        proc = start()
        try:
            wait_for(proc, lambda: gallery.get(self.db, "heartbeat") not in ("", "0"))
            os.write(master, b"d" + str(album).encode() + b"\r")
            wait_for(proc, lambda: state().get("source") == str(album.resolve()) and state().get("scanning") is True)
            started = time.monotonic()
            os.write(master, b"a")
            wait_for(proc, lambda: state().get("paused") is True, timeout=0.6)
            self.assertLess(time.monotonic() - started, 0.9,
                             "key handling must not wait on the slow background scan")
            wait_for(proc, lambda: state().get("scanning") is False)
            os.write(master, b"q")
            wait_for(proc, lambda: proc.poll() is not None)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    wait_for(proc, lambda: proc.poll() is not None)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_stale_scan_results_are_dropped_after_source_change(self):
        slow_dir = Path(self.temp.name) / "slow"
        fast_dir = Path(self.temp.name) / "fast"
        slow_dir.mkdir()
        fast_dir.mkdir()
        (slow_dir / "a.png").write_bytes(b"a")
        fast_image = fast_dir / "b.png"
        fast_image.write_bytes(b"b")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 99, 792, 448))
        env = dict(os.environ)
        env.pop("HERDR_GALLERY_RESUME", None)
        bootstrap = """
import time, gallery
_real_scan_directory = gallery.scan_directory
def delayed_scan_directory(directory, *args, **kwargs):
    if str(directory).endswith("slow"):
        time.sleep(0.6)
    return _real_scan_directory(directory, *args, **kwargs)
gallery.scan_directory = delayed_scan_directory
gallery.gallery(gallery.connect())
"""

        def start():
            return subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave, env=env)

        output = bytearray()

        def wait_for(proc, predicate, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                if predicate():
                    return
                if proc.poll() is not None:
                    self.fail("Viewer exited unexpectedly: %s" % proc.returncode)
            self.fail("Viewer state timed out")

        def state():
            return json.loads(gallery.get(self.db, "view_state", "{}"))

        proc = start()
        try:
            wait_for(proc, lambda: gallery.get(self.db, "heartbeat") not in ("", "0"))
            os.write(master, b"d" + str(slow_dir).encode() + b"\r")
            wait_for(proc, lambda: state().get("source") == str(slow_dir.resolve()) and state().get("scanning") is True)
            clear = b"\x7f" * len(str(slow_dir.resolve()))
            os.write(master, b"d" + clear + str(fast_dir).encode() + b"\r")
            wait_for(proc, lambda: state().get("source") == str(fast_dir.resolve())
                     and state().get("path") == str(fast_image.resolve()))
            # The slow scan for the old source finishes well after this point; its result
            # must never overwrite the directory the user has since switched to.
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                self.assertEqual(state().get("source"), str(fast_dir.resolve()))
                self.assertEqual(state().get("path"), str(fast_image.resolve()))
            os.write(master, b"q")
            wait_for(proc, lambda: proc.poll() is not None)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    wait_for(proc, lambda: proc.poll() is not None)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_limited_indicator_appears_in_footer_when_scan_is_capped(self):
        items = [{"path": str(self.path), "title": "x.png", "caption": "", "updated": 0, "cached_path": None}]
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch("sys.stdout", new=io.StringIO()) as out:
            gallery.draw(items, 0, "", False, False, {}, mode="grid", source="/some/big/dir",
                         recursive=True, scanning=False, limited=True)
            self.assertIn("limited", out.getvalue())
            self.assertIn("recursive", out.getvalue())
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch("sys.stdout", new=io.StringIO()) as out:
            gallery.draw(items, 0, "", False, False, {}, mode="grid", source="/some/big/dir",
                         recursive=True, scanning=False, limited=False)
            self.assertNotIn("limited", out.getvalue())
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch("sys.stdout", new=io.StringIO()) as out:
            gallery.draw([], 0, "", False, False, {}, mode="preview", source="/some/big/dir",
                         recursive=False, scanning=True, limited=False)
            self.assertIn("scanning", out.getvalue())
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch("sys.stdout", new=io.StringIO()) as out:
            gallery.draw(items, 0, "", False, False, {}, mode="grid", source="/some/big/dir",
                         recursive=False, scanning=False, limited=False, sort="newest")
            self.assertIn("newest", out.getvalue())
        with patch.object(gallery, "terminal_size", return_value=(99, 28, 8, 16)), \
             patch("sys.stdout", new=io.StringIO()) as out:
            gallery.draw(items, 0, "", False, False, {}, mode="grid", source="/some/big/dir",
                         recursive=False, scanning=False, limited=False, sort="name")
            self.assertNotIn("newest", out.getvalue())

    def test_tui_directory_sort_toggle_switches_order_and_persists(self):
        album = Path(self.temp.name) / "sortalbum"
        album.mkdir()
        now = time.time()
        older = album / "a.png"
        newer = album / "z.png"
        older.write_bytes(b"a")
        newer.write_bytes(b"z")
        os.utime(older, (now - 100, now - 100))
        os.utime(newer, (now, now))
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 99, 792, 448))
        env = dict(os.environ)
        env.pop("HERDR_GALLERY_RESUME", None)
        bootstrap = "import gallery\ngallery.gallery(gallery.connect())\n"

        def start():
            return subprocess.Popen([sys.executable, "-c", bootstrap], stdin=slave, stdout=slave, stderr=slave, env=env)

        output = bytearray()

        def wait_for(proc, predicate, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    output.extend(os.read(master, 65536))
                if predicate():
                    return
                if proc.poll() is not None:
                    self.fail("Viewer exited unexpectedly: %s" % proc.returncode)
            self.fail("Viewer state timed out")

        def state():
            return json.loads(gallery.get(self.db, "view_state", "{}"))

        proc = start()
        try:
            wait_for(proc, lambda: gallery.get(self.db, "heartbeat") not in ("", "0"))
            os.write(master, b"d" + str(album).encode() + b"\r")
            wait_for(proc, lambda: state().get("source") == str(album.resolve())
                     and state().get("path") == str(older.resolve()))
            self.assertEqual(state().get("sort") or "name", "name")
            os.write(master, b"o")
            # The current selection is preserved by path across a re-sort; only ordering changes.
            wait_for(proc, lambda: state().get("sort") == "newest")
            self.assertEqual(state().get("path"), str(older.resolve()))
            os.write(master, b"o")
            wait_for(proc, lambda: state().get("sort") == "name")
            self.assertEqual(state().get("path"), str(older.resolve()))
            os.write(master, b"q")
            wait_for(proc, lambda: proc.poll() is not None)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    wait_for(proc, lambda: proc.poll() is not None)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_scan_directory_stops_within_time_and_entry_budget_and_reports_limited(self):
        root = Path(self.temp.name) / "huge"
        root.mkdir()
        for i in range(50):
            (root / ("%03d.png" % i)).write_bytes(b"x")
        result = gallery.scan_directory(root, entry_budget=10, time_budget=5)
        self.assertTrue(result.limited)
        self.assertLessEqual(len(result), 10)

        unlimited = gallery.scan_directory(root)
        self.assertFalse(unlimited.limited)
        self.assertEqual(len(unlimited), 50)

    def test_missing_image_does_not_replace_selection(self):
        token = gallery.publish(self.db, self.path)
        with self.assertRaises(FileNotFoundError):
            gallery.publish(self.db, self.path.with_name("missing.png"))
        self.assertEqual(gallery.get(self.db, "request"), token)

    def test_geometry_preserves_ratio_and_stays_inside_pane(self):
        for w, h in ((1920, 1080), (1080, 1920), (4000, 100), (100, 4000)):
            c, r = gallery.fit(w, h, 90, 25)
            self.assertLessEqual(c, 90)
            self.assertLessEqual(r, 25)
            self.assertGreaterEqual(c, 1)
            self.assertGreaterEqual(r, 1)

    def test_terminal_text_cannot_inject_escape_sequences(self):
        self.assertNotIn("\x1b", gallery.clean("title\x1b[2J\nnew"))


class StdinPublishTests(unittest.TestCase):
    """Publishing image BYTES: `show -`, --stdin-base64, --stdin-json, dedup, limits, sniffing."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"HERDR_GALLERY_STATE_DIR": self.temp.name,
                              "HERDR_GALLERY_AUTO_SETUP": "0", "CLAUDE_CONFIG_DIR": str(Path(self.temp.name) / "claude"),
                              "HERDR_PANE_ID": "", "CODEX_HOME": str(Path(self.temp.name) / "codex"), "HERDR_ENV": "1",
                              "HERDR_WORKSPACE_ID": "w1", "HERDR_SOCKET_PATH": "/tmp/test-herdr.sock"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = gallery.connect()
        self.addCleanup(self.db.close)
        self.png = make_png()
        self.archive = Path(self.temp.name) / "images"

    def cli(self, argv, stdin=b"", tty=False):
        """Run main() in-process with bytes on stdin; returns (exit code, stdout JSON or raw, stderr)."""
        class FakeStdin(io.TextIOWrapper):
            def isatty(self):
                return tty
        source = FakeStdin(io.BytesIO(stdin))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [gallery.__file__] + argv), patch.object(sys, "stdin", source), \
             patch("sys.stdout", new=out), patch("sys.stderr", new=err), patch.object(gallery, "open_pane"):
            try:
                code = gallery.main()
            except SystemExit as exc:
                code = exc.code
        text = out.getvalue()
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        return code, payload, err.getvalue()

    def rows(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM images ORDER BY id")]

    def archived(self):
        return sorted(path.name for path in self.archive.iterdir()) if self.archive.is_dir() else []

    def test_base64_stdin_publishes_without_touching_the_caller_directory(self):
        # The acceptance criterion, through the helper an agent actually runs, in a real process.
        helper = Path(gallery.__file__).resolve().parent / "skills" / "herdr-image-gallery" / "scripts" / "gallery.py"
        workdir = Path(self.temp.name) / "agent-cwd"
        workdir.mkdir()
        for label, payload in (("plain", base64.b64encode(self.png)),
                               ("wrapped", base64.encodebytes(self.png)),
                               ("data-url", b"data:image/png;base64," + base64.b64encode(self.png))):
            with self.subTest(payload=label):
                result = subprocess.run([sys.executable, str(helper), "show", "-", "--stdin-base64",
                                         "--title", "Axe small", "--caption", "Rotation, top view", "--no-open"],
                                        input=payload, capture_output=True, cwd=str(workdir),
                                        env=dict(os.environ), timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                published = json.loads(result.stdout)["images"][0]
                self.assertEqual(published["sha256"], hashlib.sha256(self.png).hexdigest())
                self.assertEqual(published["title"], "Axe small")
                self.assertEqual(published["format"], "png")
                self.assertEqual(published["source"], "stdin")
                self.assertEqual(Path(published["path"]).read_bytes(), self.png)
        self.assertEqual(self.archived(), [hashlib.sha256(self.png).hexdigest() + ".png"])
        self.assertEqual(sorted(p.name for p in workdir.iterdir()), [])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["title"], rows[0]["caption"]), ("Axe small", "Rotation, top view"))
        self.assertEqual(rows[0]["path"], rows[0]["cached_path"])

    def test_raw_and_base64_stdin_are_detected_without_the_flag(self):
        code, payload, _ = self.cli(["show", "-", "--no-open"], self.png)
        self.assertEqual(code, 0)
        self.assertEqual(payload["images"][0]["format"], "png")
        # A forgotten --stdin-base64 still works: real image bytes are never base64 of an image.
        code, wrapped, _ = self.cli(["show", "-", "--no-open"], base64.b64encode(make_png(3)))
        self.assertEqual(code, 0)
        self.assertEqual(wrapped["images"][0]["sha256"], hashlib.sha256(make_png(3)).hexdigest())
        with self.assertRaisesRegex(ValueError, "not valid base64"):
            self.cli(["show", "-", "--stdin-base64", "--no-open"], b"not!base64!!")
        self.assertEqual(len(self.rows()), 2)

    def test_default_title_names_the_digest_and_format_mismatch_only_warns(self):
        code, payload, err = self.cli(["show", "-", "--format", "image/jpeg", "--no-open"], self.png)
        self.assertEqual(code, 0)
        digest = hashlib.sha256(self.png).hexdigest()
        self.assertEqual(payload["images"][0]["title"], "image-" + digest[:12])
        self.assertIn("declares image/jpeg but the bytes are png", err)
        # An unrecognised manifest mimeType is just another wrong label: warn, do not refuse.
        code, manifest, err = self.cli(["show", "-", "--stdin-json", "--no-open"],
                                       json.dumps([{"data": base64.b64encode(make_png(3)).decode(),
                                                    "mimeType": "image/svg+xml"}]).encode())
        self.assertEqual(code, 0)
        self.assertEqual(manifest["images"][0]["format"], "png")
        self.assertIn("--stdin-json[0] declares image/svg+xml but the bytes are png", err)
        self.assertTrue(payload["path"].endswith(digest + ".png"), payload["path"])

    def test_json_manifest_publishes_many_images_in_one_call(self):
        first, second = make_png(2), make_png(3)
        entries = [{"type": "image", "data": base64.b64encode(first).decode(), "mimeType": "image/png",
                    "title": "Front", "caption": "front view"},
                   {"data": base64.b64encode(second).decode(), "title": "Side", "caption": "side view"}]
        path_entry = Path(self.temp.name) / "on-disk.png"
        path_entry.write_bytes(make_png(4))
        entries.append({"path": str(path_entry), "title": "From file"})
        for label, payload in (("array", json.dumps(entries).encode()),
                               ("json-lines", b"\n".join(json.dumps(e).encode() for e in entries))):
            with self.subTest(form=label):
                self.db.execute("DELETE FROM images")
                self.db.commit()
                code, result, _ = self.cli(["show", "-", "--stdin-json", "--no-open"], payload)
                self.assertEqual(code, 0)
                self.assertEqual([image["title"] for image in result["images"]], ["Front", "Side", "From file"])
                self.assertEqual([image["source"] for image in result["images"]], ["stdin", "stdin", "path"])
                self.assertEqual([row["caption"] for row in self.rows()], ["front view", "side view", ""])
                self.assertEqual(result["path"], result["images"][-1]["path"])
                self.assertEqual(gallery.get(self.db, "requested_path"), result["path"])
        # A single CLI --title is the default only for entries that bring none of their own.
        code, result, _ = self.cli(["show", "-", "--stdin-json", "--title", "Batch", "--no-open"],
                                   json.dumps([{"data": base64.b64encode(make_png(5)).decode()},
                                               {"data": base64.b64encode(make_png(6)).decode(), "title": "Own"}]).encode())
        self.assertEqual([image["title"] for image in result["images"]], ["Batch", "Own"])

    def test_one_bad_manifest_entry_names_its_index_and_publishes_nothing(self):
        good = {"data": base64.b64encode(self.png).decode(), "title": "Good"}
        for label, entry in (("not an image", {"data": base64.b64encode(b"plain text").decode()}),
                             ("both keys", {"data": "x", "path": "/tmp/x.png"}),
                             ("neither key", {"title": "empty"}),
                             ("missing file", {"path": str(Path(self.temp.name) / "absent.png")}),
                             ("not an object", "just a string")):
            with self.subTest(entry=label):
                result = subprocess.run([sys.executable, gallery.__file__, "show", "-", "--stdin-json", "--no-open"],
                                        input=json.dumps([good, entry]).encode(), capture_output=True,
                                        env=dict(os.environ), timeout=60)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"--stdin-json[1]", result.stderr)
                self.assertEqual(self.rows(), [])
                self.assertEqual(self.archived(), [])
        broken = subprocess.run([sys.executable, gallery.__file__, "show", "-", "--stdin-json", "--no-open"],
                                input=b'{"data": "', capture_output=True, env=dict(os.environ), timeout=60)
        self.assertEqual(broken.returncode, 1)
        self.assertIn(b"neither a JSON document nor JSON Lines", broken.stderr)

    def test_identical_bytes_publish_once_and_keep_earlier_text(self):
        self.cli(["show", "-", "--title", "First", "--caption", "Only caption", "--no-open"], self.png)
        self.cli(["show", "-", "--title", "Second", "--no-open"], self.png)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["title"], rows[0]["caption"]), ("Second", "Only caption"))
        self.assertEqual(self.archived(), [hashlib.sha256(self.png).hexdigest() + ".png"])
        self.cli(["show", "-", "--no-open"], self.png)
        self.assertEqual([row["title"] for row in self.rows()], ["Second"])

    def test_oversized_payloads_are_refused_without_writing_anything(self):
        # Limits are module constants so the real message formatting is exercised at MiB scale
        # without allocating the production 64/256 MiB. Trailing bytes after IEND still sniff as PNG.
        oversized = self.png + b"\0" * (2 * 1024 * 1024)
        with patch.object(gallery, "MAX_IMAGE_BYTES", 1024 * 1024):
            with self.assertRaisesRegex(ValueError, r"stdin exceeds 1 MiB \(got 2\.0 MiB\)"):
                self.cli(["show", "-", "--no-open"], oversized)
        with patch.object(gallery, "MAX_STDIN_BYTES", 1024 * 1024):
            with self.assertRaisesRegex(ValueError, "stdin payload exceeds 1 MiB"):
                self.cli(["show", "-", "--no-open"], oversized)
        # Refused, not truncated: no row, no archive file, not even a partial one.
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.archived(), [])
        with patch.object(gallery, "MAX_IMAGE_BYTES", 1024 * 1024):
            with self.assertRaisesRegex(ValueError, r"--stdin-json\[0\] exceeds 1 MiB"):
                self.cli(["show", "-", "--stdin-json", "--no-open"],
                         json.dumps([{"data": base64.b64encode(oversized).decode()}]).encode())
        self.assertEqual(self.archived(), [])

    def test_magic_bytes_decide_the_format(self):
        ftyp = lambda major, *compatible: (struct.pack(">I", 16 + 4 * len(compatible)) + b"ftyp" + major +
                                           b"\x00\x00\x00\x00" + b"".join(compatible))
        supported = {
            "png": self.png,
            "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF\x00",
            "gif": b"GIF89a\x02\x00\x02\x00",
            "webp": b"RIFF" + struct.pack("<I", 100) + b"WEBPVP8 ",
            "tiff": b"II*\x00\x08\x00\x00\x00",
            "bmp": b"BM" + struct.pack("<IHHI", 122, 0, 0, 54) + struct.pack("<I", 40),
            "heic": ftyp(b"heic"),
        }
        for name, data in supported.items():
            with self.subTest(format=name):
                self.assertEqual(gallery.sniff_format(data), name)
        self.assertEqual(gallery.sniff_format(b"GIF87a\x02\x00"), "gif")
        self.assertEqual(gallery.sniff_format(b"MM\x00*\x00\x00\x00\x08"), "tiff")
        self.assertEqual(gallery.sniff_format(ftyp(b"mif1", b"mif1", b"heic")), "heic")
        for name, data in (("avif", ftyp(b"avif", b"avif", b"mif1")),
                           ("heif without a heic brand", ftyp(b"mif1", b"mif1", b"miaf")),
                           ("text", b"hello world, not an image"),
                           ("pdf", b"%PDF-1.7\n1 0 obj"),
                           ("svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
                           ("bmp-like text", b"BM this is just prose about bitmaps"),
                           ("riff but not webp", b"RIFF" + struct.pack("<I", 100) + b"AVI LIST"),
                           ("empty", b"")):
            with self.subTest(rejected=name):
                self.assertIsNone(gallery.sniff_format(data))
        for rejected in (b"hello world, not an image", b"%PDF-1.7\n1 0 obj",
                         b'<svg xmlns="http://www.w3.org/2000/svg"/>'):
            with self.assertRaisesRegex(ValueError, "not a supported image"):
                self.cli(["show", "-", "--no-open"], rejected)
        self.assertEqual(self.rows(), [])

    def test_stdin_argument_errors_are_explicit(self):
        sample = Path(self.temp.name) / "file.png"
        sample.write_bytes(self.png)
        code, _, err = self.cli(["show", "-", str(sample), "--no-open"], self.png)
        self.assertEqual(code, 2)
        self.assertIn("cannot be mixed with file paths", err)
        code, _, err = self.cli(["show", str(sample), "--stdin-base64", "--no-open"], self.png)
        self.assertEqual(code, 2)
        self.assertIn("pass `-` instead of a path", err)
        code, _, err = self.cli(["show", "-", "--stdin-json", "--title", "a", "--title", "b", "--no-open"], b"[]")
        self.assertEqual(code, 2)
        self.assertIn("at most once with --stdin-json", err)
        code, _, err = self.cli(["show", "--no-open"], b"")
        self.assertEqual(code, 2)
        self.assertIn("`-` to read image bytes from stdin", err)
        with self.assertRaisesRegex(ValueError, "No image data on stdin"):
            self.cli(["show", "-", "--no-open"], b"", tty=True)
        with self.assertRaisesRegex(ValueError, "No image data on stdin"):
            self.cli(["show", "-", "--no-open"], b"")
        # fd 0 closed: sys.stdin is None, and that must still be the clean message.
        with patch.object(sys, "stdin", None), patch.object(sys, "argv", [gallery.__file__, "show", "-", "--no-open"]):
            with self.assertRaisesRegex(ValueError, "No image data on stdin"):
                gallery.main()
        result = subprocess.run([sys.executable, gallery.__file__, "show", "-", "--format", "tga", "--no-open"],
                                input=self.png, capture_output=True, env=dict(os.environ), timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"invalid choice: 'tga'", result.stderr)
        self.assertEqual(self.rows(), [])

    def test_repeated_title_and_caption_pair_with_paths(self):
        first, second = Path(self.temp.name) / "a.png", Path(self.temp.name) / "b.png"
        first.write_bytes(make_png(2))
        second.write_bytes(make_png(3))
        code, payload, _ = self.cli(["show", str(first), str(second), "--title", "A", "--title", "B",
                                     "--caption", "ca", "--caption", "cb", "--no-open"])
        self.assertEqual(code, 0)
        self.assertEqual([(row["title"], row["caption"]) for row in self.rows()], [("A", "ca"), ("B", "cb")])
        # Legacy shape: one --title/--caption still applies to every path, and no key was dropped.
        self.assertEqual(set(payload) - {"images"},
                         {"request", "path", "rendered", "delivered", "acknowledgement", "error", "workspace"})
        self.db.execute("DELETE FROM images")
        self.db.commit()
        self.cli(["show", str(first), str(second), "--title", "Both", "--no-open"])
        self.assertEqual([row["title"] for row in self.rows()], ["Both", "Both"])
        code, _, err = self.cli(["show", str(first), str(second), "--caption", "x", "--caption", "y",
                                 "--caption", "z", "--no-open"])
        self.assertEqual(code, 2)
        self.assertIn("--caption given 3 times for 2 image(s)", err)

    def test_status_and_list_report_bytes_and_files_alike(self):
        sample = Path(self.temp.name) / "on-disk.png"
        sample.write_bytes(make_png(4))
        _, from_file, _ = self.cli(["show", str(sample), "--no-open"])
        file_state = dict(self.db.execute("SELECT key,value FROM state"))
        file_row = self.rows()[-1]
        _, from_bytes, _ = self.cli(["show", "-", "--no-open"], self.png)
        bytes_state = dict(self.db.execute("SELECT key,value FROM state"))
        bytes_row = self.rows()[-1]
        self.assertEqual(set(file_state), set(bytes_state))
        self.assertEqual(set(file_row), set(bytes_row))
        self.assertEqual(set(from_file), set(from_bytes))
        self.assertEqual(bytes_state["requested_path"], from_bytes["path"])
        self.assertTrue(all(bytes_row[column] for column in ("path", "title", "updated", "cached_path")))
        code, listed, _ = self.cli(["list"])
        self.assertEqual(code, 0)
        self.assertEqual([item["path"] for item in listed], [file_row["path"], bytes_row["path"]])
        code, reported, _ = self.cli(["status"])
        self.assertEqual((code, reported["requested_path"]), (0, bytes_row["path"]))


if __name__ == "__main__":
    unittest.main()
