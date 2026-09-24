"""Data + actions for the Omarchy plugin browser.

Sources of truth (nothing here is hard-coded plugin data):

  * Community + built-in catalog: https://plugins.omarchy.org/catalog.json
    (the Omarchy Plugin Marketplace, github.com/omacom/omarchy-plugin-marketplace)
  * What is on this machine: `omarchy-plugin-list --json` (enabled state) and
    `omarchy-plugin-catalog` (manifest locations) -> each plugin's manifest.json
  * Versions: git tags of the plugin's repository
  * Developer info: GitHub's public REST API (anonymous, cached)

Every change goes through Omarchy's own `omarchy-plugin-*` commands, so the app
behaves exactly like Setup > Plugins does.
"""

import atexit
import gzip
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from gi.repository import GObject

OMARCHY_PATH = os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")
CATALOG_URL = "https://plugins.omarchy.org/catalog.json"
MARKETPLACE_URL = "https://plugins.omarchy.org"
MANUAL_URL = "https://omarchy.org/manual/shell-plugins/"
BUILTIN_REPO = "https://github.com/omacom/omarchy"
BUILTIN_BRANCH = "quattro"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "omarchy-plugins"
PLUGINS_DIR = Path.home() / ".config/omarchy/plugins"
USER_AGENT = "omarchy-plugins/1.0 (+https://plugins.omarchy.org)"

TAGS_TTL = 12 * 3600
GITHUB_TTL = 6 * 3600
IMAGE_TTL = 7 * 24 * 3600


# --------------------------------------------------------------------------- helpers

def _env():
    env = dict(os.environ)
    env.setdefault("OMARCHY_PATH", OMARCHY_PATH)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    extra = [str(Path.home() / ".local/share/omarchy/bin"), f"{OMARCHY_PATH}/bin"]
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


# --------------------------------------------------------------------------- sandbox mode
# `omarchy-plugins --sandbox`: the real catalog and the real installed list, but every change is
# simulated in memory. Two layers keep it harmless: the action functions below are replaced by
# simulations, AND run() refuses any command that isn't on a read-only allow-list.

SANDBOX = False
SIM = {"added": {}, "removed": set(), "enabled": {}, "patch": {}, "updatable": set()}
_SIM_PREFIX = "/sandbox/"
_RO_CMDS = {"omarchy-plugin-list", "omarchy-plugin-catalog", "pgrep"}
_RO_GIT = {"ls-remote", "rev-parse", "describe", "symbolic-ref"}


def enable_sandbox():
    """Switch to sandbox mode: scratch cache (deleted on exit), simulated actions, read-only commands."""
    global SANDBOX, CACHE_DIR
    SANDBOX = True
    tmp = tempfile.mkdtemp(prefix="omarchy-plugins-sandbox-")
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    CACHE_DIR = Path(tmp)


def _sandbox_allows(args):
    if args[0] in _RO_CMDS:
        return True
    if args[0] == "git":
        rest = args[1:]
        if rest[:1] == ["-C"]:
            rest = rest[2:]
        if not rest:
            return False
        if rest[0] in _RO_GIT:
            return True
        if rest[0] == "tag":
            return rest[1:] == ["--list"]
        if rest[0] == "remote":
            return rest[1:2] == ["get-url"]
    return False


def _sim_delay():
    time.sleep(0.9)  # long enough to see the "Adding…" state


def _sim_id(path):
    return path[len(_SIM_PREFIX):] if path.startswith(_SIM_PREFIX) else ""


def _sim_head():
    return f"sim{int(time.time() * 1000) % 10**7:07d}"


def run(args, timeout=60):
    """Run a command with no tty and no stdin. Returns (returncode, combined output)."""
    if SANDBOX and not _sandbox_allows(args):
        return 126, f"Blocked: sandbox mode never changes anything ({' '.join(args[:2])})."
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=_env())
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"Timed out after {timeout}s: {' '.join(args[:3])}"
    except OSError as e:
        return 127, f"Could not run {args[0]}: {e}"


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def omarchy_version():
    try:
        return Path(OMARCHY_PATH, "version").read_text().strip()
    except OSError:
        return ""


def normalize_repo(url):
    """Canonical https form so a git remote can be matched to a catalog entry."""
    if not url:
        return ""
    u = url.strip()
    m = re.match(r"^git@([^:]+):(.+)$", u)
    if m:
        u = f"https://{m.group(1)}/{m.group(2)}"
    u = re.sub(r"^ssh://git@", "https://", u)
    u = re.sub(r"\.git$", "", u.rstrip("/"))
    return u.lower()


def repo_parts(url):
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return (m.group(1), m.group(2)) if m else None


def version_key(tag):
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)(.*)$", tag)
    if not m:
        return ((), 0, tag)
    nums = tuple(int(x) for x in m.group(1).split("."))
    suffix = m.group(2)
    return (nums, 1 if suffix == "" else 0, suffix)  # 1.0.0 outranks 1.0.0-beta


def sort_tags(tags):
    return sorted(set(tags), key=version_key, reverse=True)


def fmt_date(iso):
    return (iso or "")[:10]


def _asset_url(path):
    if not path:
        return ""
    return path if path.startswith("http") else f"{MARKETPLACE_URL}/{path}"


def fetch_image(url):
    """Download (and cache) a marketplace preview image. Returns a local file path, or None."""
    if not url:
        return None
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    ext = Path(url.split("?", 1)[0]).suffix or ".img"
    path = CACHE_DIR / "images" / f"{digest}{ext}"
    if path.exists() and time.time() - path.stat().st_mtime < IMAGE_TTL:
        return str(path)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
    except (urllib.error.URLError, OSError):
        return str(path) if path.exists() else None  # serve a stale copy rather than nothing
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return str(path)


# --------------------------------------------------------------------------- Plugin model

class Plugin(GObject.Object):
    """One row: merged marketplace + local information. Emits `changed` on state change."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, pid):
        super().__init__()
        self.id = pid
        self.local_id = pid          # id used with omarchy-plugin-* (differs if matched by repo)
        self.name = pid
        self.description = ""
        self.author = ""
        self.version = ""            # latest, per the marketplace
        self.category = ""
        self.kind_label = ""
        self.tags = []
        self.repo = ""
        self.source = "community"    # builtin | community | local
        self.verification = None     # verified | unverified | None
        self.verified_commit = ""
        self.install_available = None
        self.install_note = ""
        self.install_command = ""
        self.license = ""
        self.stars = 0
        self.updated_at = ""
        self.added_at = ""
        self.release_tag = ""
        self.release_url = ""
        self.has_release_hint = False
        self.source_url = ""         # built-ins: folder inside the omarchy repo
        self.preview_images = []     # [{"full", "thumb", "w", "h"}, ...] from the marketplace listing
        # local state
        self.installed = False
        self.enabled = False
        self.can_disable = True
        self.first_party = False
        self.clone_of = ""
        self.installed_dir = ""
        self.installed_version = ""
        self.installed_tag = ""
        self.local_tags = []
        self.kinds = []
        # versions
        self.remote_tags = []
        self.tags_state = "none"     # none | loading | loaded
        self.selected_version = None  # tag chosen before adding; None = latest
        self.busy = None
        # updates (git-managed third-party plugins only)
        self.update_state = "unknown"   # unknown | checking | available | current | pinned | n/a
        self.update_checked = 0.0
        self.head = ""                  # installed commit (short)
        self.on_branch = True
        self.manifest_path = ""
        self.default_section = ""       # bar widgets: where Omarchy puts them by default
        self.blob = ""
        self.sort_key = (1, 0, "")

    # -- catalog / local merging
    def apply_catalog(self, e):
        rel = e.get("repositoryRelease") or {}
        self.name = e.get("name") or self.id
        self.description = e.get("description") or ""
        self.author = e.get("author") or ""
        self.version = e.get("version") or ""
        self.category = e.get("category") or ""
        self.kind_label = e.get("kind") or ""
        self.tags = list(e.get("tags") or [])
        self.repo = e.get("repo") or ""
        self.source = "builtin" if e.get("sourceType") == "builtin" else "community"
        self.verification = e.get("verificationStatus")
        self.verified_commit = e.get("verificationCommit") or ""
        self.install_available = e.get("installAvailable")
        self.install_note = e.get("installNote") or ""
        self.install_command = e.get("installCommand") or ""
        self.license = e.get("license") or ""
        self.stars = e.get("stars") or 0
        self.updated_at = e.get("repositoryUpdatedAt") or ""
        self.added_at = e.get("addedAt") or ""
        self.release_tag = rel.get("tag") or ""
        self.release_url = rel.get("url") or ""
        self.has_release_hint = bool(rel)
        img = e.get("previewImage")
        self.preview_images = [{
            "full": _asset_url(img),
            "thumb": _asset_url(e.get("previewThumbnail") or img),
            "w": e.get("previewWidth") or e.get("previewSourceWidth") or 1600,
            "h": e.get("previewHeight") or e.get("previewSourceHeight") or 900,
        }] if img else []
        if self.source == "builtin":
            self.first_party = True
            mp = (e.get("manifestPath") or "").rsplit("/", 1)[0]
            self.source_url = f"{BUILTIN_REPO}/tree/{BUILTIN_BRANCH}/shell/plugins/{mp}" if mp else \
                f"{BUILTIN_REPO}/tree/{BUILTIN_BRANCH}/shell/plugins"
        self.refresh_derived()

    def refresh_derived(self):
        self.blob = " ".join([self.id, self.local_id, self.name, self.description, self.author,
                              self.category, self.kind_label, *self.tags]).lower()
        self.sort_key = (0 if self.installed else 1, -(self.stars or 0), (self.name or self.id).lower())

    def snapshot(self):
        return (self.installed, self.enabled, self.can_disable, self.installed_version, self.installed_tag,
                tuple(self.local_tags), tuple(self.remote_tags), self.tags_state, self.version, self.busy,
                self.local_id, self.name, self.update_state, self.head)

    def notify_changed(self):
        self.refresh_derived()
        self.emit("changed")

    # -- versions
    def versions(self):
        """[(label, tag|None)]; the first entry is always 'latest' (tag None)."""
        latest = f"{self.version} (latest)" if self.version else "latest"
        tags = sort_tags([*self.remote_tags, *self.local_tags])
        return [(latest, None)] + [(t, t) for t in tags if not t.startswith("-")]

    def selected_index(self):
        tag = self.installed_tag if self.installed else self.selected_version
        if tag:
            for i, (_label, t) in enumerate(self.versions()):
                if t == tag:
                    return i
        return 0

    @property
    def shown_version(self):
        return (self.installed_version if self.installed else self.version) or self.version or "—"

    @property
    def update_available(self):
        return self.installed and self.update_state == "available"

    @property
    def is_bar_widget(self):
        """True if enabling this puts a widget on the bar (so a bar position can be chosen)."""
        if self.installed:
            return "bar-widget" in self.kinds and "bar" not in self.kinds
        label = (self.kind_label or "").lower()
        return "bar widget" in label

    @property
    def marketplace_url(self):
        return f"{MARKETPLACE_URL}/plugin.html?id={quote(self.id)}" if self.source == "community" else ""

    @property
    def clone_command(self):
        if self.install_command:
            return self.install_command
        if self.repo and self.source == "community" and self.install_available is not False:
            return f"omarchy plugin add {self.repo}.git --enable"
        return ""


# --------------------------------------------------------------------------- catalog

def load_cached_catalog():
    data = read_json(CACHE_DIR / "catalog.json")
    return data.get("plugins") if isinstance(data, dict) else None


def fetch_catalog(force=False):
    """Returns (entries|None, status). None + 'current' means the cache is up to date."""
    cache, meta = CACHE_DIR / "catalog.json", CACHE_DIR / "catalog.meta.json"
    req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    etag = (read_json(meta) or {}).get("etag")
    if etag and cache.exists() and not force:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            data = json.loads(raw)
            if not isinstance(data.get("plugins"), list):
                raise ValueError("unexpected catalog format")
            new_etag = r.headers.get("ETag")
        write_json(cache, data)
        write_json(meta, {"etag": new_etag, "fetched": time.time(), "generatedAt": data.get("generatedAt")})
        return data["plugins"], "updated"
    except urllib.error.HTTPError as e:
        if e.code == 304:
            write_json(meta, {**(read_json(meta) or {}), "fetched": time.time()})
            return None, "current"
        return None, f"marketplace unreachable (HTTP {e.code})"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"offline ({getattr(e, 'reason', e)})"


def catalog_age():
    fetched = (read_json(CACHE_DIR / "catalog.meta.json") or {}).get("fetched")
    return time.time() - fetched if fetched else None


# --------------------------------------------------------------------------- local state

def git_local(path):
    d = str(path)
    empty = {"remote": "", "tags": [], "exact_tag": "", "head": "", "on_branch": True}
    if not Path(d, ".git").exists():
        return empty
    _, remote = run(["git", "-C", d, "remote", "get-url", "origin"], 10)
    rc, tags = run(["git", "-C", d, "tag", "--list"], 10)
    rc2, exact = run(["git", "-C", d, "describe", "--tags", "--exact-match", "HEAD"], 10)
    _, head = run(["git", "-C", d, "rev-parse", "--short", "HEAD"], 10)
    rc3, _ = run(["git", "-C", d, "symbolic-ref", "-q", "HEAD"], 10)
    return {"remote": remote if "\n" not in remote and " " not in remote else "",
            "tags": tags.split() if rc == 0 else [],
            "exact_tag": exact if rc2 == 0 else "",
            "head": head if rc3 == 0 or head else "", "on_branch": rc3 == 0}


def read_local():
    """What Omarchy reports as installed (plus, in sandbox mode, the simulated changes on top)."""
    local, err = _read_local_real()
    if not SANDBOX:
        return local, err
    for pid in SIM["removed"]:
        local.pop(pid, None)
    for pid, entry in SIM["added"].items():
        local[pid] = dict(entry)
    for pid, enabled in SIM["enabled"].items():
        if pid in local:
            local[pid]["enabled"] = enabled
    for pid, patch in SIM["patch"].items():
        if pid in local:
            local[pid].update(patch)
    return local, err


def _read_local_real():
    """What Omarchy reports as installed. Returns (dict id -> info, error|None)."""
    rc, out = run(["omarchy-plugin-list", "--json"], 15)
    try:
        listed = json.loads(out) if rc == 0 else None
    except ValueError:
        listed = None
    if not isinstance(listed, list):
        return {}, "Couldn't read installed plugins — is omarchy-shell running?"

    dirs = {}
    rc, out = run(["omarchy-plugin-catalog"], 15)
    try:
        for m in (json.loads(out) if rc == 0 else []):
            dirs[m["id"]] = m
    except (ValueError, KeyError, TypeError):
        pass

    local = {}
    for item in listed:
        pid = item.get("id")
        if not pid:
            continue
        info = dirs.get(pid, {})
        manifest = read_json(info.get("manifestPath", ""), {}) or {}
        src = info.get("sourceDir", "")
        git = git_local(src) if not item.get("firstParty") and src else \
            {"remote": "", "tags": [], "exact_tag": "", "head": "", "on_branch": True}
        local[pid] = {
            "id": pid,
            "name": item.get("name") or manifest.get("name") or pid,
            "kinds": item.get("kinds") or manifest.get("kinds") or [],
            "enabled": bool(item.get("enabled")),
            "can_disable": bool(item.get("canDisable", True)),
            "first_party": bool(item.get("firstParty")),
            "clone_of": item.get("clonedFrom") or "",
            "dir": src,
            "version": manifest.get("version") or "",
            "author": manifest.get("author") or "",
            "description": manifest.get("description") or "",
            "remote": git["remote"],
            "tags": git["tags"],
            "exact_tag": git["exact_tag"],
            "head": git["head"],
            "on_branch": git["on_branch"],
            "manifest_path": info.get("manifestPath", ""),
            "default_section": ((manifest.get("barWidget") or {}).get("defaultSection")) or "",
        }
    return local, None


def merge(plugins, catalog_entries, local):
    """Fold catalog + local state into `plugins` (id -> Plugin). Returns (new Plugins, changed Plugins)."""
    before = {pid: p.snapshot() for pid, p in plugins.items()}
    new = []

    for e in catalog_entries or []:
        pid = e.get("id")
        if not pid:
            continue
        p = plugins.get(pid)
        if p is None:
            p = plugins[pid] = Plugin(pid)
            new.append(p)
        p.apply_catalog(e)

    by_repo = {normalize_repo(p.repo): p for p in plugins.values() if p.repo}
    matched = set()
    for lid, l in (local or {}).items():
        p = plugins.get(lid)
        if p is None and l["remote"]:
            p = by_repo.get(normalize_repo(l["remote"]))
            if p is not None and p.id in matched:
                p = None
        if p is None:
            p = plugins[lid] = Plugin(lid)
            new.append(p)
            p.name, p.description, p.author = l["name"], l["description"], l["author"]
            p.version = l["version"]
            p.source = "builtin" if l["first_party"] else "local"
            if l["first_party"]:
                p.repo = BUILTIN_REPO
            elif l["remote"]:
                p.repo = re.sub(r"\.git$", "", l["remote"]) if l["remote"].startswith("http") else ""
        matched.add(p.id)
        p.local_id = lid
        p.installed, p.enabled = True, l["enabled"]
        p.can_disable, p.first_party = l["can_disable"], l["first_party"] or p.first_party
        p.clone_of, p.installed_dir = l["clone_of"], l["dir"]
        p.installed_version, p.local_tags, p.installed_tag = l["version"], l["tags"], l["exact_tag"]
        head, on_branch = l.get("head", ""), l.get("on_branch", True)
        if head != p.head:  # installed or updated (possibly by another process): re-check
            p.update_state, p.update_checked = "unknown", 0.0
        p.head, p.on_branch = head, on_branch
        p.manifest_path, p.default_section = l.get("manifest_path", ""), l.get("default_section", "")
        if not head or l["first_party"]:
            p.update_state = "n/a"   # built-ins update with Omarchy itself; hand-made folders have no upstream
        elif not on_branch:
            p.update_state = "pinned"  # deliberately on a tag / commit: never nag
        elif p.update_state in ("n/a", "pinned"):
            p.update_state = "unknown"
        p.kinds = l["kinds"]
        if not p.kind_label and l["kinds"]:
            p.kind_label = " + ".join(k.replace("-", " ").capitalize() for k in l["kinds"])
        if p.first_party and l["dir"] and not p.source_url:
            rel = os.path.relpath(l["dir"], f"{OMARCHY_PATH}/shell/plugins")
            if not rel.startswith(".."):
                p.source_url = f"{BUILTIN_REPO}/tree/{BUILTIN_BRANCH}/shell/plugins/{rel}"

    for pid, p in plugins.items():
        if p.id not in matched and p.installed:  # was installed, no longer reported
            p.installed = p.enabled = False
            p.installed_dir = p.installed_version = p.installed_tag = ""
            p.local_tags, p.local_id, p.clone_of = [], p.id, ""
            p.update_state, p.head = "unknown", ""
        p.refresh_derived()

    changed = [p for pid, p in plugins.items() if pid in before and p.snapshot() != before[pid]]
    return new, changed


# --------------------------------------------------------------------------- tags

_tag_lock = threading.Lock()
_tag_cache = None


def remote_tags(repo, force=False):
    global _tag_cache
    with _tag_lock:
        if _tag_cache is None:
            _tag_cache = read_json(CACHE_DIR / "tags.json", {}) or {}
        hit = _tag_cache.get(repo)
    if hit and not force and time.time() - hit["t"] < TAGS_TTL:
        return hit["tags"]
    rc, out = run(["git", "ls-remote", "--tags", "--refs", repo + ".git"], 30)
    if rc != 0:
        return hit["tags"] if hit else []
    tags = sort_tags(m.group(1) for m in re.finditer(r"refs/tags/(\S+)", out))
    with _tag_lock:
        _tag_cache[repo] = {"t": time.time(), "tags": tags}
        write_json(CACHE_DIR / "tags.json", _tag_cache)
    return tags


class TagWorkers:
    """A few daemon threads that fetch tag lists without ever blocking the UI or app exit."""

    def __init__(self, n=3):
        self.q = queue.Queue()
        for _ in range(n):
            threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, fn, done):
        self.q.put((fn, done))

    def _loop(self):
        while True:
            fn, done = self.q.get()
            try:
                done(fn())
            except Exception:  # noqa: BLE001 — a failed lookup must never kill the worker
                done([])


# --------------------------------------------------------------------------- developer info

def github_info(repo_url):
    """Anonymous GitHub lookup for the repo + its owner. Returns dict; 'error' set on failure."""
    parts = repo_parts(repo_url)
    if not parts:
        return {"error": "not a GitHub repository"}
    owner, name = parts
    cache_path = CACHE_DIR / "github.json"
    cache = read_json(cache_path, {}) or {}
    hit = cache.get(f"{owner}/{name}".lower())
    if hit and time.time() - hit["t"] < GITHUB_TTL:
        return hit["data"]

    def get(url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    data = {"repo": {}, "user": {}, "error": None}
    try:
        data["repo"] = get(f"https://api.github.com/repos/{owner}/{name}")
        data["user"] = get(f"https://api.github.com/users/{owner}")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            data["error"] = "GitHub's anonymous rate limit was reached — showing marketplace data only. Try again later."
        elif e.code == 404:
            data["error"] = "Repository not found on GitHub (it may have been renamed or removed)."
        else:
            data["error"] = f"GitHub returned HTTP {e.code}."
    except (urllib.error.URLError, OSError, ValueError) as e:
        data["error"] = f"Couldn't reach GitHub ({getattr(e, 'reason', e)})."
    if not data["error"]:
        cache[f"{owner}/{name}".lower()] = {"t": time.time(), "data": data}
        write_json(cache_path, cache)
    elif hit:
        return hit["data"]
    return data


# --------------------------------------------------------------------------- actions
# Each returns (ok, message). They run on worker threads, never the UI thread.

def _rescan():
    run(["omarchy-shell", "shell", "rescanPlugins"], 15)


def _default_branch(path):
    rc, out = run(["git", "-C", path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], 10)
    return out.split("/", 1)[1] if rc == 0 and "/" in out else "main"


def _current_ref(path):
    rc, out = run(["git", "-C", path, "symbolic-ref", "-q", "--short", "HEAD"], 10)
    if rc == 0 and out:
        return out
    return run(["git", "-C", path, "rev-parse", "HEAD"], 10)[1]


def _checkout_tag(path, tag):
    """Check out a tag, validate the manifest, roll back if it doesn't validate."""
    if not tag or tag.startswith("-"):
        return False, f"Invalid version: {tag!r}"
    previous = _current_ref(path)
    run(["git", "-C", path, "fetch", "--quiet", "--tags", "origin"], 60)  # best effort; tag may be local
    rc, out = run(["git", "-C", path, "checkout", "--quiet", "--detach", f"refs/tags/{tag}"], 60)
    if rc != 0:
        return False, out or f"Couldn't check out {tag}."
    rc, out = run(["omarchy-plugin-validate", path], 20)
    if rc != 0:
        run(["git", "-C", path, "checkout", "--quiet", previous], 30)
        return False, f"Version {tag} failed Omarchy's plugin validation and was rolled back.\n{out}"
    return True, ""


SECTIONS = ("left", "center", "right")


def _placement(p, section):
    """`--section X` for bar widgets only; Omarchy refuses a position for a full bar."""
    return ["--section", section] if section in SECTIONS and p.is_bar_widget else []


def _sim_kinds(p):
    label = (p.kind_label or "").lower()
    return [k.strip().replace(" ", "-") for k in label.split("+") if k.strip()] or ["plugin"]


def _sim_add(p, tag, section):
    _sim_delay()
    SIM["removed"].discard(p.id)
    SIM["added"][p.id] = {
        "id": p.id, "name": p.name, "kinds": _sim_kinds(p), "enabled": True, "can_disable": True,
        "first_party": False, "clone_of": "", "dir": _SIM_PREFIX + p.id,
        "version": (tag or "").lstrip("vV") or p.version, "author": p.author, "description": p.description,
        "remote": p.repo, "tags": list(p.remote_tags), "exact_tag": tag or "", "head": _sim_head(),
        "on_branch": tag is None, "manifest_path": "", "default_section": section or "",
    }
    if tag is None:
        SIM["updatable"].add(p.id)  # gives the demo an update to try
    where = f" on the bar ({section})" if section and p.is_bar_widget else ""
    return True, f"[sandbox] Simulated add of {p.id}{where}. Nothing was installed."


def add_plugin(p, tag=None, section=None):
    if SANDBOX:
        return _sim_add(p, tag, section)
    if not p.repo:
        return False, "This plugin has no repository URL."
    url = p.repo + ".git"
    if tag is None and section is None:
        # Exactly the command the marketplace shows for every plugin.
        rc, out = run(["omarchy-plugin-add", url, "--enable", "--yes"], 300)
        return rc == 0, out
    # Add without enabling, so a version can be pinned and a bar position chosen first.
    rc, out = run(["omarchy-plugin-add", url, "--yes"], 300)
    if rc != 0:
        return False, out
    m = re.search(r"Added (\S+) into (.+)", out)
    pid, path = (m.group(1), m.group(2).strip()) if m else (p.id, "")
    if tag:
        if not path:
            return False, "Added, but couldn't find where the plugin was installed to pin its version."
        ok, msg = _checkout_tag(path, tag)
        if not ok:
            run(["omarchy-plugin-remove", pid, "--yes"], 60)
            return False, f"{msg}\nThe plugin was not kept."
        _rescan()
    rc, out = run(["omarchy-plugin-enable", pid, *_placement(p, section)], 30)
    return rc == 0, out


def remove_plugin(p):
    if SANDBOX:
        _sim_delay()
        SIM["added"].pop(p.local_id, None)
        SIM["updatable"].discard(p.local_id)
        if not p.local_id.startswith("omarchy."):
            SIM["removed"].add(p.local_id)
        return True, "[sandbox] Simulated remove. Nothing was deleted."
    rc, out = run(["omarchy-plugin-remove", p.local_id, "--yes"], 120)
    return rc == 0, out


def set_enabled(p, enabled, section=None):
    if SANDBOX:
        _sim_delay()
        SIM["enabled"][p.local_id] = bool(enabled)
        return True, "[sandbox] Simulated. Nothing was changed."
    if not enabled:
        rc, out = run(["omarchy-plugin-disable", p.local_id], 30)
    else:
        rc, out = run(["omarchy-plugin-enable", p.local_id, *_placement(p, section)], 30)
    return rc == 0, out


def clone_plugin(p):
    """Copy a built-in plugin into ~/.config/omarchy/plugins/<user>.<name> and switch to the copy.

    Backend only (no button): the same as `omarchy plugin clone <id>`, kept here so scripts and AI
    agents can make quick clones through this module too."""
    if not p.first_party or p.clone_of:
        return False, "Only built-in plugins can be cloned."
    if SANDBOX:
        return False, "Cloning is disabled in sandbox mode."
    rc, out = run(["omarchy-plugin-clone", p.local_id], 60)
    return rc == 0, out


# --------------------------------------------------------------------------- updates
# Omarchy has no background updater: `omarchy plugin update` is the only way plugins change, and
# anyone (the user in a terminal, an AI agent) can run it at any time. So this app:
#   * detects updates read-only (git ls-remote; nothing local is written),
#   * only touches the plugin folder when the user asks, and applies with Omarchy's own command,
#   * re-checks immediately before applying, and steps aside if a git/plugin operation is running.

def repo_busy(path):
    """Reason string if something else is changing plugins right now, else ''."""
    if SANDBOX and path.startswith(_SIM_PREFIX):
        return ""
    if Path(path, ".git", "index.lock").exists():
        return "Git is busy in this plugin's folder — try again in a moment."
    rc, _ = run(["pgrep", "-f", r"omarchy-plugin-(update|add|remove|clone)"], 5)
    if rc == 0:
        return "Another plugin add/update/remove is already running — try again when it finishes."
    return ""


def check_update(path):
    """Read-only. Returns (state, remote_sha): state is available | current | unknown."""
    if SANDBOX and path.startswith(_SIM_PREFIX):
        return ("available" if _sim_id(path) in SIM["updatable"] else "current"), "sim"
    rc, local = run(["git", "-C", path, "rev-parse", "HEAD"], 10)
    if rc != 0:
        return "unknown", ""
    rc, out = run(["git", "-C", path, "ls-remote", "origin", "HEAD"], 30)
    if rc != 0 or not out.strip():
        return "unknown", ""
    remote = out.split()[0]
    return ("current" if remote == local else "available"), remote


def update_preview(p):
    """What updating would change. Fetches into the plugin's own repo (the first thing
    `omarchy plugin update` does) but changes nothing else. Returns a dict for the UI."""
    path = p.installed_dir
    if SANDBOX and path.startswith(_SIM_PREFIX):
        return {
            "state": "available", "current": "a1b2c3d", "new": "e4f5a6b", "new_version": p.version,
            "old_version": p.installed_version, "can_ff": True, "count": 3,
            "stat": "4 files changed, 61 insertions(+), 9 deletions(-)",
            "commits": [("e4f5a6b", "Sample Dev", "2026-09-20", "Fix crash when the network drops mid-refresh"),
                        ("9c8d7e6", "Sample Dev", "2026-09-18", "Add a compact option to the widget"),
                        ("5b4a3c2", "Sample Dev", "2026-09-15", "Bump manifest version")],
            "files": ["Widget.qml", "manifest.json", "README.md", "assets/icon.svg"], "n_files": 4,
            "touches_code": True, "compare_url": p.repo + "/commits" if p.repo else "",
        }
    if not path or not Path(path, ".git").exists():
        return {"error": "This plugin isn't a git checkout, so there's nothing to update from."}
    busy = repo_busy(path)
    if busy:
        return {"error": busy}
    rc, out = run(["git", "-C", path, "fetch", "--quiet", "origin", "HEAD"], 90)
    if rc != 0:
        return {"error": out or "Couldn't reach the plugin's repository."}

    def git(*args, timeout=20):
        return run(["git", "-C", path, *args], timeout)

    _, cur = git("rev-parse", "--short", "HEAD")
    _, new = git("rev-parse", "--short", "FETCH_HEAD")
    if cur == new:
        return {"state": "current", "current": cur}
    can_ff = git("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")[0] == 0
    _, log = git("log", "--format=%h%x09%an%x09%ad%x09%s", "--date=short", "-n", "40", "HEAD..FETCH_HEAD")
    commits = [tuple(line.split("\t", 3)) for line in log.splitlines() if line.count("\t") == 3]
    _, count = git("rev-list", "--count", "HEAD..FETCH_HEAD")
    _, stat = git("diff", "--shortstat", "HEAD", "FETCH_HEAD")
    _, names = git("diff", "--name-only", "HEAD", "FETCH_HEAD")
    files = names.splitlines()
    code = [f for f in files if f.endswith((".qml", ".js", ".mjs", ".sh", ".py")) or f.endswith("manifest.json")]
    new_version = ""
    rc, manifest = git("show", "FETCH_HEAD:manifest.json")
    if rc == 0:
        try:
            new_version = json.loads(manifest).get("version") or ""
        except ValueError:
            pass
    _, full_new = git("rev-parse", "FETCH_HEAD")
    _, full_cur = git("rev-parse", "HEAD")
    parts = repo_parts(p.repo)
    return {
        "state": "available", "current": cur, "new": new, "new_version": new_version,
        "old_version": p.installed_version, "can_ff": can_ff, "commits": commits,
        "count": int(count) if count.isdigit() else len(commits), "stat": stat, "files": files[:12],
        "n_files": len(files), "touches_code": bool(code),
        "compare_url": f"https://github.com/{parts[0]}/{parts[1]}/compare/{full_cur}...{full_new}" if parts else "",
    }


def apply_update(p):
    """Apply with Omarchy's own updater (fetch, fast-forward, validate, roll back on failure)."""
    path = p.installed_dir
    if SANDBOX and path.startswith(_SIM_PREFIX):
        _sim_delay()
        SIM["updatable"].discard(p.local_id)
        SIM["patch"].setdefault(p.local_id, {})["head"] = _sim_head()
        return True, "[sandbox] Simulated update. Nothing was changed."
    busy = repo_busy(path) if path else ""
    if busy:
        return False, busy
    state, _ = check_update(path)  # someone else may have updated since the window last looked
    if state == "current":
        return True, "Already up to date."
    rc, out = run(["omarchy-plugin-update", p.local_id, "--yes"], 180)
    return rc == 0, out


def switch_version(p, tag):
    """Move an installed git plugin to a tag, or back to the latest (default branch)."""
    path = p.installed_dir
    if SANDBOX and path.startswith(_SIM_PREFIX):
        _sim_delay()
        patch = SIM["patch"].setdefault(p.local_id, {})
        patch.update({"exact_tag": tag or "", "on_branch": tag is None, "head": _sim_head()})
        if tag:
            patch["version"] = tag.lstrip("vV")
        return True, "[sandbox] Simulated version change. Nothing was changed."
    if not path or not Path(path, ".git").exists():
        return False, "This plugin isn't a git checkout, so its version can't be changed."
    busy = repo_busy(path)
    if busy:
        return False, busy
    if tag:
        ok, msg = _checkout_tag(path, tag)
        if ok:
            _rescan()
        return ok, msg
    branch = _default_branch(path)
    rc, out = run(["git", "-C", path, "checkout", "--quiet", branch], 30)
    if rc != 0:
        return False, out
    rc, out = run(["omarchy-plugin-update", p.local_id, "--yes"], 120)
    return rc == 0, out
