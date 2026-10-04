"""Persist each search run and compare prices for the same listing slug."""

import csv
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx

from facebook_marketplace_mcp.parser import MarketplaceListing
from facebook_marketplace_mcp.preview import PagePreviewer

PROJECT_DIR = Path(__file__).resolve().parents[2]
STORAGE_DIR = Path.home() / ".fb-marketplace"
RUNS_DIR = PROJECT_DIR / "runs"
CATALOG_FILE = STORAGE_DIR / "catalog.json"

CSV_COLUMNS = [
    "created_at",
    "updated_at",
    "query",
    "slug",
    "id",
    "title",
    "original_price",
    "original_amount",
    "price",
    "amount",
    "price_dropped",
    "location",
    "seller_name",
    "url",
    "image",
    "page_preview",
]

_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
}

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "listing"


def listing_slug(title: str, listing_id: str) -> str:
    return f"{slugify(title)}-{listing_id}"


def price_amount(price: str) -> float | None:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", price or "")
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def query_dir_name(query: str) -> str:
    """Folder name for a search. ``cr-v 2023`` stays ``cr-v 2023``."""
    cleaned = re.sub(r"[\x00-\x1f/\\]", "-", query.strip())
    cleaned = cleaned.strip(" .")
    if cleaned in {"", ".", ".."}:
        return "search"
    return cleaned


def record_run(listings: list[MarketplaceListing], query: str = "") -> Path:
    """Save listings under ``runs/<query>/<slug>.json``.

    The first time a listing is seen, ``created_at`` and ``original_price`` are
    set. A later search that finds a lower price keeps that original price,
    stores the lower price, and updates ``updated_at``.
    """
    migrate_legacy_date_runs()
    seen_at = datetime.now(timezone.utc).isoformat()
    catalog = _load_catalog()
    folders: set[Path] = set()
    previewer = PagePreviewer()
    preview_ready = False

    def page_preview_name(url: str, dest: Path, refresh: bool) -> str:
        nonlocal preview_ready
        if not url:
            return dest.name if dest.exists() else ""
        if dest.exists() and not refresh:
            return dest.name
        if not preview_ready:
            try:
                previewer.start()
                preview_ready = previewer._socket is not None
            except Exception:
                preview_ready = False
        if preview_ready:
            try:
                previewer.capture(url, dest)
            except Exception:
                pass
        return dest.name if dest.exists() else ""

    try:
        for listing in listings:
            entry = catalog.get(listing.id) if isinstance(catalog.get(listing.id), dict) else None
            item_query = query.strip() or (entry or {}).get("query") or listing.title or "listing"
            folder = RUNS_DIR / query_dir_name(item_query)
            folder.mkdir(parents=True, exist_ok=True)
            folders.add(folder)

            slug = (entry or {}).get("slug") or listing_slug(listing.title, listing.id)
            amount = price_amount(listing.price)
            document = _load_item(folder / f"{slug}.json")
            created_at = document.get("created_at") or seen_at
            original_price = document.get("original_price") or listing.price
            original_amount = document.get("original_amount")
            if original_amount is None:
                original_amount = price_amount(str(original_price))
            updated_at = document.get("updated_at") or created_at
            last_amount = document.get("amount") if document else None
            if document and last_amount is not None and amount is not None and amount != last_amount:
                updated_at = seen_at

            dropped = (
                original_amount is not None
                and amount is not None
                and amount < original_amount
            )
            listing.slug = slug
            listing.query = item_query
            listing.original_price = str(original_price)
            listing.previous_price = str(original_price) if dropped else ""
            listing.price_changed = dropped
            listing.price_dropped = dropped
            listing.created_at = created_at
            listing.updated_at = updated_at

            saved = {
                "slug": slug,
                "id": listing.id,
                "title": listing.title,
                "location": listing.location,
                "seller_name": listing.seller_name,
                "url": listing.url,
                "query": item_query,
                "created_at": created_at,
                "updated_at": updated_at,
                "original_price": original_price,
                "original_amount": original_amount,
                "price": listing.price,
                "amount": amount,
                "price_dropped": dropped,
                "image": _save_listing_image(folder, slug, listing.image_url),
                "page_preview": page_preview_name(
                    listing.url,
                    folder / f"{slug}-page.png",
                    refresh=dropped or not (folder / f"{slug}-page.png").exists(),
                ),
            }
            _write_item(folder / slug, saved)
            catalog[listing.id] = {
                "slug": slug,
                "query": item_query,
                "price": listing.price,
                "amount": amount,
                "original_price": original_price,
                "original_amount": original_amount,
                "title": listing.title,
                "location": listing.location,
                "seller_name": listing.seller_name,
                "url": listing.url,
                "created_at": created_at,
                "updated_at": updated_at,
            }

        for folder in folders:
            _rewrite_folder_csv(folder)
        _save_catalog(catalog)
        write_runs_index()
    finally:
        previewer.close()
    if len(folders) == 1:
        return next(iter(folders))
    return RUNS_DIR


def write_runs_index() -> Path:
    """Write a self-contained page that lists search folders and their listings."""
    migrate_legacy_date_runs()
    runs: dict[str, list] = {}
    if RUNS_DIR.exists():
        for day_dir in sorted(path for path in RUNS_DIR.iterdir() if path.is_dir()):
            items = []
            for path in sorted(day_dir.glob("*.json")):
                try:
                    items.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
            runs[day_dir.name] = items
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_json_safe(runs)).replace("<", "\\u003c")
    index = RUNS_DIR / "index.html"
    index.write_text(_INDEX_HTML.replace("/*__DATA__*/", payload), encoding="utf-8")
    return index


def migrate_legacy_date_runs() -> None:
    """Move ``runs/<YYYY-MM-DD>/`` items into ``runs/<query>/``."""
    if not RUNS_DIR.exists():
        return
    for day_dir in list(RUNS_DIR.iterdir()):
        if not day_dir.is_dir() or not _DATE_DIR.match(day_dir.name):
            continue
        catalog = _load_catalog()
        for path in list(day_dir.glob("*.json")):
            saved = _migrate_dated_item(path)
            if saved and saved.get("id"):
                catalog[str(saved["id"])] = {
                    "slug": saved["slug"],
                    "query": saved["query"],
                    "price": saved["price"],
                    "amount": saved["amount"],
                    "original_price": saved["original_price"],
                    "original_amount": saved["original_amount"],
                    "title": saved["title"],
                    "location": saved["location"],
                    "seller_name": saved["seller_name"],
                    "url": saved["url"],
                    "created_at": saved["created_at"],
                    "updated_at": saved["updated_at"],
                }
        _save_catalog(catalog)
        for leftover in list(day_dir.iterdir()):
            leftover.unlink()
        day_dir.rmdir()


def _migrate_dated_item(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(document, dict):
        return {}
    observations = document.get("observations")
    first = observations[0] if isinstance(observations, list) and observations else document
    if not isinstance(first, dict):
        first = {}
    query = str(first.get("query") or document.get("query") or "search")
    created_at = str(first.get("seen_at") or document.get("created_at") or "")
    price = str(document.get("price") or first.get("price") or "")
    amount = document.get("amount")
    if amount is None:
        amount = first.get("amount")
    if amount is None:
        amount = price_amount(price)
    original_price = str(document.get("original_price") or first.get("price") or price)
    original_amount = document.get("original_amount")
    if original_amount is None:
        original_amount = first.get("amount")
    if original_amount is None:
        original_amount = price_amount(original_price)
    slug = str(document.get("slug") or path.stem)
    saved = {
        "slug": slug,
        "id": document.get("id") or first.get("id") or "",
        "title": document.get("title") or first.get("title") or "",
        "location": document.get("location") or first.get("location") or "",
        "seller_name": document.get("seller_name") or first.get("seller_name") or "",
        "url": document.get("url") or first.get("url") or "",
        "query": query,
        "created_at": created_at,
        "updated_at": str(document.get("updated_at") or created_at),
        "original_price": original_price,
        "original_amount": original_amount,
        "price": price,
        "amount": amount,
        "price_dropped": bool(
            original_amount is not None and amount is not None and amount < original_amount
        ),
    }
    folder = RUNS_DIR / query_dir_name(query)
    folder.mkdir(parents=True, exist_ok=True)
    _write_item(folder / slug, saved)
    _rewrite_folder_csv(folder)
    return saved


def _save_listing_image(folder: Path, slug: str, image_url: str) -> str:
    """Save the listing photo from this run as ``<slug>.png``."""
    name = f"{slug}.png"
    dest = folder / name
    if not image_url:
        return name if dest.exists() else ""
    try:
        response = httpx.get(
            image_url,
            headers=_IMAGE_HEADERS,
            timeout=20,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return name if dest.exists() else ""
    if response.status_code >= 400 or len(response.content) < 32:
        return name if dest.exists() else ""
    if _write_png(response.content, dest):
        return name
    return name if dest.exists() else ""


def _write_png(content: bytes, dest: Path) -> bool:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        dest.write_bytes(content)
        return True
    suffix = ".webp" if content.startswith(b"RIFF") and content[8:12] == b"WEBP" else ".jpg"
    tmp = dest.with_suffix(suffix + ".tmp")
    tmp.write_bytes(content)
    try:
        result = subprocess.run(
            ["sips", "-s", "format", "png", str(tmp), "--out", str(dest)],
            capture_output=True,
            check=False,
        )
    finally:
        tmp.unlink(missing_ok=True)
    return result.returncode == 0 and dest.exists() and dest.stat().st_size > 32


def _load_item(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _json_safe(value):
    if isinstance(value, str):
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _write_item(stem: Path, row: dict) -> None:
    stem.with_suffix(".json").write_text(json.dumps(_json_safe(row), indent=2) + "\n", encoding="utf-8")
    _write_csv(stem.with_suffix(".csv"), [row])


def _rewrite_folder_csv(folder: Path) -> None:
    rows = []
    for path in sorted(folder.glob("*.json")):
        document = _load_item(path)
        if document:
            rows.append(document)
    _write_csv(folder / "listings.csv", rows)


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _json_safe(row.get(column, "")) for column in CSV_COLUMNS})


def _load_catalog() -> dict:
    if not CATALOG_FILE.exists():
        return {}
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_catalog(catalog: dict) -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(_json_safe(catalog), indent=2), encoding="utf-8")


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marketplace runs</title>
<style>
  :root { color-scheme: light; --ink: #1c1917; --muted: #78716c; --line: #e7e5e4; --paper: #f6f3ee; --card: #fff; --drop: #c2410c; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 ui-sans-serif, system-ui, sans-serif; color: var(--ink); background: var(--paper); }
  header { padding: 28px 28px 4px; }
  header h1 { margin: 0; font-size: 28px; letter-spacing: -0.03em; }
  header p { margin: 6px 0 0; color: var(--muted); max-width: 42rem; }
  .layout { display: grid; grid-template-columns: 240px 1fr; gap: 8px; padding: 16px 20px 40px; }
  nav { position: sticky; top: 16px; align-self: start; display: flex; flex-direction: column; gap: 8px; }
  nav button { text-align: left; padding: 12px 14px; border: 1px solid transparent; border-radius: 12px; background: transparent; color: var(--ink); cursor: pointer; }
  nav button:hover { background: rgba(255,255,255,0.7); }
  nav button[aria-current="true"] { background: var(--ink); color: #fff; }
  main { min-width: 0; }
  .summary { margin: 4px 8px 12px; color: var(--muted); font-size: 13px; }
  .list { display: flex; flex-direction: column; gap: 10px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; box-shadow: 0 1px 2px rgba(28,25,23,0.04); }
  .row { width: 100%; display: grid; grid-template-columns: 84px 120px 1fr auto 28px; gap: 16px; align-items: center; padding: 14px 16px; border: 0; background: transparent; text-align: left; cursor: pointer; color: inherit; font: inherit; }
  .thumb-wrap { width: 84px; height: 64px; border-radius: 10px; background: #f5f5f4; overflow: hidden; }
  .thumb, .preview { width: 100%; height: 100%; object-fit: cover; display: block; }
  .preview { height: 280px; width: calc(100% - 32px); margin: 0 16px 12px; border-radius: 12px; background: #f5f5f4; }
  .page-link { display: block; margin: 0 16px 12px; color: inherit; text-decoration: none; }
  .page-link .preview { width: 100%; height: auto; max-height: 560px; margin: 0; object-fit: contain; object-position: top center; background: #1c1917; }
  .page-url { display: block; margin-top: 8px; color: var(--muted); font-size: 12px; word-break: break-all; }
  .row:hover { background: #fafaf9; }
  .price { font-size: 18px; font-weight: 650; letter-spacing: -0.02em; }
  .card.dropped .price { color: var(--drop); }
  .was { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; font-weight: 500; text-decoration: line-through; }
  .title { display: block; font-weight: 600; }
  .sub { display: block; margin-top: 2px; color: var(--muted); font-size: 13px; }
  .updated { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .chevron { width: 18px; height: 18px; border-right: 2px solid #a8a29e; border-bottom: 2px solid #a8a29e; transform: rotate(45deg); transition: transform 160ms ease; justify-self: center; }
  .card.open .chevron { transform: rotate(225deg); }
  .panel { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 180ms ease; }
  .card.open .panel { grid-template-rows: 1fr; }
  .panel-inner { overflow: hidden; }
  .facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0 16px 16px; padding: 14px; border-radius: 12px; background: #fafaf9; }
  .facts span { display: block; color: var(--muted); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; }
  .facts strong { display: block; margin-top: 4px; font-size: 14px; font-weight: 600; }
  .actions { padding: 0 16px 16px; }
  .actions a { display: inline-flex; align-items: center; padding: 8px 12px; border-radius: 999px; background: var(--ink); color: #fff; text-decoration: none; font-size: 13px; font-weight: 600; }
  .actions a:hover { background: #44403c; }
  @media (max-width: 800px) {
    .layout { grid-template-columns: 1fr; }
    nav { position: static; flex-direction: row; overflow-x: auto; }
    .row { grid-template-columns: 84px 1fr auto; }
    .updated { display: none; }
    .facts { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>
<header>
  <h1>Marketplace runs</h1>
  <p>Open a search, then a car. The details open under that car. A later lower price keeps the original.</p>
</header>
<div class="layout">
  <nav id="dates"></nav>
  <main>
    <div id="summary" class="summary"></div>
    <div id="list" class="list"></div>
  </main>
</div>
<script>
const runs = /*__DATA__*/;
const datesEl = document.getElementById("dates");
const listEl = document.getElementById("list");
const summaryEl = document.getElementById("summary");
const dates = Object.keys(runs).sort();
let selected = dates[0] || "";
let openSlug = "";

function when(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) return value || "—";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function addFact(parent, label, value) {
  const item = document.createElement("div");
  const name = document.createElement("span");
  const number = document.createElement("strong");
  name.textContent = label;
  number.textContent = value || "—";
  item.append(name, number);
  parent.append(item);
}

function renderDates() {
  datesEl.replaceChildren();
  if (!dates.length) {
    datesEl.textContent = "No searches yet.";
    return;
  }
  for (const day of dates) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = day + " · " + runs[day].length;
    button.setAttribute("aria-current", day === selected ? "true" : "false");
    button.addEventListener("click", () => { selected = day; openSlug = ""; renderDates(); renderItems(); });
    datesEl.append(button);
  }
}

function renderItems() {
  listEl.replaceChildren();
  const items = (runs[selected] || []).slice().sort((a, b) => {
    const aa = Number(a.amount);
    const bb = Number(b.amount);
    return (Number.isFinite(aa) ? aa : 0) - (Number.isFinite(bb) ? bb : 0);
  });
  summaryEl.textContent = selected
    ? items.length + " listing" + (items.length === 1 ? "" : "s") + " in " + selected
    : "No searches yet.";
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "card" + (item.price_dropped ? " dropped" : "");
    if (item.slug === openSlug) card.classList.add("open");

    const button = document.createElement("button");
    button.type = "button";
    button.className = "row";
    button.setAttribute("aria-expanded", item.slug === openSlug ? "true" : "false");

    const thumbWrap = document.createElement("span");
    thumbWrap.className = "thumb-wrap";
    if (item.image) {
      const thumb = document.createElement("img");
      thumb.className = "thumb";
      thumb.alt = "";
      thumb.src = encodeURI(selected + "/" + item.image);
      thumbWrap.append(thumb);
    }

    const price = document.createElement("span");
    price.className = "price";
    price.textContent = item.price || "—";
    if (item.price_dropped && item.original_price && item.original_price !== item.price) {
      const was = document.createElement("span");
      was.className = "was";
      was.textContent = item.original_price;
      price.append(was);
    }

    const meta = document.createElement("span");
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = item.title || item.slug || "Listing";
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = [item.location, item.seller_name].filter(Boolean).join(" · ");
    meta.append(title, sub);

    const updated = document.createElement("span");
    updated.className = "updated";
    updated.textContent = when(item.updated_at);
    const chevron = document.createElement("span");
    chevron.className = "chevron";
    chevron.setAttribute("aria-hidden", "true");
    button.append(thumbWrap, price, meta, updated, chevron);

    const panel = document.createElement("div");
    panel.className = "panel";
    const inner = document.createElement("div");
    inner.className = "panel-inner";
    const pageFile = item.page_preview || "";
    if (pageFile && item.url) {
      const pageLink = document.createElement("a");
      pageLink.className = "page-link";
      pageLink.href = item.url;
      pageLink.target = "_blank";
      pageLink.rel = "noreferrer";
      const preview = document.createElement("img");
      preview.className = "preview";
      preview.alt = "Preview of " + item.url;
      preview.src = encodeURI(selected + "/" + pageFile);
      const address = document.createElement("span");
      address.className = "page-url";
      address.textContent = item.url;
      pageLink.append(preview, address);
      inner.append(pageLink);
    } else if (item.image) {
      const preview = document.createElement("img");
      preview.className = "preview";
      preview.alt = item.title || "Listing photo";
      preview.src = encodeURI(selected + "/" + item.image);
      inner.append(preview);
    }
    const facts = document.createElement("div");
    facts.className = "facts";
    addFact(facts, "Price", item.price);
    addFact(facts, "Original", item.original_price || item.price);
    addFact(facts, "Created", when(item.created_at));
    addFact(facts, "Updated", when(item.updated_at));
    inner.append(facts);
    if (item.url) {
      const actions = document.createElement("div");
      actions.className = "actions";
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Open listing";
      actions.append(link);
      inner.append(actions);
    }
    panel.append(inner);

    button.addEventListener("click", () => {
      const willOpen = !card.classList.contains("open");
      for (const other of listEl.querySelectorAll(".card.open")) {
        other.classList.remove("open");
        other.querySelector(".row").setAttribute("aria-expanded", "false");
      }
      openSlug = willOpen ? item.slug : "";
      card.classList.toggle("open", willOpen);
      button.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
    card.append(button, panel);
    listEl.append(card);
  }
}

renderDates();
renderItems();
</script>
</body>
</html>
"""
