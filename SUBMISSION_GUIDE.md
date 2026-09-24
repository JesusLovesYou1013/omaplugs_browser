# Getting OmaPlugs Browser listed on plugins.omarchy.org

Step-by-step, in order. Based on `SUBMISSION.md` and the develop guide at
`github.com/omacom/omarchy-plugin-marketplace` and `plugins.omarchy.org` (checked 2026-09-23).
This is a manual review process — there is no automatic scraping or discovery. Nothing here
has been submitted yet.

## 0. What's already done

- `manifest.json` + `BarWidget.qml` at the repo root: a `bar-widget` plugin that adds a bar
  icon opening this same app. Passes `omarchy-plugin-validate` cleanly.
- `LICENSE`: GPL-3.0 (your choice, 2026-09-23).
- Category: **System** (your choice, 2026-09-23) — must be typed exactly like that on the
  submission issue.
- ~~Placeholder `__GITHUB_USERNAME__`~~ — **done, 2026-09-23.** Username confirmed as a real,
  existing GitHub account (`github.com/JesusLovesYou1013`) before baking it in, since the ID
  is permanent once submitted. Plugin ID is now `io.github.JesusLovesYou1013.omaplugs-browser`
  everywhere (manifest `id`/`author`, `BarWidget.qml`'s `moduleName` and launch path).
  Re-ran `omarchy-plugin-validate` after the substitution — still passes cleanly.

## 1. ~~Create a GitHub account~~ — done

Account confirmed: `github.com/JesusLovesYou1013`.

## 2. Create the GitHub repo and push

    cd "~/Work/OmaPlugs-Plugin Browser"
    git init
    git add .
    git commit -m "Initial commit"

Then create a new **public** repo on GitHub (via the website, or `gh repo create` if you have
the `gh` CLI set up) and push this checkout to it. The repo's root must directly contain
`manifest.json` — it does, already.

## 3. Verify the manifest one more time against the pushed repo

    omarchy-plugin-validate "~/Work/OmaPlugs-Plugin Browser"

Should print nothing and exit 0 (already confirmed passing as of 2026-09-23, including after
the username substitution — re-confirm once more against the actual pushed repo state).

## 4. Test it for real, as a marketplace user would install it

This is the same install path anyone else will use — worth doing once before submitting.

    omarchy plugin add https://github.com/JesusLovesYou1013/omaplugs_browser.git --enable

This is a real, visible change to your desktop (adds a bar icon) — ask before doing this step
if Claude is driving it. Then, per the official dev-guide checklist, actually click through:
- Click the new bar icon — confirm the OmaPlugs Browser window opens.
- Press Escape / close it.
- Open and close it again.
- Disable the plugin (Setup › Plugins › Disable Plugin), confirm the icon disappears.
- Re-enable it, confirm it comes back.
- Restart the Omarchy shell (or log out/in), confirm it survives.
- Remove it (Setup › Plugins › Remove Plugin) — confirm the bar icon and
  `~/.config/omarchy/plugins/io.github.JesusLovesYou1013.omaplugs-browser/` are both gone.

If anything misbehaves, fix it and re-push before submitting — the review is against an
"exact-commit scan," so whatever commit is `main` when you submit is what gets reviewed.

## 5. Open the submission issue

Go to: https://github.com/omacom/omarchy-plugin-marketplace/issues/new/choose

Pick the plugin-submission template (look for one labeled for plugin submissions — exact
template name wasn't confirmed by this guide's research). If no template picker appears, open
a plain issue.

**Title, exactly this format:**

    [Plugin]: OmaPlugs Browser

**Body — six sections, in this order:**

1. **Repository URL** — `https://github.com/JesusLovesYou1013/omaplugs_browser`
2. **Category** — `System`
3. **Tags** — pick 1–3 from: `ai, bar, education, games, hyprland, kids, launcher, media,
   power-management, quickshell, security, system, vpn, workspaces`. Best fits here are
   probably `system` and `quickshell` — your call, pick what feels right, 1–3 only.
4. **Suggest a missing tag** — optional; skip or suggest something like `plugin-manager` if
   you think that's missing from the list.
5. **Maintainer notes** — free text. Worth mentioning: this bundles a Python/GTK4/libadwaita
   app launched from the bar-widget's entry point, uses Omarchy's own `omarchy-plugin-*`
   commands for every action, and has a `--sandbox` mode reviewers can try with zero risk.
6. **Submission checklist** — all five must be checked, and must actually be true:
   - [ ] The repository is public and contains installation and removal instructions.
     *(True — README's "Install from the marketplace" and "Install / uninstall" sections.)*
   - [ ] I have documented the plugin license and any external dependencies.
     *(True for the license — LICENSE file, GPL-3.0. For dependencies: make sure the README
     mentions the Python3 + PyGObject/GTK4/libadwaita requirement before checking this.)*
   - [ ] I confirm that I own or have permission to submit this plugin and its preview assets.
   - [ ] The plugin does not overwrite user configuration without explicit consent.
     *(True — the bar-widget only launches the app; the separate `install.sh` only ever
     touches your own `~/.config/omarchy/extensions/omarchy-menu.jsonc`, and only when you
     run it yourself.)*
   - [ ] I understand that approval is for listing and is not a security review.

## 6. Wait for automated checks, then a maintainer decision

After you submit, two things happen automatically and post results on the issue:
- Automated validation of the manifest/repo structure.
- The "Automated Security Baseline" — flags things like direct download-to-shell execution or
  unpinned external Git source execution (neither applies here: every command run is one of
  Omarchy's own `omarchy-plugin-*` binaries, called with a literal argument, never something
  downloaded and executed).

After that, a maintainer makes an explicit `approved-and-verified` decision before the plugin
is actually published to the catalog. There's no stated timeline for this in what's been
researched so far — if it's been a while with no response, that'd be a reasonable point to
follow up on the issue itself.

## Open questions not yet resolved

- Exact submission-issue template name/URL on GitHub couldn't be confirmed (the page needs a
  logged-in, JS-rendered view) — step 5 links to the generic "new issue" chooser instead.
- Preview image: optional per the requirements, not created here. If you want the marketplace
  listing to show one of the app's own screenshots later, that'd be a separate follow-up.
