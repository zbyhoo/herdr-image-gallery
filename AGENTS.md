# Developing the gallery plugin

- Plugin code updates reload in the same terminal process and pane. While updating, testing or debugging the plugin, never move, swap, resize, close/recreate or refocus an existing gallery; preserve the location the user chose, including placement above/below other right-column tools.
- Escape must never close the gallery - only `q` does. This is an explicit user preference, covered by tests in `test_gallery.py`; keep it when changing navigation.
- Run the tests with `python3 -m unittest test_gallery`.
