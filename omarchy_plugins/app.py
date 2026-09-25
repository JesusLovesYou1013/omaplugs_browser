"""Search Plugins — browse, add, enable and remove Omarchy shell plugins."""

import os
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import backend, theme  # noqa: E402
from .backend import Plugin  # noqa: E402

APP_ID = "org.omarchy.plugins"
CHECK = "\U000f012c"  # nf-md-check — a plain mark, never a checkbox
BUILTIN_TIP = ("Built into Omarchy — bundled with the omarchy package, so it can't be removed. "
               "Use Disable to turn it off.")


# --------------------------------------------------------------------------- small helpers

def bg(fn, done=None):
    """Run fn on a worker thread; deliver its result to `done` on the UI thread."""
    def work():
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            result = e
        if done:
            GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=work, daemon=True).start()


def open_url(url):
    try:
        subprocess.Popen(["omarchy-launch-browser", url], env=backend._env(), start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        Gtk.show_uri(None, url, Gdk.CURRENT_TIME)


def esc(text):
    return GLib.markup_escape_text(str(text))


def link_markup(url, text=None):
    return f'<a href="{esc(url)}">{esc(text or url)}</a>'


def badge(text, kind=""):
    label = Gtk.Label(label=text)
    label.add_css_class("badge")
    if kind:
        label.add_css_class(f"badge-{kind}")
    return label


def clear(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def button(label, css="", callback=None):
    b = Gtk.Button(label=label)
    b.add_css_class("omarchy-btn")
    for c in css.split():
        b.add_css_class(c)
    if callback:
        b.connect("clicked", callback)
    return b


def plugin_badges(p, box):
    clear(box)
    if p.source == "builtin":
        box.append(badge("Built-in", "builtin"))
    elif p.source == "local":
        box.append(badge("Local"))
    if p.clone_of:
        box.append(badge("Clone", "builtin"))
    if p.installed:
        box.append(badge("Enabled", "enabled") if p.enabled else badge("Disabled", "disabled"))
    if p.source == "community":
        if p.verification == "verified":
            box.append(badge("Verified", "verified"))
        else:
            box.append(badge("Unverified", "warn"))
    if not p.installed and p.install_available is False:
        box.append(badge("Manual install"))  # explains the disabled Add button at a glance
    if p.update_available:
        box.append(badge("Update", "warn"))


def apply_buttons(p, primary, toggle, update=None):
    """Add/Remove, Enable/Disable and (only when one exists) Update — worded like Setup > Plugins."""
    if update is not None:
        update.set_visible(p.update_available)  # independent of enabled/disabled
        update.set_sensitive(not p.busy)
    for c in ("primary", "danger"):
        primary.remove_css_class(c)
    tip = None
    if p.busy:
        primary.set_label(p.busy)
        primary.set_sensitive(False)
    elif p.installed:
        primary.set_label("Remove")
        primary.add_css_class("danger")
        removable = not (p.first_party and not p.clone_of)
        primary.set_sensitive(removable)
        tip = None if removable else BUILTIN_TIP
    else:
        primary.set_label("Add")
        primary.add_css_class("primary")
        ok = p.install_available is not False and bool(p.repo)
        primary.set_sensitive(ok)
        tip = None if ok else (p.install_note or "This listing can't be installed with `omarchy plugin add`.")
    primary.set_tooltip_text(tip)

    toggle.set_visible(p.installed)
    if p.installed:
        toggle.set_label("Disable" if p.enabled else "Enable")
        pinned_on = p.enabled and not p.can_disable
        toggle.set_sensitive(not p.busy and not pinned_on)
        toggle.set_tooltip_text("A bar can't be switched off — enable another bar to replace it."
                                if pinned_on else None)


# --------------------------------------------------------------------------- version picker

class VersionPicker(Gtk.Box):
    """The plugin's version: plain text, or a drop-down when older versions can be chosen."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.win, self.p, self._sync, self._labels = win, None, False, None
        cap = Gtk.Label(label="Version", xalign=0)
        cap.add_css_class("dim")
        cap.add_css_class("small")
        self.label = Gtk.Label(xalign=0, selectable=False)
        self.model = Gtk.StringList()
        self.drop = Gtk.DropDown(model=self.model)
        self.drop.add_css_class("version-drop")
        self.drop.connect("notify::selected", self._on_selected)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.label, "label")
        self.stack.add_named(self.drop, "drop")
        self.hint = Gtk.Label(xalign=0)
        self.hint.add_css_class("dim")
        self.hint.add_css_class("small")
        for w in (cap, self.stack, self.hint):
            self.append(w)

    def set_plugin(self, p):
        self.p = p
        self.win.ensure_tags(p)  # first, so the "checking versions…" hint below is accurate
        versions = p.versions()
        multi = len(versions) > 1 and p.source != "builtin"
        self._sync = True
        if multi:
            labels = [v[0] for v in versions]
            if labels != self._labels:
                self.model.splice(0, self.model.get_n_items(), labels)
                self._labels = labels
            self.drop.set_selected(p.selected_index())
            self.drop.set_sensitive(not p.busy)
            self.stack.set_visible_child_name("drop")
        else:
            self._labels = None
            self.label.set_text(p.shown_version)
            self.stack.set_visible_child_name("label")
        self._sync = False

        if p.source == "builtin":
            hint = f"with Omarchy {backend.omarchy_version()}".strip()
        elif p.update_available:
            hint = "update available"
        elif p.tags_state == "loading":
            hint = "checking versions…"
        else:
            hint = ""
        self.hint.set_text(hint)
        self.hint.set_visible(bool(hint))

    def _on_selected(self, drop, _pspec):
        if self._sync or not self.p:
            return
        versions = self.p.versions()
        idx = drop.get_selected()
        if 0 <= idx < len(versions):
            self.win.request_version(self.p, versions[idx][1])


# --------------------------------------------------------------------------- list row

class PluginRow(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.add_css_class("plugin-row")
        self.win, self.p, self._handler = win, None, None

        # Not a Gtk.CheckButton on purpose: it must not look clickable.
        self.mark = Gtk.Label(label=CHECK)
        self.mark.add_css_class("installed-mark")
        self.mark.set_can_target(False)
        self.mark.set_can_focus(False)
        self.mark.set_valign(Gtk.Align.CENTER)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        head = Gtk.Box(spacing=10)
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("plugin-name")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.badges = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        head.append(self.title)
        head.append(self.badges)
        self.desc = Gtk.Label(xalign=0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=90, width_chars=30)
        self.desc.add_css_class("dim")
        self.meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.meta.add_css_class("dim")
        self.meta.add_css_class("small")
        for w in (head, self.desc, self.meta):
            text.append(w)

        self.version = VersionPicker(win)
        self.version.set_valign(Gtk.Align.CENTER)
        self.version.set_size_request(150, -1)

        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        btns.set_size_request(118, -1)
        self.primary = button("Add", callback=lambda *_: win.on_primary(self.p))
        self.toggle = button("Enable", callback=lambda *_: win.on_toggle(self.p))
        self.update = button("Update", "primary", callback=lambda b: win.show_update_preview(self.p, b))
        self.info = button("More Info", callback=lambda *_: win.show_info(self.p))
        for b in (self.primary, self.toggle, self.update, self.info):  # Update only shows when there is one
            btns.append(b)

        for w in (self.mark, text, self.version, btns):
            self.append(w)

    def bind(self, p):
        self.p = p
        self._handler = p.connect("changed", lambda *_: self.refresh())
        self.refresh()

    def unbind(self):
        if self.p and self._handler:
            self.p.disconnect(self._handler)
        self.p, self._handler = None, None

    def refresh(self):
        p = self.p
        if not p:
            return
        self.mark.set_opacity(1.0 if p.installed else 0.0)  # keeps the column aligned when absent
        self.title.set_text(p.name or p.id)
        plugin_badges(p, self.badges)
        self.desc.set_text(p.description or "No description provided.")
        bits = [p.author, p.category or p.kind_label, f"★ {p.stars}" if p.stars else ""]
        self.meta.set_text("  ·  ".join(b for b in bits if b))
        self.version.set_plugin(p)
        apply_buttons(p, self.primary, self.toggle, self.update)


# --------------------------------------------------------------------------- preview carousel

class PreviewCarousel(Gtk.Box):
    """Preview screenshots from the plugin's marketplace listing. Swipe, click the (translucent,
    theme-coloured) arrows, or use the arrow keys; a single image just hides the arrows."""

    HEIGHT = 320

    def __init__(self, images):
        super().__init__()
        self.images, self.index, self._pages = images, 0, {}

        overlay = Gtk.Overlay()
        overlay.add_css_class("carousel-frame")
        overlay.set_size_request(-1, self.HEIGHT)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        overlay.set_child(self.stack)
        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.stack.add_named(self.spinner, "loading")
        self.broken = Gtk.Label(label="Preview unavailable", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.broken.add_css_class("dim")
        self.stack.add_named(self.broken, "broken")

        self.prev_btn = self._arrow("", -1)   # nf-fa-chevron_left / _right
        self.next_btn = self._arrow("", 1)
        self.prev_btn.set_halign(Gtk.Align.START)
        self.next_btn.set_halign(Gtk.Align.END)
        for b in (self.prev_btn, self.next_btn):
            b.set_valign(Gtk.Align.CENTER)
            overlay.add_overlay(b)

        self.dots = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        self.dots.add_css_class("carousel-dots")
        overlay.add_overlay(self.dots)

        swipe = Gtk.GestureSwipe()
        swipe.connect("swipe", self._on_swipe)
        overlay.add_controller(swipe)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        overlay.add_controller(keys)
        overlay.set_focusable(True)
        overlay.set_can_focus(True)

        self.append(overlay)
        self._build_dots()
        self._show(0)

    def _arrow(self, glyph, delta):
        b = Gtk.Button(label=glyph)
        b.add_css_class("carousel-arrow")
        b.add_css_class("mono")
        b.connect("clicked", lambda *_: self.go(delta))
        return b

    def _build_dots(self):
        clear(self.dots)
        multi = len(self.images) > 1
        self.prev_btn.set_visible(multi)
        self.next_btn.set_visible(multi)
        self.dots.set_visible(multi)
        for i in range(len(self.images)):
            d = Gtk.Label(label="●")
            d.add_css_class("carousel-dot")
            if i == self.index:
                d.add_css_class("active")
            self.dots.append(d)

    def go(self, delta):
        if len(self.images) > 1:
            self._show((self.index + delta) % len(self.images))

    def _on_swipe(self, _g, vx, _vy):
        if vx < -150:
            self.go(1)
        elif vx > 150:
            self.go(-1)

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Left:
            self.go(-1); return True
        if keyval == Gdk.KEY_Right:
            self.go(1); return True
        return False

    def _show(self, index):
        self.index = index
        self._build_dots()
        page = self._pages.get(index)
        if page:
            self.stack.set_visible_child_name(page)
            return
        self.stack.set_visible_child_name("loading")
        url = self.images[index]["full"]

        def done(path):
            if not path or isinstance(path, Exception):
                if self.index == index:
                    self.stack.set_visible_child_name("broken")
                return
            # Gtk.Picture.new_for_filename() has been observed loading a fixed-size (200x200)
            # texture regardless of the source image; decoding via Gdk.Texture avoids that.
            try:
                texture = Gdk.Texture.new_from_filename(path)
            except GLib.Error:
                texture = None
            if texture is None:
                if self.index == index:
                    self.stack.set_visible_child_name("broken")
                return
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
            name = f"page-{index}"
            self._pages[index] = name
            self.stack.add_named(picture, name)
            if self.index == index:
                self.stack.set_visible_child_name(name)
        bg(lambda: backend.fetch_image(url), done)


# --------------------------------------------------------------------------- info page

class InfoPage(Gtk.Box):
    def __init__(self, win, p):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win, self.p = win, p
        self._dyn = {}

        top = Gtk.Box(spacing=14)
        top.add_css_class("tabbar")
        top.set_margin_bottom(0)
        back = button("Back", callback=lambda *_: win.go_back())
        back.set_margin_top(8)
        back.set_margin_bottom(8)
        crumb = Gtk.Label(label="Plugin details", xalign=0)
        crumb.add_css_class("dim")
        top.append(back)
        top.append(crumb)
        self.append(top)

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        for side in ("start", "end"):
            getattr(body, f"set_margin_{side}")(22)
        body.set_margin_top(18)
        body.set_margin_bottom(24)
        scroller.set_child(body)
        self.append(scroller)

        # header: mark + name/badges on the left, version + actions on the right
        head = Gtk.Box(spacing=16)
        self.mark = Gtk.Label(label=CHECK)
        self.mark.add_css_class("installed-mark")
        self.mark.set_can_target(False)
        self.mark.set_can_focus(False)
        self.mark.set_valign(Gtk.Align.START)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.name = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.name.add_css_class("plugin-name")
        self.name.set_markup(f'<span size="x-large" weight="bold">{esc(p.name or p.id)}</span>')
        self.badges = Gtk.Box(spacing=6)
        sub = Gtk.Label(label="  ·  ".join(b for b in (p.author and f"by {p.author}", p.id) if b), xalign=0,
                        selectable=True)
        sub.add_css_class("dim")
        for w in (self.name, self.badges, sub):
            titles.append(w)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
        actions.set_size_request(170, -1)
        self.version = VersionPicker(win)
        self.primary = button("Add", callback=lambda *_: win.on_primary(self.p))
        self.toggle = button("Enable", callback=lambda *_: win.on_toggle(self.p))
        self.update = button("Update", "primary", callback=lambda b: win.show_update_preview(self.p, b))
        for w in (self.version, self.primary, self.toggle, self.update):
            actions.append(w)
        for w in (self.mark, titles, actions):
            head.append(w)
        body.append(head)

        if p.preview_images:
            body.append(PreviewCarousel(p.preview_images))

        # about
        self._section(body, "About")
        about = self._value(p.description or "No description provided.")
        body.append(about)

        if p.source == "community":
            note = "Community plugins are independent third-party code and run unsandboxed inside omarchy-shell."
            if p.verification != "verified":
                note += " This listing has not passed the marketplace's automated verification."
            body.append(self._notice(note))

        # details
        self._section(body, "Details")
        grid = self._grid()
        body.append(grid)
        self._details = grid
        self._fill_details()

        # developer
        self._section(body, "Developer")
        self.dev = self._grid()
        body.append(self.dev)
        self.dev_note = self._value("")
        self.dev_note.add_css_class("dim")
        body.append(self.dev_note)
        self._fill_developer(None)

        # links
        self._section(body, "Links")
        self.links = self._grid()
        body.append(self.links)
        self._fill_links(None)

        # install
        cmd = p.clone_command
        if cmd or p.install_note:
            self._section(body, "Install from a terminal")
            if cmd:
                c = self._value(cmd)
                c.add_css_class("mono")
                body.append(c)
            if p.install_note:
                n = self._value(p.install_note)
                n.add_css_class("dim")
                body.append(n)

        self._handler = p.connect("changed", lambda *_: self.refresh())
        self.refresh()
        win.ensure_tags(p, force=True)
        if p.repo:
            bg(lambda: backend.github_info(p.repo), self._on_github)

    # -- builders
    def _section(self, body, title):
        label = Gtk.Label(label=title.upper(), xalign=0)
        label.add_css_class("section-title")
        body.append(label)

    def _value(self, text, markup=False):
        label = Gtk.Label(xalign=0, wrap=True, selectable=True, hexpand=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.connect("activate-link", lambda _l, uri: (open_url(uri), True)[1])
        (label.set_markup if markup else label.set_text)(text)
        return label

    def _notice(self, text):
        box = Gtk.Box()
        box.add_css_class("notice")
        box.append(self._value(text))
        return box

    def _grid(self):
        g = Gtk.Grid(column_spacing=22, row_spacing=6)
        g._next = 0
        return g

    def _row(self, grid, key, text, markup=False):
        if not text:
            return None
        k = Gtk.Label(label=key, xalign=0, yalign=0)
        k.add_css_class("kv-key")
        k.set_size_request(150, -1)
        v = self._value(text, markup)
        grid.attach(k, 0, grid._next, 1, 1)
        grid.attach(v, 1, grid._next, 1, 1)
        grid._next += 1
        return v

    def _reset(self, grid):
        child = grid.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            grid.remove(child)
            child = nxt
        grid._next = 0

    # -- sections
    def _fill_details(self):
        p, g = self.p, self._details
        self._reset(g)
        kinds = p.kind_label or ", ".join(p.kinds)
        self._row(g, "Latest version", p.latest_version)
        if p.installed:
            self._row(g, "Installed version", p.current_tag or p.installed_version)
        self._row(g, "Kind", kinds)
        self._row(g, "Category", p.category)
        self._row(g, "Tags", ", ".join(p.tags))
        self._row(g, "License", p.license)
        self._row(g, "Stars", f"{p.stars:,}" if p.stars else "")
        self._row(g, "Repo updated", backend.fmt_date(p.updated_at))
        self._row(g, "Listed on marketplace", backend.fmt_date(p.added_at))
        if p.source == "community":
            ver = "Verified" if p.verification == "verified" else "Not verified"
            if p.verified_commit:
                ver += f" at commit {p.verified_commit[:9]}"
            self._row(g, "Marketplace check", ver)
        if p.source == "builtin":
            self._row(g, "Source", "Ships with Omarchy " + backend.omarchy_version())
        if p.installed_dir:
            self._row(g, "Installed at", p.installed_dir)
        if p.clone_of:
            self._row(g, "Cloned from", p.clone_of)

    def _fill_developer(self, gh):
        p, g = self.p, self.dev
        self._reset(g)
        user = (gh or {}).get("user") or {}
        parts = backend.repo_parts(p.repo)
        self._row(g, "Developer", p.author or (parts[0] if parts else ""))
        self._row(g, "Name", user.get("name"))
        if parts:
            self._row(g, "GitHub", link_markup(f"https://github.com/{parts[0]}", f"github.com/{parts[0]}"), True)
        if gh is None and parts:
            self._row(g, "Contact", "Loading developer details…")
        if user.get("email"):
            self._row(g, "Email", link_markup("mailto:" + user["email"], user["email"]), True)
        elif gh is not None and parts:
            self._row(g, "Email", "Not public — use the issue tracker instead")
        blog = (user.get("blog") or "").strip()
        if blog:
            url = blog if blog.startswith("http") else "https://" + blog
            self._row(g, "Website", link_markup(url, blog), True)
        if user.get("twitter_username"):
            handle = user["twitter_username"]
            self._row(g, "X / Twitter", link_markup(f"https://x.com/{handle}", "@" + handle), True)
        self._row(g, "Company", user.get("company"))
        self._row(g, "Location", user.get("location"))
        self._row(g, "Bio", user.get("bio"))
        self.dev_note.set_text((gh or {}).get("error") or "")
        self.dev_note.set_visible(bool((gh or {}).get("error")))

    def _fill_links(self, gh):
        p, g = self.p, self.links
        self._reset(g)
        repo = (gh or {}).get("repo") or {}
        lk = link_markup
        if p.repo:
            self._row(g, "Repository", lk(p.repo), True)
        if p.marketplace_url:
            self._row(g, "Marketplace page", lk(p.marketplace_url), True)
        if p.source == "builtin":
            self._row(g, "Omarchy manual", lk(backend.MANUAL_URL), True)
            if p.source_url:
                self._row(g, "Source folder", lk(p.source_url), True)
        if p.repo and backend.repo_parts(p.repo) and p.source != "builtin":
            self._row(g, "Issues", lk(p.repo + "/issues"), True)
            self._row(g, "Releases", lk(p.release_url or p.repo + "/releases"), True)
        if repo.get("homepage"):
            self._row(g, "Homepage", lk(repo["homepage"]), True)

    def _on_github(self, gh):
        if isinstance(gh, Exception):
            gh = {"error": f"Couldn't load developer details ({gh})."}
        self._fill_developer(gh)
        self._fill_links(gh)

    # -- live state
    def refresh(self):
        p = self.p
        self.mark.set_opacity(1.0 if p.installed else 0.0)
        plugin_badges(p, self.badges)
        self.version.set_plugin(p)
        apply_buttons(p, self.primary, self.toggle, self.update)
        self._fill_details()

    def teardown(self):
        if self._handler:
            self.p.disconnect(self._handler)
            self._handler = None


# --------------------------------------------------------------------------- window

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Search Plugins", default_width=1040, default_height=780)
        self.add_css_class("omarchy-plugins")
        self.plugins = {}
        self.catalog, self.local = [], {}
        self.search_text = ""
        self.info_page = None
        self.tag_workers = backend.TagWorkers(3)
        self.update_workers = backend.TagWorkers(2)  # separate, so update checks never queue behind version lookups
        self._timers = {}
        self._last_css = None
        self._monitors = []

        self.store = Gio.ListStore.new(Plugin)
        cmp = lambda key: (lambda a, b, *_: (key(a) > key(b)) - (key(a) < key(b)))  # noqa: E731
        # Plugins with a screenshot tend to be the more polished submissions, so they sort first;
        # the rest are still there for anyone searching specifically or scrolling past them.
        self.sorter_available = Gtk.CustomSorter.new(
            cmp(lambda p: (0 if p.preview_images else 1, -(p.stars or 0), (p.name or p.id).lower())))
        self.sorter_installed = Gtk.CustomSorter.new(cmp(lambda p: (p.name or p.id).lower()))
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.reload_theme()

        # ---- list page
        self.list_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label="Search Plugins…", xalign=0)
        title.add_css_class("window-title")
        self.list_page.append(title)

        bar = Gtk.Box(spacing=0)
        bar.add_css_class("tabbar")
        self.tab_available, self.count_available = self._make_tab("Available")
        self.tab_installed, self.count_installed = self._make_tab("Installed")
        self.tab_installed.set_group(self.tab_available)
        self.tab_available.set_active(True)
        self.tab_available.connect("toggled", self._on_tab)
        bar.append(self.tab_available)
        bar.append(self.tab_installed)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        self.search = Gtk.SearchEntry(placeholder_text="Search plugins…", valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", self._on_search)
        bar.append(self.search)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Re-sync with the marketplace",
                             valign=Gtk.Align.CENTER, margin_start=8)
        refresh.add_css_class("icon-btn")
        refresh.connect("clicked", lambda *_: (self.sync_catalog(force=True), self.refresh_local(0),
                                            self.check_updates(force=True)))
        bar.append(refresh)
        self.list_page.append(bar)

        self.banner = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.banner.add_css_class("banner")
        self.list_page.append(self.banner)

        self.tabs = Gtk.Stack()
        self.tabs.set_vexpand(True)
        self.tabs.add_named(self._make_list(installed_only=False), "available")
        self.tabs.add_named(self._make_list(installed_only=True), "installed")
        self.list_page.append(self.tabs)

        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("statusbar")
        self.list_page.append(self.status)

        # ---- assemble
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.stack.add_named(self.list_page, "list")
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("frame-root")
        if backend.SANDBOX:
            self.set_title("Search Plugins — sandbox")
            note = Gtk.Label(label="SANDBOX — the real catalog and your real installed plugins, but nothing is changed: "
                                   "add / remove / enable / update are only simulated.", xalign=0, wrap=True)
            note.add_css_class("banner")
            frame.append(note)
        frame.append(self.stack)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(frame)
        self.set_content(self.toasts)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self._watch_files()
        self._loaded = False
        self.connect("notify::is-active", self._on_active)
        self.search.grab_focus()
        self.set_status("Loading…")
        bg(lambda: (backend.load_cached_catalog(), backend.read_local()), self._on_first_load)

    # ---- construction helpers
    def _make_tab(self, name):
        btn = Gtk.ToggleButton()
        btn.add_css_class("tab")
        box = Gtk.Box(spacing=0)
        box.append(Gtk.Label(label=name))
        count = Gtk.Label(label="")
        count.add_css_class("count")
        box.append(count)
        btn.set_child(box)
        return btn, count

    def _make_list(self, installed_only):
        def match(p, *_):
            if installed_only and not p.installed:
                return False
            if p.source == "local" and not p.installed:  # an unlisted plugin that has since been removed
                return False
            return all(tok in p.blob for tok in self.search_text.split())

        flt = Gtk.CustomFilter.new(match)
        model = Gtk.SortListModel.new(Gtk.FilterListModel.new(self.store, flt),
                                      self.sorter_installed if installed_only else self.sorter_available)
        selection = Gtk.NoSelection.new(model)  # rows are never "selected", so nothing looks tick-able

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(PluginRow(self)))
        factory.connect("bind", lambda _f, item: item.get_child().bind(item.get_item()))
        factory.connect("unbind", lambda _f, item: item.get_child().unbind())

        lv = Gtk.ListView.new(selection, factory)
        setattr(self, "lv_installed" if installed_only else "lv_available", lv)
        lv.connect("activate", lambda _lv, pos: self.show_info(selection.get_item(pos)))

        empty = Gtk.Label(label="Nothing installed yet — switch to Available to add a plugin."
                          if installed_only else "No plugins match your search.")
        empty.add_css_class("empty")
        inner = Gtk.Stack()
        inner.add_named(Gtk.ScrolledWindow(child=lv, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        inner.add_named(empty, "empty")

        def sync(*_):
            n = selection.get_n_items()
            inner.set_visible_child_name("list" if n else "empty")
            (self.count_installed if installed_only else self.count_available).set_text(f"{n:,}")

        selection.connect("items-changed", sync)
        sync()
        setattr(self, "filter_installed" if installed_only else "filter_available", flt)
        return inner

    # ---- tabs / search / keys
    def _on_tab(self, btn):
        self.tabs.set_visible_child_name("available" if self.tab_available.get_active() else "installed")

    def _on_search(self, entry):
        self.search_text = entry.get_text().lower().strip()
        for f in (self.filter_available, self.filter_installed):
            f.changed(Gtk.FilterChange.DIFFERENT)
        # GTK keeps the previously visible row anchored when a model changes; a new search should start at the top
        GLib.idle_add(self._scroll_top)

    def _scroll_top(self):
        for lv in (self.lv_available, self.lv_installed):
            if lv.get_model().get_n_items():
                lv.scroll_to(0, Gtk.ListScrollFlags.NONE, None)
        return False

    def _on_key(self, _ctl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            if self.stack.get_visible_child_name() == "info":
                self.go_back()
            elif self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
            return True
        if keyval == Gdk.KEY_f and state & Gdk.ModifierType.CONTROL_MASK and self.stack.get_visible_child_name() == "list":
            self.search.grab_focus()
            return True
        if keyval == Gdk.KEY_Left and state & Gdk.ModifierType.ALT_MASK and self.stack.get_visible_child_name() == "info":
            self.go_back()
            return True
        return False

    # ---- theme (live)
    def reload_theme(self):
        t = theme.load_theme()
        css = theme.build_css(t)
        if css == self._last_css:
            return
        self._last_css = css
        self.css.load_from_string(css)
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_LIGHT if t.mode == "light" else Adw.ColorScheme.FORCE_DARK)

    def _debounce(self, key, ms, fn):
        if key in self._timers:
            GLib.source_remove(self._timers[key])

        def fire():
            self._timers.pop(key, None)
            fn()
            return False
        self._timers[key] = GLib.timeout_add(ms, fire)

    def theme_changed(self):
        # Themes are swapped in several steps (rm + mv + hooks): settle, apply, then re-check once.
        def apply():
            self.reload_theme()
            self._debounce("theme-followup", 1500, self.reload_theme)
        self._debounce("theme", 250, apply)

    def _watch(self, path, cb):
        try:
            mon = Gio.File.new_for_path(str(path)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        mon.connect("changed", lambda _m, f, _o, _e: cb(f.get_basename() or ""))
        self._monitors.append(mon)

    def _watch_files(self):
        self._watch(theme.STATE_DIR, lambda name: self.theme_changed())
        self._watch(theme.USER_CONFIG_DIR, self._on_config_dir)
        self._watch(backend.PLUGINS_DIR, lambda name: self.refresh_local())

    def _on_config_dir(self, name):
        if name == "shell.toml":
            self.theme_changed()
        elif name == "shell.json":
            self.refresh_local()

    # ---- data flow
    def set_status(self, text):
        self.status.set_text(text)

    def _on_first_load(self, res):
        if isinstance(res, Exception):
            self.set_status(f"Couldn't load plugins: {res}")
            return
        cached, (local, err) = res
        self.catalog, self.local = cached or [], local
        self._loaded = True
        self.rebuild(err)
        self.sync_catalog()

    def sync_catalog(self, force=False):
        self.set_status("Syncing with the marketplace…")
        bg(lambda: backend.fetch_catalog(force), self._on_catalog)

    def _on_catalog(self, res):
        if isinstance(res, Exception):
            res = (None, f"error ({res})")
        entries, status = res
        if entries is not None:
            self.catalog = entries
            self.rebuild()
        if status in ("updated", "current"):
            self.banner.set_visible(False)
        elif self.catalog:
            self.banner.set_text(f"Marketplace {status} — showing the last synced catalog.")
            self.banner.set_visible(True)
        else:
            self.banner.set_text(f"Marketplace {status} — showing only built-in and installed plugins for now.")
            self.banner.set_visible(True)
        self._update_status()

    def _update_status(self):
        n = len(self.plugins)
        age = backend.catalog_age()
        when = "" if age is None else (" · synced just now" if age < 90 else f" · synced {int(age // 60)} min ago"
                                       if age < 5400 else f" · synced {int(age // 3600)} h ago")
        ups = sum(1 for p in self.plugins.values() if p.update_available)
        extra = f" · {ups} update{'s' if ups != 1 else ''} available" if ups else ""
        self.set_status(f"{n:,} plugins{when}{extra}")

    def refresh_local(self, delay=400):
        def go():
            def done(res):
                if isinstance(res, Exception):
                    return
                self.local = res[0]
                self.rebuild(res[1])
            bg(backend.read_local, done)
        if delay:
            self._debounce("local", delay, go)
        else:
            go()

    def rebuild(self, local_err=None):
        was_installed = {pid for pid, p in self.plugins.items() if p.installed}
        new, changed = backend.merge(self.plugins, self.catalog, self.local)
        if new:
            self.store.splice(self.store.get_n_items(), 0, new)
        now_installed = {pid for pid, p in self.plugins.items() if p.installed}
        for p in changed:
            p.notify_changed()
        if was_installed != now_installed or new:
            for srt in (self.sorter_available, self.sorter_installed):
                srt.changed(Gtk.SorterChange.DIFFERENT)
            for f in (self.filter_available, self.filter_installed):
                f.changed(Gtk.FilterChange.DIFFERENT)
        if local_err:
            self.banner.set_text(local_err)
            self.banner.set_visible(True)
        self._update_status()
        self.check_updates()

    def _on_active(self, *_):
        if self.is_active() and self._loaded:
            self.refresh_local(300)  # something else may have changed plugins meanwhile
            self.check_updates()

    # ---- updates (read-only detection; see backend.py for how this avoids competing with `omarchy plugin update`)
    def check_updates(self, force=False):
        now = time.time()
        for p in self.plugins.values():
            if not p.installed or p.busy or p.update_state in ("n/a", "pinned", "checking"):
                continue
            if not force and p.update_checked and now - p.update_checked < 900:
                continue
            p.update_state = "checking"
            path = p.installed_dir

            def done(res, p=p):
                def apply():
                    state = res[0] if isinstance(res, tuple) and res[0] in ("available", "current") else "unknown"
                    if p.update_state == "checking":
                        p.update_state = state
                    p.update_checked = time.time()
                    p.notify_changed()
                    self._update_status()
                    return False
                GLib.idle_add(apply)
            self.update_workers.submit(lambda path=path: backend.check_update(path), done)

    def show_update_preview(self, p, anchor):
        """The bubble: what an update would change, shown BEFORE anything is applied."""
        pop = Gtk.Popover()
        pop.set_parent(anchor)
        pop.set_position(Gtk.PositionType.LEFT)
        pop.connect("closed", lambda pp: GLib.idle_add(lambda: (pp.unparent(), False)[1]))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(14)
        box.set_size_request(470, -1)
        wait = Gtk.Box(spacing=10)
        wait.append(Gtk.Spinner(spinning=True))
        wait.append(Gtk.Label(label="Checking what changed…"))
        box.append(wait)
        pop.set_child(box)
        pop.popup()
        bg(lambda: backend.update_preview(p), lambda res: self._fill_update_popover(p, pop, box, res))

    def _fill_update_popover(self, p, pop, box, res):
        clear(box)
        def text(s, css="", wrap=True):
            l = Gtk.Label(label=s, xalign=0, wrap=wrap, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=60)
            for c in css.split():
                l.add_css_class(c)
            return l
        def close_row(*buttons):
            row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            for b in buttons:
                row.append(b)
            box.append(row)
        cancel = button("Close", callback=lambda *_: pop.popdown())
        if isinstance(res, Exception) or res.get("error"):
            box.append(text(str(res) if isinstance(res, Exception) else res["error"]))
            close_row(cancel)
            return
        if res.get("state") == "current":
            p.update_state, p.update_checked = "current", time.time()
            p.notify_changed()
            box.append(text("Already up to date — nothing to apply."))
            close_row(cancel)
            return
        ver = f"{res['old_version'] or '?'} → {res['new_version']}  ·  " if res["new_version"] else ""
        box.append(text(f"Update available for {p.name}", "section-title"))
        box.append(text(f"{ver}{res['current']} → {res['new']}", "mono"))
        n = res["count"]
        summary = f"{n} new commit{'s' if n != 1 else ''}" + (f"  ·  {res['stat']}" if res["stat"] else "")
        box.append(text(summary, "dim"))
        if not res["can_ff"]:
            n_ = self._notice_label("This plugin has local changes or commits, so Omarchy can't fast-forward it. "
                                   "Updating would be refused — resolve that in its folder first.")
            box.append(n_)
        elif res["touches_code"]:
            box.append(self._notice_label("This update changes the plugin's code. Plugins run unsandboxed "
                                         "inside omarchy-shell, so skim the changes before applying."))
        if res["commits"]:
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            for h, author, date, subject in res["commits"]:
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                row.append(text(subject, wrap=True))
                row.append(text(f"{h}  ·  {author}  ·  {date}", "dim small mono", wrap=False))
                listing.append(row)
            scroller = Gtk.ScrolledWindow(child=listing, min_content_height=60, max_content_height=210,
                                          propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            box.append(scroller)
        if res["files"]:
            more = res["n_files"] - len(res["files"])
            box.append(text("Files: " + ", ".join(res["files"]) + (f"  … +{more} more" if more > 0 else ""),
                            "dim small"))
        if res["compare_url"]:
            lk = Gtk.Label(xalign=0, use_markup=True, label=link_markup(res["compare_url"], "View the full diff on GitHub"))
            lk.connect("activate-link", lambda _l, uri: (open_url(uri), True)[1])
            box.append(lk)
        go = button("Update now", "primary", callback=lambda *_: (
            pop.popdown(), self.run_action(p, "Updating…", lambda: backend.apply_update(p), f"Updated {p.name}")))
        go.set_sensitive(res["can_ff"])
        close_row(cancel, go)

    def _notice_label(self, s):
        box = Gtk.Box()
        box.add_css_class("notice")
        box.append(Gtk.Label(label=s, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=58))
        return box

    # ---- tags / versions
    def ensure_tags(self, p, force=False):
        if p.source == "builtin" or not p.repo or p.tags_state != "none":
            return
        if not force and not (p.has_release_hint or p.installed):
            return
        p.tags_state = "loading"
        repo, refetch = p.repo, p.tags_refetch
        p.tags_refetch = False

        def done(result):
            tags, commits = result

            def apply():
                p.remote_tags, p.remote_tag_commits, p.tags_state = list(tags or []), commits, "loaded"
                p.notify_changed()
                return False
            GLib.idle_add(apply)
        self.tag_workers.submit(
            lambda: (backend.remote_tags(repo, force=refetch), backend.remote_tag_commits(repo)), done)

    def request_version(self, p, tag):
        if not p.installed:
            p.selected_version = tag
            return
        current = p.current_tag or None
        if tag == current:
            return
        target = tag or "the latest version"

        def cancel():
            p.notify_changed()  # snap the drop-down back
        self.confirm(f"Switch {p.name} to {target}?",
                     "Omarchy checks out that version of the plugin and reloads it. "
                     "Local edits inside the plugin folder can block this.",
                     "Switch", lambda: self.run_action(p, "Switching…", lambda: backend.switch_version(p, tag),
                                                       f"{p.name} is now on {target}"),
                     on_cancel=cancel)

    # ---- actions
    def on_primary(self, p):
        if p.installed:
            if p.first_party and not p.clone_of:
                return
            detail = ("The plugin's files are deleted from ~/.config/omarchy/plugins. "
                      "If you might want it back, Disable it instead — that keeps the files, so enabling it "
                      "again needs no download.")
            if p.clone_of:
                detail += f"\n\nRemoving this clone switches back to the built-in {p.clone_of}."
            self.confirm(f"Remove {p.name}?", detail, "Remove",
                         lambda: self.run_action(p, "Removing…", lambda: backend.remove_plugin(p),
                                                 f"Removed {p.name}"), destructive=True)
        else:
            tag = p.selected_version
            body = (f"{p.repo}\n\n"
                    "Plugins are arbitrary, unsandboxed code that runs inside your long-lived omarchy-shell "
                    "process. Only add repositories you trust.\n\n"
                    + ("This listing is verified by the marketplace's automated checks (not a security audit).\n\n"
                       if p.verification == "verified" else
                       "This listing has NOT passed the marketplace's automated verification.\n\n")
                    + (f"Version: {tag}\n" if tag else "Version: latest\n")
                    + "Omarchy will clone it, validate it, and enable it.")
            extra, chosen = self.placement_picker(p) if p.is_bar_widget else (None, None)
            if extra is not None:
                body += "\nIt's a bar widget — choose where it goes on the bar."

            def go():
                section = chosen() if chosen else None
                self.run_action(p, "Adding…", lambda: backend.add_plugin(p, tag, section),
                                f"Added and enabled {p.name}")
            self.confirm(f"Add {p.name}?", body, "Add", go, extra=extra)

    def placement_picker(self, p):
        """A bar-position chooser for bar widgets. Returns (widget, getter -> 'left'|'center'|'right'|None)."""
        default = p.default_section if p.default_section in backend.SECTIONS else ""
        choices = [f"Plugin default ({default})" if default else "Plugin default", "Left", "Center", "Right"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cap = Gtk.Label(label="Bar position", xalign=0)
        cap.add_css_class("dim")
        drop = Gtk.DropDown.new_from_strings(choices)
        drop.add_css_class("version-drop")
        box.append(cap)
        box.append(drop)
        return box, lambda: (None, "left", "center", "right")[drop.get_selected()]

    def on_toggle(self, p):
        enable = not p.enabled
        if enable and p.is_bar_widget:
            extra, chosen = self.placement_picker(p)
            self.confirm(f"Enable {p.name}?", "It's a bar widget — choose where it goes on the bar.", "Enable",
                         lambda: self.run_action(p, "Enabling…", lambda s=chosen(): backend.set_enabled(p, True, s),
                                                 f"Enabled {p.name}"), extra=extra)
            return
        self.run_action(p, "Enabling…" if enable else "Disabling…", lambda: backend.set_enabled(p, enable),
                        f"{'Enabled' if enable else 'Disabled'} {p.name}")

    def run_action(self, p, busy, fn, success):
        if p.busy:
            return
        p.busy = busy
        p.notify_changed()

        def work():
            ok, msg = fn()
            local, err = backend.read_local()
            return ok, msg, local, err

        def done(res):
            p.busy = None
            if isinstance(res, Exception):
                p.notify_changed()
                self.alert("Something went wrong", str(res))
                return
            ok, msg, local, err = res
            self.local = local
            p.notify_changed()
            self.rebuild(err)
            if ok:
                self.toast(msg if msg.startswith("Already up to date") else success)
            else:
                self.alert(f"Couldn't finish: {p.name}", msg or "The command failed without any output.")
        bg(work, done)

    # ---- dialogs
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))

    def alert(self, heading, body):
        d = Adw.AlertDialog(heading=heading, body=body[-1500:])
        d.add_response("close", "Close")
        d.set_close_response("close")
        d.present(self)

    def confirm(self, heading, body, verb, on_ok, destructive=False, on_cancel=None, extra=None):
        d = Adw.AlertDialog(heading=heading, body=body)
        if extra is not None:
            d.set_extra_child(extra)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", verb)
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                                  else Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("cancel")
        d.set_close_response("cancel")

        def on_response(_d, response):
            if response == "ok":
                on_ok()
            elif on_cancel:
                on_cancel()
        d.connect("response", on_response)
        d.present(self)

    # ---- navigation
    def show_info(self, p):
        if self.info_page:
            self.stack.remove(self.info_page)
            self.info_page.teardown()
        self.info_page = InfoPage(self, p)
        self.stack.add_named(self.info_page, "info")
        self.stack.set_visible_child_name("info")

    def go_back(self):
        # The list page (and the tab it was on, scroll position, search text) was never torn down.
        self.stack.set_visible_child_name("list")
        page, self.info_page = self.info_page, None
        if page:
            page.teardown()
            GLib.timeout_add(250, lambda: (self.stack.remove(page), False)[1])


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main():
    """`--sandbox` (or OMARCHY_PLUGINS_SANDBOX=1): real data, simulated changes. See README."""
    argv = [a for a in sys.argv if a != "--sandbox"]
    if len(argv) != len(sys.argv) or os.environ.get("OMARCHY_PLUGINS_SANDBOX"):
        backend.enable_sandbox()
    return App().run(argv)
