"""
Research Daily - feed fetcher.

Sources, in order of trust for *completeness*:
  1. Crossref (api.crossref.org) - every DOI a journal registers, queried by ISSN.
     This is the authoritative list; publisher RSS feeds are capped and miss papers.
  2. Publisher RSS feeds - add article type (Science, Cell) and summaries.
  3. Nature article pages - standard <meta> tags give the article type and abstract
     for Nature-family papers the RSS feed did not cover (looked up once per paper).
  4. bioRxiv API - preprints.

Everything is merged by DOI into a rolling archive (data/papers.json), and
data/papers.js is written for the dashboard.  Standard library only, Python 3.8+.
Run:  python fetch.py
"""
import json
import os
import re
import sys
import time
import html
import datetime as dt
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DATA_DIR = os.path.join(HERE, "data")
ARCHIVE_PATH = os.path.join(DATA_DIR, "papers.json")
JS_PATH = os.path.join(DATA_DIR, "papers.js")

USER_AGENT = "Mozilla/5.0 (personal research dashboard; low volume; standard-library python)"
TODAY = dt.date.today()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def fetch(url, timeout=30, retries=2, accept=None):
    last = None
    for attempt in range(retries + 1):
        try:
            headers = {"User-Agent": USER_AGENT}
            if accept:
                headers["Accept"] = accept
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404):
                break
            time.sleep(2.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise last


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = TAG_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


def local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_texts(item, name):
    return [(c.text or "") for c in item if local(c.tag) == name]


def child_text(item, name):
    vals = child_texts(item, name)
    return vals[0] if vals else ""


def norm_date(s):
    s = (s or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


def clean_doi(doi):
    doi = (doi or "").strip()
    doi = re.sub(r"^(doi:|https?://(dx\.)?doi\.org/)\s*", "", doi, flags=re.I)
    return doi


# ----------------------------------------------------------------------------
# classification
# ----------------------------------------------------------------------------
# type_source ranks: how trustworthy the classification is.  Higher wins on merge.
RANK = {"default": 0, "heuristic": 1, "page": 2, "feed": 3}

RESEARCH_TYPES = {
    "research article", "research resource", "report", "article", "short article",
    "resource", "brief communication", "technical report", "analysis", "letter",
}
REVIEW_TYPES = {"review", "special issue review", "review article", "minireview", "perspective"}
NOISE_TYPES = {
    "erratum", "errata", "correction", "retraction", "author correction",
    "publisher correction", "editorial", "news", "in depth", "books et al.",
    "working life", "feature", "policy forum", "introduction to special issue",
    "research highlights", "research highlight", "preview", "spotlight", "commentary",
    "news & views", "comment", "focus", "obituary", "podcast", "editors' choice",
    "matters arising", "correspondence", "world view", "career column", "careers",
    "news feature", "outlook", "q&a", "where i work", "technology feature",
    "book review", "news in brief", "from the analyst's couch", "an audience with",
    "biobusiness briefs", "fresh from the pipeline", "news and analysis",
}
CORRECTION_RE = re.compile(
    r"^(author|publisher)?\s*(correction|erratum|retraction( note)?|addendum)\b", re.I
)
HIGHLIGHT_RE = re.compile(r"\bet al\.?\b")
# journal front matter that some feeds list as if it were an article
FRONT_MATTER_RE = re.compile(
    r"^(advisory board|editorial board|subscription|copyright|table of contents|contents|masthead|"
    r"in this issue|front cover|cover image|forthcoming|issue information|announcements?)\b", re.I
)


def classify_feed_type(ft, publisher):
    """Explicit article type from an RSS feed (Science dc:type, Cell prism:section)."""
    ftl = ft.strip().lower()
    if not ftl:
        return None
    if ftl in REVIEW_TYPES and ftl != "perspective":
        return "review", ft
    if ftl == "perspective":
        # Science 'Perspective' pieces are short commentaries on other papers
        return ("other", ft) if publisher == "Science" else ("review", ft)
    if ftl == "letter":
        return ("other", ft) if publisher == "Science" else ("research", ft)
    if ftl in RESEARCH_TYPES:
        return "research", ft
    if ftl in NOISE_TYPES:
        return "other", ft
    return None


def classify_page_meta(dc_type, art_type, journal, title=""):
    """Nature-family pages: <meta name="dc.type"> and <meta name="citation_article_type">."""
    d = (dc_type or "").strip().lower()
    a = (art_type or "").strip()
    al = a.lower()
    label = a or dc_type or ""
    if d == "erratum" or CORRECTION_RE.search(a):
        return "other", label or "Correction"
    if FRONT_MATTER_RE.search(title or ""):
        return "other", "Front matter"
    if d == "originalpaper":
        return "research", label or "Article"
    if d == "reviewpaper":
        return "review", label or "Review"
    if d == "briefcommunication":
        if al in ("brief communication", "resource", "analysis", "technical report", "article"):
            return "research", label
        if al in ("review", "review article", "perspective", "consensus statement", "roadmap", "primer"):
            return "review", label
        return "other", label or "Comment"
    if d in ("correspondence", "news", "news in brief", "editorial", "comment", "bookreview"):
        return "other", label or dc_type
    if al in REVIEW_TYPES:
        return "review", label
    if al in RESEARCH_TYPES:
        return "research", label
    if al in NOISE_TYPES:
        return "other", label
    # unknown: keep it visible rather than hide it
    return ("review", label or "Review") if journal["section"] == "review" else ("research", label or "Article")


def classify_heuristic(journal, title, snippet, authors, doi, review_patterns):
    """Fallback when no explicit type is available."""
    t = (title or "").strip()
    if CORRECTION_RE.search(t):
        return "other", "Correction"
    if FRONT_MATTER_RE.search(t):
        return "other", "Front matter"
    if doi and re.search(r"10\.1038/d\d{5}-", doi):
        return "other", "News & comment"
    if journal["section"] == "review":
        if len(authors) <= 1 and HIGHLIGHT_RE.search((snippet or "")[:120]):
            return "other", "Research highlight"
        return "review", "Review"
    for pat in review_patterns:
        if pat.search(t):
            return "review", "Review (title heuristic)"
    return "research", "Article"


# ----------------------------------------------------------------------------
# records
# ----------------------------------------------------------------------------
def make_record(journal, doi, title, link, authors, date, abstract, kind, subtype, type_source):
    return {
        "id": ("doi:" + doi.lower()) if doi else ("url:" + link.lower()),
        "title": title,
        "link": link,
        "doi": doi,
        "journal": journal["name"],
        "publisher": journal["publisher"],
        "authors": authors,
        "date": date,
        "abstract": abstract,
        "kind": kind,
        "subtype": subtype,
        "type_source": type_source,
        "sources": [],
        "affiliations": [],
    }


def dedupe(seq):
    seen, out = set(), []
    for s in seq:
        s = WS_RE.sub(" ", (s or "")).strip().rstrip(".")
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def apply_section(journal, kind):
    """Journals configured as section=review put every paper in the Reviews section."""
    return "review" if (journal.get("section") == "review" and kind == "research") else kind


def merge_into(store, rec, source):
    """Merge rec into store (dict id -> record). Higher-ranked type wins, longer abstract wins."""
    old = store.get(rec["id"])
    if not old:
        rec["sources"] = [source]
        store[rec["id"]] = rec
        return rec
    if source not in old["sources"]:
        old["sources"].append(source)
    if RANK.get(rec["type_source"], 0) >= RANK.get(old.get("type_source", "default"), 0):
        old["kind"], old["subtype"], old["type_source"] = rec["kind"], rec["subtype"], rec["type_source"]
    if len(rec.get("abstract", "")) > len(old.get("abstract", "")):
        old["abstract"] = rec["abstract"]
    if len(rec.get("authors", [])) > len(old.get("authors", [])):
        old["authors"] = rec["authors"]
    if len(rec.get("affiliations", [])) > len(old.get("affiliations", [])):
        old["affiliations"] = rec["affiliations"]
    if not old.get("title") and rec.get("title"):
        old["title"] = rec["title"]
    if rec.get("date") and (not old.get("date") or source == "feed"):
        old["date"] = rec["date"]
    if not old.get("doi") and rec.get("doi"):
        old["doi"] = rec["doi"]
    if source == "feed" and rec.get("link"):
        old["link"] = rec["link"]
    return old


# ----------------------------------------------------------------------------
# RSS feeds
# ----------------------------------------------------------------------------
def parse_feed(journal, raw, review_patterns):
    out = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise RuntimeError("XML parse error: %s" % e)

    for item in root.iter():
        if local(item.tag) != "item":
            continue
        title = strip_html(child_text(item, "title"))
        link = (child_text(item, "link") or "").strip()
        link = re.sub(r"\?(af=R|rss=yes)$", "", link)
        doi = clean_doi(child_text(item, "doi") or child_text(item, "identifier"))
        if doi and not doi.startswith("10."):
            doi = ""
        authors = []
        for a in child_texts(item, "creator"):
            a = a.strip()
            if not a:
                continue
            # Cell Press in-press feeds put every author in one comma-separated element
            if "," in a and len(child_texts(item, "creator")) == 1:
                authors.extend(x.strip() for x in a.split(",") if x.strip())
            else:
                authors.append(a)
        date = norm_date(child_text(item, "date") or child_text(item, "publicationDate")) or TODAY.isoformat()
        feed_type = child_text(item, "type") or child_text(item, "section")

        snippet = ""
        enc = child_text(item, "encoded")
        if enc:
            enc = html.unescape(enc)
            enc = re.sub(r"^\s*<p>.*?</p>", "", enc, count=1, flags=re.S)
            snippet = strip_html(enc)
            if re.match(r"^(Science|Nature)\b.*(Volume|Published online)", snippet):
                snippet = ""
        if not snippet:
            desc = strip_html(child_text(item, "description"))
            if desc and not re.match(r"^(Science|Nature)\b.*(Volume|Published online)", desc):
                snippet = desc
        if snippet == title:
            snippet = ""
        if not (title and link):
            continue

        explicit = classify_feed_type(feed_type, journal["publisher"])
        if explicit:
            kind, subtype, src = explicit[0], explicit[1], "feed"
            if CORRECTION_RE.search(title):
                kind, subtype = "other", "Correction"
            elif FRONT_MATTER_RE.search(title):
                kind, subtype = "other", "Front matter"
        else:
            kind, subtype = classify_heuristic(journal, title, snippet, authors, doi, review_patterns)
            src = "heuristic"
        out.append(make_record(journal, doi, title, link, authors, date, snippet, apply_section(journal, kind), subtype, src))
    return out


# ----------------------------------------------------------------------------
# Crossref (completeness)
# ----------------------------------------------------------------------------
def crossref_journal(journal, since, review_patterns):
    """All DOIs the journal registered since `since` (YYYY-MM-DD)."""
    out = []
    cursor = "*"
    base = ("https://api.crossref.org/journals/%s/works?filter=from-created-date:%s,type:journal-article"
            "&rows=500&select=DOI,title,author,created,published-online,published-print,abstract,container-title,URL"
            % (journal["issn"], since))
    wanted = journal["name"].lower()
    for _ in range(20):
        url = base + "&cursor=" + urllib.parse.quote(cursor)
        payload = json.loads(fetch(url, timeout=60).decode("utf-8", "replace"))
        msg = payload.get("message", {})
        items = msg.get("items") or []
        for it in items:
            ct = html.unescape((it.get("container-title") or [""])[0]).lower()
            if ct and ct != wanted:
                continue  # Crossref's ISSN records sometimes bundle sister journals
            doi = clean_doi(it.get("DOI"))
            title = strip_html((it.get("title") or [""])[0])
            if not doi or not title:
                continue
            authors, affs = [], []
            for a in it.get("author") or []:
                nm = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name") or ""
                if nm:
                    authors.append(nm)
                for af in a.get("affiliation") or []:
                    if af.get("name"):
                        affs.append(af["name"])
            date = ""
            for key in ("published-online", "created", "published-print"):
                parts = (it.get(key) or {}).get("date-parts") or []
                if parts and parts[0] and len(parts[0]) >= 3:
                    date = "%04d-%02d-%02d" % tuple(parts[0][:3])
                    break
            if not date:
                date = norm_date((it.get("created") or {}).get("date-time", "")) or TODAY.isoformat()
            abstract = strip_html(it.get("abstract", ""))
            abstract = re.sub(r"^(Abstract|Summary)\s*", "", abstract, flags=re.I)
            link = "https://doi.org/" + doi
            kind, subtype = classify_heuristic(journal, title, abstract, authors, doi, review_patterns)
            src = "heuristic"
            if journal["publisher"] != "Nature" and kind == "research":
                subtype, src = "Unclassified", "default"
            rec = make_record(journal, doi, title, link, authors, date, abstract, apply_section(journal, kind), subtype, src)
            rec["affiliations"] = dedupe(affs)
            out.append(rec)
        cursor = msg.get("next-cursor")
        if not cursor or len(items) < 500:
            break
        time.sleep(0.5)
    return out


# ----------------------------------------------------------------------------
# Nature article pages (type + abstract)
# ----------------------------------------------------------------------------
META_RE = re.compile(r'<meta\s+name="(dc\.type|citation_article_type|dc\.description|citation_online_date|citation_publication_date)"\s+content="([^"]*)"', re.I)


def enrich_from_nature_page(p, journal):
    url = p["link"] if "nature.com" in p["link"] else "https://doi.org/" + p["doi"]
    raw = fetch(url, timeout=30, retries=1, accept="text/html").decode("utf-8", "replace")
    meta = {}
    for k, v in META_RE.findall(raw[:200000]):
        meta.setdefault(k.lower(), html.unescape(v))
    m = re.search(r'<link rel="canonical" href="([^"]+)"', raw[:100000])
    if m and "nature.com" in m.group(1):
        p["link"] = m.group(1)
    if not meta:
        return False
    insts = re.findall(r'<meta\s+name="citation_author_institution"\s+content="([^"]*)"', raw[:400000], re.I)
    insts = dedupe(html.unescape(x) for x in insts)
    if len(insts) > len(p.get("affiliations", [])):
        p["affiliations"] = insts
    p["affil_checked"] = True
    kind, subtype = classify_page_meta(meta.get("dc.type"), meta.get("citation_article_type"), journal, p.get("title", ""))
    if RANK["page"] >= RANK.get(p.get("type_source", "default"), 0):
        p["kind"], p["subtype"], p["type_source"] = apply_section(journal, kind), subtype, "page"
    desc = strip_html(meta.get("dc.description", ""))
    if len(desc) > len(p.get("abstract", "")):
        p["abstract"] = desc
    d = norm_date(meta.get("citation_online_date") or meta.get("citation_publication_date"))
    if d:
        p["date"] = d
    return True


# ----------------------------------------------------------------------------
# bioRxiv
# ----------------------------------------------------------------------------
def fetch_biorxiv(cfg):
    days = int(cfg.get("preprint_days", 7))
    start = (TODAY - dt.timedelta(days=days)).isoformat()
    end = TODAY.isoformat()
    keep = cfg.get("biorxiv", {}).get("categories")
    keep = set(c.lower() for c in keep) if keep else None
    new_only = cfg.get("biorxiv", {}).get("new_results_only", True)
    journal = {"name": "bioRxiv", "publisher": "bioRxiv", "section": "preprint"}

    out, cursor, pages = [], 0, 0
    while True:
        url = "https://api.biorxiv.org/details/biorxiv/%s/%s/%d" % (start, end, cursor)
        payload = json.loads(fetch(url, timeout=60).decode("utf-8", "replace"))
        msg = (payload.get("messages") or [{}])[0]
        coll = payload.get("collection") or []
        if not coll:
            break
        for p in coll:
            cat = (p.get("category") or "").lower().strip()
            if keep and cat not in keep:
                continue
            if new_only and (p.get("type") or "").lower() != "new results":
                continue
            doi = clean_doi(p.get("doi", ""))
            authors = [a.strip() for a in (p.get("authors") or "").split(";") if a.strip()]
            rec = make_record(
                journal, doi, strip_html(p.get("title", "")),
                "https://www.biorxiv.org/content/%sv%s" % (doi, p.get("version", "1")),
                authors, norm_date(p.get("date")) or TODAY.isoformat(), strip_html(p.get("abstract", "")),
                "preprint", cat.title() if cat else "Preprint", "feed")
            inst = strip_html(p.get("author_corresponding_institution", ""))
            if inst and inst.upper() != "NA":
                rec["affiliations"] = [inst + " (corresponding author)"]
            out.append(rec)
        count = int(msg.get("count") or len(coll))
        total = int(msg.get("total") or 0)
        cursor += count
        pages += 1
        if count == 0 or (total and cursor >= total) or pages > 200:
            break
        time.sleep(0.3)
    return out


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    review_patterns = [re.compile(p, re.I) for p in cfg.get("review_title_patterns", [])]
    os.makedirs(DATA_DIR, exist_ok=True)

    archive = {}
    if os.path.exists(ARCHIVE_PATH):
        with open(ARCHIVE_PATH, encoding="utf-8") as f:
            for p in json.load(f).get("papers", []):
                p.setdefault("type_source", "heuristic")
                p.setdefault("sources", ["feed"])
                archive[p["id"]] = p

    now_iso = dt.datetime.now().replace(microsecond=0).isoformat()
    lookback = int(cfg.get("lookback_days", 7))
    if not archive:
        lookback = max(lookback, 14)
    since = (TODAY - dt.timedelta(days=lookback)).isoformat()

    run = {}           # id -> record, everything seen this run
    report = []        # per-journal source summary
    failures = []

    print("Collecting %d journals (Crossref since %s + RSS feeds)..." % (len(cfg["journals"]), since))
    for j in cfg["journals"]:
        row = {"journal": j["name"], "crossref": None, "feed": None}
        if j.get("issn"):
            try:
                items = crossref_journal(j, since, review_patterns)
                for r in items:
                    merge_into(run, r, "crossref")
                row["crossref"] = len(items)
            except Exception as e:
                failures.append("Crossref %s: %s" % (j["name"], e))
                row["crossref"] = "failed"
        else:
            failures.append("%s has no ISSN in config.json; relying on RSS only" % j["name"])
        n_feed = 0
        for url in j.get("feeds") or ([j["url"]] if j.get("url") else []):
            try:
                for r in parse_feed(j, fetch(url), review_patterns):
                    merge_into(run, r, "feed")
                    n_feed += 1
            except Exception as e:
                failures.append("Feed %s (%s): %s" % (j["name"], url, e))
                row["feed"] = "failed"
        if row["feed"] != "failed":
            row["feed"] = n_feed
        report.append(row)
        print("   %-34s crossref=%-6s feed=%s" % (j["name"], row["crossref"], row["feed"]))
        time.sleep(0.2)

    print("Fetching bioRxiv (last %d days)..." % int(cfg.get("preprint_days", 7)))
    try:
        pre = fetch_biorxiv(cfg)
        for r in pre:
            merge_into(run, r, "biorxiv")
        print("   bioRxiv %d preprints kept" % len(pre))
    except Exception as e:
        failures.append("bioRxiv: %s" % e)
        print("   ! bioRxiv failed: %s" % e)

    # ---- merge this run into the archive
    new_count = 0
    for rec in run.values():
        old = archive.get(rec["id"])
        if old:
            rec["first_seen"] = old.get("first_seen", now_iso)
            rec["page_checked"] = old.get("page_checked", False)
            rec["abstract_checked"] = old.get("abstract_checked", False)
            rec["affil_checked"] = old.get("affil_checked", False)
            # keep anything better that the archive already learned
            if RANK.get(old.get("type_source", "default"), 0) > RANK.get(rec["type_source"], 0):
                rec["kind"], rec["subtype"], rec["type_source"] = old["kind"], old["subtype"], old["type_source"]
            if len(old.get("abstract", "")) > len(rec.get("abstract", "")):
                rec["abstract"] = old["abstract"]
            if len(old.get("authors", [])) > len(rec.get("authors", [])):
                rec["authors"] = old["authors"]
            if len(old.get("affiliations", [])) > len(rec.get("affiliations", [])):
                rec["affiliations"] = old["affiliations"]
            if "nature.com" in old.get("link", "") and "doi.org" in rec.get("link", ""):
                rec["link"] = old["link"]
            for s in old.get("sources", []):
                if s not in rec["sources"]:
                    rec["sources"].append(s)
        else:
            rec["first_seen"] = now_iso
            rec["page_checked"] = False
            rec["abstract_checked"] = False
            new_count += 1
        archive[rec["id"]] = rec

    journals_by_name = {j["name"]: j for j in cfg["journals"]}

    # ---- Nature pages: article type + abstract for anything the feed did not type
    def needs_page(p):
        if p["publisher"] != "Nature" or p["kind"] == "preprint":
            return False
        if re.search(r"10\.1038/d\d{5}-", p.get("doi", "")):
            return False
        if not p.get("page_checked") and p.get("type_source") in ("heuristic", "default"):
            return True
        # pages read before affiliations were collected: read once more
        return p["kind"] in ("research", "review") and not p.get("affiliations") and not p.get("affil_checked")
    todo = [p for p in archive.values() if needs_page(p)]
    todo.sort(key=lambda p: p.get("first_seen", ""), reverse=True)
    cap = int(cfg.get("page_lookups_per_run", 400))
    if todo:
        print("Reading %d Nature article pages for type and abstract (cap %d)..." % (len(todo), cap))
    done = ok = 0
    refused = [False]

    def work(p):
        if refused[0]:
            return None
        j = journals_by_name.get(p["journal"], {"section": "research", "name": p["journal"]})
        try:
            r = enrich_from_nature_page(p, j)
            p["page_checked"] = True
            return r
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (403, 429):
                refused[0] = True
                return None
            p["page_checked"] = True   # 404s etc: do not retry forever
            return False

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:   # four at a time keeps nature.com happy
        for r in ex.map(work, todo[:cap]):
            done += 1
            if r:
                ok += 1
            if done % 50 == 0:
                print("   %d/%d" % (done, min(len(todo), cap)))
    if refused[0]:
        failures.append("nature.com refused page lookups (HTTP 403/429); unclassified papers stay visible and are retried next run")
    if todo:
        print("   %d pages read, %d classified" % (done, ok))

    # ---- Crossref per-DOI abstract lookups for anything still lacking one
    todo = [p for p in archive.values()
            if p["kind"] in ("research", "review") and not p["abstract"]
            and p.get("doi") and not p.get("abstract_checked") and "crossref" not in p.get("sources", [])]
    cap = int(cfg.get("crossref_lookups_per_run", 120))
    filled = 0
    if todo:
        print("Looking up %d missing abstracts on Crossref (cap %d)..." % (len(todo), cap))
    for p in todo[:cap]:
        p["abstract_checked"] = True
        try:
            raw = fetch("https://api.crossref.org/works/" + urllib.parse.quote(p["doi"]), timeout=20, retries=0)
            msg = json.loads(raw.decode("utf-8", "replace")).get("message", {})
            abs_ = re.sub(r"^(Abstract|Summary)\s*", "", strip_html(msg.get("abstract", "")), flags=re.I)
            if abs_:
                p["abstract"] = abs_
                filled += 1
        except Exception:
            pass
        time.sleep(0.25)
    if todo:
        print("   filled %d abstracts" % filled)

    # ---- prune and write
    cutoff_j = (TODAY - dt.timedelta(days=int(cfg.get("archive_days", 45)))).isoformat()
    cutoff_p = (TODAY - dt.timedelta(days=int(cfg.get("preprint_days", 7)) + 2)).isoformat()
    papers = []
    for p in archive.values():
        cutoff = cutoff_p if p["kind"] == "preprint" else cutoff_j
        if p.get("date", "") >= cutoff:
            papers.append(p)
    papers.sort(key=lambda p: (p.get("first_seen", ""), p.get("date", "")), reverse=True)
    # news, comment and corrections are only ever skimmed: drop their abstracts to keep the data file small
    for p in papers:
        if p["kind"] == "other":
            p["abstract"] = ""
            p["affiliations"] = []

    meta = {
        "generated": now_iso,
        "new_this_run": new_count,
        "failures": failures,
        "report": report,
        "journals": [{"name": j["name"], "publisher": j["publisher"], "section": j["section"]} for j in cfg["journals"]],
        "categories": cfg.get("categories", {}),
    }
    with open(ARCHIVE_PATH, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "papers": papers}, f, ensure_ascii=False, indent=0)
    with open(JS_PATH, "w", encoding="utf-8") as f:
        f.write("window.DASHBOARD_DATA = ")
        json.dump({"meta": meta, "papers": papers}, f, ensure_ascii=False)
        f.write(";\n")

    kinds = {}
    for p in papers:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print("\nDone. %d papers in archive (%d new this run): %s" % (len(papers), new_count, kinds))
    if failures:
        print("Problems this run:\n   " + "\n   ".join(failures))


if __name__ == "__main__":
    main()
