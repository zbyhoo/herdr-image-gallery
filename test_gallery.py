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


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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

        def start():
            return subprocess.Popen([sys.executable, str(Path(gallery.__file__))], stdin=slave, stdout=slave, stderr=slave, env=env)

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
            os.write(master, b"\t")
            wait_for(proc, lambda: state().get("mode") == "grid")
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


if __name__ == "__main__":
    unittest.main()
