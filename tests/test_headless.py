"""Headless checks: no display, no network, nothing installed or changed.

Run:  python3 -m unittest discover -s tests -v
Set OMARCHY_PLUGINS_SAMPLE_CATALOG=/path/to/catalog.json to also exercise a real catalog.
"""

import atexit
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_CACHE = tempfile.mkdtemp(prefix="omarchy-plugins-test-")  # keeps the real ~/.cache untouched
os.environ["XDG_CACHE_HOME"] = _CACHE
atexit.register(shutil.rmtree, _CACHE, ignore_errors=True)

from omarchy_plugins import backend, theme  # noqa: E402
from omarchy_plugins.backend import Plugin  # noqa: E402

CATALOG = [
    {"id": "acme.weather", "name": "Weather+", "description": "Forecasts", "author": "acme", "version": "1.2.0",
     "repo": "https://github.com/acme/omarchy-weather", "sourceType": "community", "verificationStatus": "verified",
     "installAvailable": True, "installCommand": "omarchy plugin add https://github.com/acme/omarchy-weather.git --enable",
     "stars": 12, "tags": ["weather"], "repositoryRelease": {"tag": "v1.2.0", "url": "https://x/tag"}},
    {"id": "acme.suite", "name": "Suite", "repo": "https://github.com/acme/suite", "sourceType": "community",
     "installAvailable": False, "installNote": "Has its own installer.", "version": "0.1"},
    {"id": "omarchy.clock", "name": "Clock", "author": "Omarchy", "version": "1.0.0", "sourceType": "builtin",
     "repo": "https://github.com/omacom/omarchy", "manifestPath": "panels/clock/manifest.json"},
]


def local(**over):
    base = {"id": "omarchy.clock", "name": "Clock", "kinds": ["bar-widget"], "enabled": True, "can_disable": True,
            "first_party": True, "clone_of": "", "dir": "", "version": "1.0.0", "author": "Omarchy",
            "description": "", "remote": "", "tags": [], "exact_tag": "", "head": "", "on_branch": True,
            "manifest_path": "", "default_section": ""}
    base.update(over)
    return {base["id"]: base}


class ThemeTests(unittest.TestCase):
    def test_resolve_colors(self):
        hypr = {"active-border": "#7daea3", "grad": "rgba(33ccffee) rgba(00ff99ee) 45deg"}
        self.assertEqual(theme.resolve_color("#AABBCC", hypr, "x"), "#aabbcc")
        self.assertEqual(theme.resolve_color("#aabbccdd", hypr, "x"), "#aabbcc")
        self.assertEqual(theme.resolve_color("hyprland.active-border", hypr, "x"), "#7daea3")
        self.assertEqual(theme.resolve_color("hyprland.grad", hypr, "x"), "#33ccff")  # first gradient stop
        self.assertEqual(theme.resolve_color("garbage", hypr, "fb"), "fb")
        self.assertEqual(theme.resolve_color(None, hypr, "fb"), "fb")

    def test_css_follows_theme(self):
        t = theme.load_theme()
        css = theme.build_css(t)
        self.assertIn(t.accent, css)
        self.assertIn(t.bg, css)
        self.assertIn(".installed-mark", css)
        # a different theme must produce different CSS
        t2 = theme.load_theme()
        t2.accent, t2.bg, t2.fg = "#ff00aa", "#101010", "#eeeeee"
        css2 = theme.build_css(t2)
        self.assertIn("#ff00aa", css2)
        self.assertNotEqual(css, css2)

    def test_check_mark_is_not_a_checkbox(self):
        src = (Path(__file__).resolve().parent.parent / "omarchy_plugins/app.py").read_text()
        self.assertNotIn("CheckButton(", src)  # the comment explaining why is fine; building one is not
        self.assertNotIn("CheckButton.new", src)


class BackendTests(unittest.TestCase):
    def test_version_order(self):
        tags = ["v1.0.0", "v1.10.0", "v1.2.0", "v1.0.0-beta.1", "weird"]
        self.assertEqual(backend.sort_tags(tags), ["v1.10.0", "v1.2.0", "v1.0.0", "v1.0.0-beta.1", "weird"])

    def test_normalize_repo(self):
        n = backend.normalize_repo
        self.assertEqual(n("git@github.com:Acme/Weather.git"), "https://github.com/acme/weather")
        self.assertEqual(n("https://github.com/Acme/Weather.git/"), "https://github.com/acme/weather")

    def test_merge_marks_installed(self):
        plugins = {}
        new, _ = backend.merge(plugins, CATALOG, local())
        self.assertEqual(len(new), 3)
        self.assertTrue(plugins["omarchy.clock"].installed)
        self.assertTrue(plugins["omarchy.clock"].enabled)
        self.assertFalse(plugins["acme.weather"].installed)
        self.assertEqual(plugins["omarchy.clock"].source, "builtin")
        self.assertIn("panels/clock", plugins["omarchy.clock"].source_url)

    def test_install_toggle_and_ghosts(self):
        plugins = {}
        backend.merge(plugins, CATALOG, {})
        _, changed = backend.merge(plugins, CATALOG, local(id="acme.weather", first_party=False, version="1.2.0", enabled=False,
                                                            dir="/x"))
        self.assertTrue(plugins["acme.weather"].installed)
        self.assertFalse(plugins["acme.weather"].enabled)
        self.assertIn(plugins["acme.weather"], changed)
        # removing it flips it back
        backend.merge(plugins, CATALOG, {})
        self.assertFalse(plugins["acme.weather"].installed)
        self.assertEqual(plugins["acme.weather"].installed_dir, "")

    def test_unlisted_local_plugin_and_repo_match(self):
        plugins = {}
        backend.merge(plugins, CATALOG, local(id="me.custom", first_party=False, remote="", dir="/y"))
        self.assertEqual(plugins["me.custom"].source, "local")
        # installed under a different id than the catalog's, matched through its git remote
        backend.merge(plugins, CATALOG, local(id="acme.other-id", first_party=False,
                                              remote="git@github.com:acme/omarchy-weather.git", dir="/z"))
        p = plugins["acme.weather"]
        self.assertTrue(p.installed)
        self.assertEqual(p.local_id, "acme.other-id")
        self.assertNotIn("acme.other-id", plugins)

    def test_versions_and_selection(self):
        p = Plugin("x")
        p.version = "1.2.0"
        self.assertEqual(len(p.versions()), 1)  # no tags -> plain label, no drop-down
        p.remote_tags = ["v1.0.0", "v1.2.0", "v1.1.0"]
        self.assertEqual([t for _l, t in p.versions()], [None, "v1.2.0", "v1.1.0", "v1.0.0"])
        self.assertEqual(p.versions()[0][0], "1.2.0 (latest)")
        p.selected_version = "v1.1.0"
        self.assertEqual(p.selected_index(), 2)
        p.installed, p.installed_tag = True, "v1.0.0"
        self.assertEqual(p.selected_index(), 3)

    def test_update_flag_and_states(self):
        p = Plugin("x")
        p.installed, p.update_state = True, "available"
        self.assertTrue(p.update_available)
        p.update_state = "current"
        self.assertFalse(p.update_available)
        p.installed, p.update_state = False, "available"
        self.assertFalse(p.update_available)  # never offered for something that isn't installed

    def test_merge_update_states(self):
        plugins = {}
        backend.merge(plugins, CATALOG, local(id="acme.weather", first_party=False, dir="/x", head="aaa1111"))
        p = plugins["acme.weather"]
        self.assertEqual(p.update_state, "unknown")          # git checkout on a branch: worth checking
        p.update_state, p.update_checked = "current", 123.0
        backend.merge(plugins, CATALOG, local(id="acme.weather", first_party=False, dir="/x", head="aaa1111"))
        self.assertEqual((p.update_state, p.update_checked), ("current", 123.0))  # unchanged: no needless re-check
        backend.merge(plugins, CATALOG, local(id="acme.weather", first_party=False, dir="/x", head="bbb2222"))
        self.assertEqual((p.update_state, p.update_checked), ("unknown", 0.0))    # updated elsewhere: re-check
        backend.merge(plugins, CATALOG, local(id="acme.weather", first_party=False, dir="/x", head="bbb2222",
                                              on_branch=False))
        self.assertEqual(p.update_state, "pinned")           # deliberately on a tag: never nag
        backend.merge(plugins, CATALOG, local())
        self.assertEqual(plugins["omarchy.clock"].update_state, "n/a")  # built-ins update with Omarchy

    def test_check_update_is_read_only(self):
        calls = []
        orig = backend.run

        def fake(remote):
            def f(args, timeout=60):
                calls.append(args)
                if "rev-parse" in args:
                    return 0, "aaa"
                if "ls-remote" in args:
                    return (0, f"{remote}\tHEAD") if remote else (1, "offline")
                return 0, ""
            return f
        try:
            backend.run = fake("bbb")
            self.assertEqual(backend.check_update("/p"), ("available", "bbb"))
            backend.run = fake("aaa")
            self.assertEqual(backend.check_update("/p")[0], "current")
            backend.run = fake("")
            self.assertEqual(backend.check_update("/p")[0], "unknown")
        finally:
            backend.run = orig
        for c in calls:  # nothing that writes to the repo
            self.assertFalse({"fetch", "merge", "checkout", "pull", "reset"} & set(c), c)

    def test_update_preview_summarises_changes(self):
        orig, orig_busy = backend.run, backend.repo_busy

        def fake(args, timeout=60):
            a = " ".join(args)
            if "fetch" in args:
                return 0, ""
            if "rev-parse --short HEAD" in a:
                return 0, "aaa1111"
            if "rev-parse --short FETCH_HEAD" in a:
                return 0, "bbb2222"
            if "rev-parse FETCH_HEAD" in a:
                return 0, "b" * 40
            if "rev-parse HEAD" in a:
                return 0, "a" * 40
            if "merge-base" in a:
                return 0, ""
            if "log" in args:
                return 0, "bbb2222\tAda\t2026-09-20\tFix crash on resume\nccc3333\tAda\t2026-09-19\tAdd option"
            if "rev-list" in args:
                return 0, "2"
            if "--shortstat" in args:
                return 0, "1 file changed, 4 insertions(+)"
            if "--name-only" in args:
                return 0, "Widget.qml\nREADME.md"
            if "show" in args:
                return 0, json.dumps({"version": "1.1.0"})
            return 0, ""
        backend.run, backend.repo_busy = fake, lambda path: ""
        try:
            p = Plugin("acme.weather")
            p.repo, p.installed_dir, p.installed_version = "https://github.com/acme/omarchy-weather", "/nonexistent", "1.0.0"
            Path("/nonexistent")  # existence is faked below
            import pathlib
            real_exists = pathlib.Path.exists
            pathlib.Path.exists = lambda self: True
            try:
                r = backend.update_preview(p)
            finally:
                pathlib.Path.exists = real_exists
        finally:
            backend.run, backend.repo_busy = orig, orig_busy
        self.assertEqual(r["state"], "available")
        self.assertEqual((r["old_version"], r["new_version"], r["count"]), ("1.0.0", "1.1.0", 2))
        self.assertEqual(r["commits"][0][3], "Fix crash on resume")
        self.assertTrue(r["can_ff"])
        self.assertTrue(r["touches_code"])  # Widget.qml changed: the UI must warn
        self.assertTrue(r["compare_url"].endswith("/compare/" + "a" * 40 + "..." + "b" * 40))

    def test_apply_update_defers_and_rechecks(self):
        calls = []
        orig, orig_busy, orig_check = backend.run, backend.repo_busy, backend.check_update
        backend.run = lambda args, timeout=60: (calls.append(args), (0, "Updated x."))[1]
        p = Plugin("acme.weather")
        p.local_id, p.installed_dir = "acme.weather", "/x"
        try:
            backend.repo_busy = lambda path: "Another plugin add/update/remove is already running"
            ok, msg = backend.apply_update(p)
            self.assertFalse(ok)
            self.assertEqual(calls, [])  # stepped aside: ran nothing
            backend.repo_busy = lambda path: ""
            backend.check_update = lambda path: ("current", "s")  # someone else already updated it
            self.assertEqual(backend.apply_update(p), (True, "Already up to date."))
            self.assertEqual(calls, [])
            backend.check_update = lambda path: ("available", "s")
            self.assertTrue(backend.apply_update(p)[0])
        finally:
            backend.run, backend.repo_busy, backend.check_update = orig, orig_busy, orig_check
        self.assertEqual(calls, [["omarchy-plugin-update", "acme.weather", "--yes"]])  # Omarchy's own updater

    def test_repo_busy_sees_git_lock(self):
        d = tempfile.mkdtemp(prefix="omarchy-plugins-test-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, ".git").mkdir()
        orig = backend.run
        backend.run = lambda args, timeout=60: (1, "")  # pgrep finds nothing
        try:
            self.assertEqual(backend.repo_busy(d), "")
            Path(d, ".git", "index.lock").write_text("")
            self.assertIn("busy", backend.repo_busy(d))
        finally:
            backend.run = orig

    def test_bar_position_only_for_bar_widgets(self):
        calls = []
        orig = backend.run
        backend.run = lambda args, timeout=60: (calls.append(args), (0, "Added acme.weather into /p"))[1]
        try:
            w = Plugin("acme.weather")
            w.repo, w.kind_label = "https://github.com/acme/omarchy-weather", "Bar widget"
            self.assertTrue(w.is_bar_widget)
            backend.add_plugin(w, None, "right")
            self.assertEqual(calls[0], ["omarchy-plugin-add", "https://github.com/acme/omarchy-weather.git", "--yes"])
            self.assertEqual(calls[1], ["omarchy-plugin-enable", "acme.weather", "--section", "right"])
            calls.clear()
            b = Plugin("acme.bar")
            b.repo, b.kind_label = "https://github.com/acme/bar", "Bar"  # a full bar replaces the bar: no position
            self.assertFalse(b.is_bar_widget)
            backend.add_plugin(b, None, "right")
            self.assertNotIn("--section", calls[1])
            calls.clear()
            w.installed, w.kinds, w.local_id = True, ["bar-widget"], "acme.weather"
            backend.set_enabled(w, True, "left")
            self.assertEqual(calls[0], ["omarchy-plugin-enable", "acme.weather", "--section", "left"])
            backend.set_enabled(w, False)
            self.assertEqual(calls[1], ["omarchy-plugin-disable", "acme.weather"])
        finally:
            backend.run = orig

    def test_clone_is_available_on_the_backend(self):
        calls = []
        orig = backend.run
        backend.run = lambda args, timeout=60: (calls.append(args), (0, "Cloned"))[1]
        try:
            p = Plugin("omarchy.clock")
            p.first_party = True
            self.assertEqual(backend.clone_plugin(p), (True, "Cloned"))
            self.assertEqual(calls, [["omarchy-plugin-clone", "omarchy.clock"]])
            third = Plugin("acme.weather")
            self.assertFalse(backend.clone_plugin(third)[0])  # only built-ins can be cloned
        finally:
            backend.run = orig

    def test_add_uses_official_command(self):
        calls = []
        orig = backend.run
        backend.run = lambda args, timeout=60: (calls.append(args), (0, "ok"))[1]
        try:
            p = Plugin("acme.weather")
            p.repo = "https://github.com/acme/omarchy-weather"
            self.assertEqual(backend.add_plugin(p), (True, "ok"))
        finally:
            backend.run = orig
        self.assertEqual(calls[0], ["omarchy-plugin-add", "https://github.com/acme/omarchy-weather.git",
                                    "--enable", "--yes"])

    def test_pinned_add_removes_on_failed_checkout(self):
        calls = []
        orig = backend.run

        def fake(args, timeout=60):
            calls.append(args)
            if args[0] == "omarchy-plugin-add":
                return 0, "Added acme.weather into /tmp/nope"
            if "checkout" in args and "--detach" in args:
                return 1, "boom"
            return 0, ""
        backend.run = fake
        try:
            p = Plugin("acme.weather")
            p.repo = "https://github.com/acme/omarchy-weather"
            ok, msg = backend.add_plugin(p, "v1.0.0")
        finally:
            backend.run = orig
        self.assertFalse(ok)
        self.assertTrue(any(c[:2] == ["omarchy-plugin-remove", "acme.weather"] for c in calls))

    def test_tag_input_is_refused_if_it_looks_like_an_option(self):
        self.assertFalse(backend._checkout_tag("/tmp", "--upload-pack=evil")[0])

    def test_real_catalog_parses(self):
        path = os.environ.get("OMARCHY_PLUGINS_SAMPLE_CATALOG")
        if not path or not Path(path).exists():
            self.skipTest("no sample catalog")
        entries = json.loads(Path(path).read_text())["plugins"]
        plugins = {}
        new, _ = backend.merge(plugins, entries, {})
        self.assertEqual(len(new), len({e["id"] for e in entries}))
        self.assertTrue(all(p.name for p in plugins.values()))

    def test_local_state_reads_this_machine(self):
        local_state, err = backend.read_local()
        if err:
            self.skipTest(err)
        self.assertTrue(any(v["first_party"] for v in local_state.values()))


class SandboxTests(unittest.TestCase):
    """`--sandbox`: real data in, simulated changes out, and nothing can reach the system."""

    def setUp(self):
        self._cache = backend.CACHE_DIR
        backend.enable_sandbox()
        self.addCleanup(self._restore)
        self._delay, backend._sim_delay = backend._sim_delay, lambda: None
        self._real_local, backend._read_local_real = backend._read_local_real, lambda: (local(), None)

    def _restore(self):
        backend.SANDBOX, backend.CACHE_DIR = False, self._cache
        backend._sim_delay, backend._read_local_real = self._delay, self._real_local
        backend.SIM.update({"added": {}, "removed": set(), "enabled": {}, "patch": {}, "updatable": set()})

    def test_mutating_commands_are_blocked(self):
        for args in (["omarchy-plugin-remove", "x", "--yes"], ["omarchy-plugin-add", "u", "--yes"],
                     ["omarchy-plugin-enable", "x"], ["omarchy-plugin-update", "x", "--yes"],
                     ["omarchy-shell", "shell", "rescanPlugins"], ["git", "-C", "/x", "fetch", "origin", "HEAD"],
                     ["git", "-C", "/x", "checkout", "main"], ["git", "-C", "/x", "tag", "v1"],
                     ["git", "clone", "https://x/y"], ["git", "-C", "/x", "remote", "add", "o", "u"], ["rm", "-rf", "/"]):
            rc, out = backend.run(args)
            self.assertEqual(rc, 126, args)
            self.assertIn("sandbox", out)

    def test_read_only_commands_are_allowed(self):
        for args in (["git", "-C", "/nonexistent", "tag", "--list"], ["git", "-C", "/nonexistent", "rev-parse", "HEAD"],
                     ["git", "-C", "/nonexistent", "remote", "get-url", "origin"],
                     ["git", "ls-remote", "--tags", "--refs", "file:///nonexistent"], ["pgrep", "-f", "zzzz-nothing-zzzz"]):
            self.assertNotEqual(backend.run(args)[0], 126, args)

    def test_cache_is_scratch_not_home(self):
        self.assertNotEqual(backend.CACHE_DIR, self._cache)
        self.assertNotIn(str(Path.home() / ".cache"), str(backend.CACHE_DIR))

    def test_simulated_lifecycle_never_spawns_a_process(self):
        def boom(*a, **k):
            raise AssertionError(f"a subprocess was started in sandbox mode: {a}")
        real_run, backend.subprocess.run = backend.subprocess.run, boom
        try:
            p = Plugin("acme.weather")
            p.name, p.repo, p.kind_label, p.version = "Weather+", "https://github.com/acme/omarchy-weather", "Bar widget", "1.2.0"
            plugins = {}
            backend.merge(plugins, CATALOG, backend.read_local()[0])
            q = plugins["acme.weather"]
            self.assertFalse(q.installed)

            self.assertTrue(backend.add_plugin(q, None, "right")[0])  # simulated add
            backend.merge(plugins, CATALOG, backend.read_local()[0])
            self.assertTrue(q.installed and q.enabled)
            self.assertEqual(q.default_section, "right")
            self.assertTrue(q.installed_dir.startswith("/sandbox/"))
            self.assertEqual(backend.check_update(q.installed_dir)[0], "available")  # demo update offered
            self.assertEqual(backend.update_preview(q)["state"], "available")
            self.assertTrue(backend.update_preview(q)["touches_code"])

            self.assertTrue(backend.apply_update(q)[0])
            self.assertEqual(backend.check_update(q.installed_dir)[0], "current")
            self.assertTrue(backend.switch_version(q, "v1.0.0")[0])
            backend.merge(plugins, CATALOG, backend.read_local()[0])
            self.assertEqual(q.installed_tag, "v1.0.0")

            self.assertTrue(backend.set_enabled(q, False)[0])
            backend.merge(plugins, CATALOG, backend.read_local()[0])
            self.assertFalse(q.enabled)
            self.assertTrue(backend.set_enabled(plugins["omarchy.clock"], False)[0])  # built-ins toggle too
            backend.merge(plugins, CATALOG, backend.read_local()[0])
            self.assertFalse(plugins["omarchy.clock"].enabled)

            self.assertTrue(backend.remove_plugin(q)[0])
            backend.merge(plugins, CATALOG, backend.read_local()[0])
            self.assertFalse(q.installed)
            self.assertFalse(backend.clone_plugin(plugins["omarchy.clock"])[0])  # clone disabled in the sandbox
        finally:
            backend.subprocess.run = real_run

    def test_normal_mode_unaffected_after_sandbox(self):
        self._restore()
        self.assertFalse(backend.SANDBOX)
        self.assertNotEqual(backend.run(["true"])[0], 126)


class DownloadLimitTests(unittest.TestCase):
    """Oversized responses are refused before they fill memory or the cache (no network used)."""

    class _Response(io.BytesIO):
        def __init__(self, body, headers=None):
            super().__init__(body)
            self.headers = headers or {}

    def _serve(self, body, headers=None):
        real = backend.urllib.request.urlopen
        backend.urllib.request.urlopen = lambda *a, **k: self._Response(body, headers)
        self.addCleanup(setattr, backend.urllib.request, "urlopen", real)

    def test_read_capped(self):
        self.assertEqual(backend.read_capped(io.BytesIO(b"x" * 100), 100), b"x" * 100)
        with self.assertRaises(backend.TooLarge):
            backend.read_capped(io.BytesIO(b"x" * 101), 100)

    def test_gzip_bomb_is_refused(self):
        bomb = gzip.compress(b"\0" * (5 * 1024 * 1024))  # ~5 KB that expands to 5 MB
        self.assertLess(len(bomb), 64 * 1024)
        with self.assertRaises(backend.TooLarge):
            backend.gunzip_capped(bomb, 1024 * 1024)
        self.assertEqual(backend.gunzip_capped(gzip.compress(b"ok"), 2), b"ok")
        with self.assertRaises(ValueError):
            backend.gunzip_capped(b"not gzip", 100)

    def test_oversized_image_is_not_cached(self):
        self._serve(b"x" * (backend.IMAGE_MAX_BYTES + 1))
        url = "https://plugins.omarchy.org/too-big-test.webp"
        self.assertIsNone(backend.fetch_image(url))
        self.assertEqual(list((backend.CACHE_DIR / "images").glob("*too-big*")), [])
        self._serve(b"small")
        self.assertTrue(Path(backend.fetch_image(url)).read_bytes() == b"small")

    def test_oversized_catalog_is_refused(self):
        body = json.dumps({"plugins": [{"pad": "x" * 2048}] * 4096}).encode()  # ~8.5 MB of JSON
        gz = {"Content-Encoding": "gzip"}
        self._serve(gzip.compress(body), gz)
        self.assertIsNotNone(backend.fetch_catalog(force=True)[0])
        real = backend.CATALOG_MAX_JSON_BYTES
        backend.CATALOG_MAX_JSON_BYTES = 1024 * 1024
        self.addCleanup(setattr, backend, "CATALOG_MAX_JSON_BYTES", real)
        self._serve(gzip.compress(body), gz)
        entries, status = backend.fetch_catalog(force=True)
        self.assertIsNone(entries)
        self.assertIn("offline", status)


if __name__ == "__main__":
    unittest.main()
