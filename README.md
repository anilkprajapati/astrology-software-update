# Astro Rishi for Mac — build project (OWNER ONLY)

Builds `AstroRishi.app` / a `.dmg` for Apple Silicon and Intel Macs. A Mac app can only be built on a Mac, so GitHub builds
it for you on a free Mac in the cloud. This folder is the content of the **public release repository**
(`anilkprajapati/astrology-software-update`): it holds only packaging code — never your source code, keys or token.
Your private source repository is only **read** by the build (with a read-only token) and is never changed.

## What a customer gets
- Open the `.dmg`, drag **Astro Rishi** to Applications, start it (first time: right-click → Open, see "READ ME FIRST.txt").
- Own program window (no browser), same licensing as Windows: first start shows a **Machine ID**, you make a key in the
  same **Key Maker**, they paste it once, later starts don't ask.
- Updates: the program checks GitHub, shows "Astro Rishi X is available", verifies your signature and applies the
  code-only update. Place-database updates (`places-N`) work exactly like on Windows.
- User data lives in `~/Library/Application Support/AstroRishi` and is never overwritten by updates.

## One-time setup
1. **Put this folder's contents into the release repository** (`astrology-software-update`):
   ```
   cd "E:\Astrology\mine software\V2_nadi_node-mac"
   git init -b main
   git remote add origin https://github.com/anilkprajapati/astrology-software-update.git
   git pull origin main --allow-unrelated-histories      (it only has a README)
   git add -A
   git commit -m "Mac build project"
   git push origin main
   ```
   (Pushing a workflow file needs your normal GitHub login — the token used for releases may not allow it.)
2. **Let the build read your private source repository:** GitHub → Settings → Developer settings → Fine-grained tokens →
   new token, *Only select repositories* = your source repo (`v2_astrology`), permission **Contents: Read-only**.
   Then in the release repository: Settings → Secrets and variables → Actions → **New repository secret**, name
   `SOURCE_REPO_TOKEN`, value = that token. If the source repo is not `anilkprajapati/v2_astrology`, add a repository
   *variable* `SOURCE_REPO` with the right `owner/name`.
3. **Upload the place database** the Mac build includes (from `Admin`):
   `build\venv\Scripts\python.exe mac_tools.py data`
   (repeat after you change places.db a lot; small changes reach existing Macs as normal place updates).

## Build
Release repository on GitHub → **Actions → Build Mac app → Run workflow**. After ~15–25 minutes the run has two downloads
under *Artifacts*: `AstroRishi-Mac-arm64` (Apple Silicon `.dmg`) and `AstroRishi-Mac-x86_64` (Intel `.dmg`), plus
`AstroRishi-Mac-Update` (the code-only update zip). Hand the `.dmg` to Mac customers.

## Update installed Macs (after you have shared the app)
1. Run the workflow again and download the `AstroRishi-Mac-Update` artifact.
2. In `Admin`: `build\venv\Scripts\python.exe mac_tools.py release path\to\AstroRishi-Mac-Update-<version>.zip` — it signs
   the file with your private key (which never leaves your PC) and publishes it as pre-release `mac-v<version>`.
   (Hold the release back while `Admin\build\hold_publishing.flag` is your reminder that nothing is shared yet.)
3. A change in `requirements.txt` (new Python packages) needs a new `.dmg` — code-only updates cannot add packages.

## After you change the licensing code in `Admin\licensing`
Run `build\venv\Scripts\python.exe mac_tools.py sync` in `Admin`, then commit + push this folder.
To force a higher Mac version when only packaging changed, add 1 to `mac\revision.txt`.

## Honest notes
- **Not tested on a Mac yet.** The first workflow run may need small fixes; the log shows exactly where. Send it to me.
- **Gatekeeper:** the app is only *ad-hoc signed*. Customers must right-click → Open once. To remove that, enrol in the Apple
  Developer Program (about US$99/year) and add signing + notarization to the workflow.
- The app is bigger than a typical Mac app (~400 MB) because it carries Python, the astrology libraries and the city database.
