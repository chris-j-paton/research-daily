# Research Daily

A personal dashboard of the latest papers from Nature, Science and Cell Press journals, plus bioRxiv preprints.
Everything runs locally and for free: no accounts, no servers, no extra Python packages.

## Daily use

1. Double-click `Research Daily.bat`. It starts a small local server (a minimised console window) and opens the dashboard at `http://127.0.0.1:8765`. If the server is already running it just opens the page.
2. Click **Refresh papers** in the header. The fetcher runs in the background, progress is shown next to the button, and the page reloads when it finishes (a few minutes the first time, usually under two afterwards).
3. Read through the sections. Clicking a title opens the paper on the publisher's site (use your institutional login there) and marks it as read.
4. Use the buttons on each row: **chevron** expands the abstract and author list, **bookmark** saves it, **tick** marks it read, **x** discards it.

Closing the minimised console window stops the server. To have it start with Windows, put a shortcut to `Research Daily.bat` in your Startup folder (`Win+R`, then `shell:startup`).

Opening `index.html` directly from disk still works, but without the Refresh button; `refresh.bat` runs the fetcher from the command line instead.

Read, saved and discarded states are stored in your browser (localStorage), so stick with one browser.

## Where the papers come from

Publisher RSS feeds are capped (Nature sub-journal feeds list only 8 items, the Nature feed is mostly news, Cell's issue feed omits online-first papers), so they cannot be the source of truth. The fetcher therefore combines four sources:

1. **Crossref** (`api.crossref.org`), queried by each journal's ISSN for every DOI registered in the last `lookback_days`. This is the complete list: a paper cannot be published without registering its DOI.
2. **Publisher RSS feeds**, which add the article type (Science and Cell state it explicitly) and short summaries. Cell Press journals use both the current-issue and the in-press feed.
3. **Nature article pages**, read once per paper for Nature-family items the feed did not cover. Their standard metadata tags give the article type (research, review, comment, correction) and the abstract.
4. **bioRxiv API** for preprints.

Each refresh prints a per-journal line showing how many items Crossref and the feeds returned, and any failures. If a source fails, the dashboard header shows a warning and the next run retries.

## Files

| File | Purpose |
| --- | --- |
| `config.json` | Journals, bioRxiv subject categories, review-detection patterns and keyword categories. Edit this to add journals. |
| `fetch.py` | Pulls the feeds, classifies items, keeps a rolling archive and writes `data/papers.js`. |
| `index.html` | The dashboard. Opens directly from disk. |
| `Research Daily.bat` | Starts the local server and opens the dashboard. Use this day to day. |
| `server.py` | The local server: serves the page and runs `fetch.py` when Refresh is clicked. |
| `refresh.bat` | Command-line alternative: runs the fetch and opens `index.html` from disk. |
| `data/papers.json` | Rolling archive (30 days of journal items, about a week of preprints). Older items are dropped on each refresh; use the Save button plus Zotero for anything you want to keep. Saved papers keep a copy in the browser so they outlive the archive. |

## Adding a journal

Add a line to the `journals` list in `config.json`:

```json
{ "name": "Nature Reviews Immunology", "issn": "1474-1733", "feeds": ["https://www.nature.com/nri.rss"], "publisher": "Nature", "section": "review" }
```

The `issn` is what makes the list complete, so always include it. Either the print or the online ISSN works; both are printed on the journal's "About" page. The `name` must match the journal title exactly as Crossref records it, because results under a shared ISSN are filtered by title.

- Nature journals: `https://www.nature.com/<slug>.rss` (the slug is the one in the journal's URL, e.g. `nri`, `natbiomedeng`).
- Science journals: `https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=<code>` (e.g. `sciimmunol`, `stm`).
- Cell Press journals: `https://www.cell.com/<journal-slug>/current.rss` (e.g. `cell-metabolism`, `cell-stem-cell`).

`section` is `review` for journals that publish only reviews (everything from them goes to the Reviews section) and `research` for everything else.
Review articles in research journals are detected from the article type the feed provides (Science and Cell) or from title patterns (Nature).

## Keyword categories

Click **Edit categories** in the dashboard to change them. Edits are stored in the browser; **Reset to config.json** restores the defaults from `config.json`, where you can also edit them permanently.

## Using it on your phone (GitHub Pages)

The folder already contains a GitHub Actions workflow (`.github/workflows/refresh.yml`) that runs `fetch.py` on GitHub's servers every morning and commits the updated data. GitHub Pages then serves the dashboard at a public URL you can open on any device. The data file is public, but it only contains titles, abstracts and links to the papers, nothing personal.

One-time setup:

1. Create a new repository on GitHub (public, so Pages and Actions are free). Do not add a README, licence or .gitignore there.
2. In this folder, run:

   ```bash
   git init
   git add .
   git commit -m "Research Daily"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
   git push -u origin main
   ```

3. On GitHub: **Settings → Actions → General → Workflow permissions**, choose *Read and write permissions* and save.
4. **Settings → Pages → Build and deployment**, set Source to *Deploy from a branch*, branch `main`, folder `/ (root)`, and save. After a minute the site is at `https://YOUR-USERNAME.github.io/YOUR-REPO/`.
5. **Actions → Refresh papers → Run workflow** to do the first run by hand. After that it runs every day at 06:00 UTC (change the `cron` line in the workflow to taste).

On the hosted version there is no Refresh button, because the daily workflow does the refreshing. Read, saved and discarded states are stored per browser, so your phone and laptop keep separate lists. Add the page to your phone's home screen for an app-like shortcut.

## Automating the refresh locally

Windows Task Scheduler can run `refresh.bat` every morning: create a basic task, trigger "Daily", action "Start a program", program `refresh.bat` in this folder. The dashboard will then always be fresh when you open it.
