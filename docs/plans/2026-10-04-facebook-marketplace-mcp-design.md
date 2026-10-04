# Facebook Marketplace MCP Server — Design

## Summary

A Python MCP server that reads Facebook Marketplace through the logged-in Chrome session. Location lookup uses Facebook's GraphQL API. Listing search and listing detail read Marketplace HTML. There is no browser automation at runtime.

Users authenticate via automatic Chrome cookie extraction on macOS. Saved searches are stored locally and return only new listings on check.

## Requirements

- **Search/browse** listings by one or more names, price, and optional category. Names such as `crv 2023` and `cr-v 2023` are searched separately and combined by listing id.
- **Get listing details** by ID
- **Look up a city** and return coordinates
- **Monitor saved searches** — persist queries, return only new listings on check
- **macOS only** for cookie extraction
- **Automated auth** — read the Chrome cookie DB and decrypt via Keychain

## Architecture

```
MCP Client (stdio) → MCP Server → Marketplace search HTML  → listing cards
                                 → Facebook GraphQL        → location lookup
                                 → Auth (Chrome SQLite + Keychain)
                                 → Monitor storage (~/.fb-marketplace/monitors.json)
```

### Approach

The server copies the Chrome cookie database, decrypts `facebook.com` cookies, and fetches `facebook.com/marketplace/` to read `fb_dtsg`, `lsd`, and `jazoest`.

- **Location lookup** posts to `/api/graphql/` with `LOCATION_SEARCH_DOC_ID` and the session tokens.
- **Listing search** loads `https://www.facebook.com/marketplace/search/?query=...` once per name and reads embedded cards (`story_key`, title, price, city, seller). Mileage may be missing and is not required. `min_price` and `max_price` are sent as `minPrice` and `maxPrice`. Commas in `query` split the names. The same listing id is kept once.
- **Listing detail** loads `/marketplace/item/{id}/` and reads Open Graph tags plus embedded JSON.

`latitude`, `longitude`, and `radius_km` stay on `search_listings` so callers do not change. The search page follows the Chrome account's Marketplace city rather than those coordinates.

When that first page reports another page, search posts `CometMarketplaceSearchContentContainerQuery` with the page cursor, up to 10 pages. The query id is read from the HTML because it changes when Facebook deploys.

## CLI commands

`facebook-marketplace` uses the same client as the MCP server and does not start a model turn. `--json` prints records. `--chrome-profile` selects the Chrome profile (`CHROME_PROFILE` or `Default`). Mileage is not required and is not printed. Search follows the account's Marketplace city; `--latitude`, `--longitude`, and `--radius-km` are kept on saved monitors only.

### `facebook-marketplace search "cr-v 2023" --min-price 20000 --max-price 26000`

Loads the Marketplace search page for each name, then up to 10 feed pages, and keeps prices from 20000 to 26000 dollars. Several names, such as `crv 2023` and `cr-v 2023`, are searched separately and combined into one list. Prints slug, price, title, location, seller, and URL. `--limit` defaults to 20. `--category` filters by category id when that id is present on the card. `--province quebec` (or `QC`) searches the province of Quebec: Facebook is queried from Quebec City with a 650 km radius, then listings outside Quebec are dropped.

```bash
facebook-marketplace search "crv 2023" "cr-v 2023" --province quebec
```

Each item's slug is `slugified-title-<listing id>`, stored on first sight in `~/.fb-marketplace/catalog.json` and reused for that listing id. One name is saved under `server/runs/<query>/`. Several names share one folder, `server/runs/crv 2023, cr-v 2023/`, with `<slug>.json`, `<slug>.csv`, and `listings.csv`. `<slug>.png` is the listing photo. `<slug>-page.png` is a snapshot of the item `url`, shown in `server/runs/index.html`. The item keeps `created_at`, `updated_at`, and `original_price`. A later run with a lower price keeps the original, stores the lower price, and changes `updated_at`. `listing` and `monitor check` update that same file.

### `facebook-marketplace location "Montreal"`

GraphQL city lookup. Prints each match with latitude and longitude. `Montreal` returns Montreal, Quebec. A query like `Montreal QC` can return no rows.

### `facebook-marketplace listing 29168184989452025`

Fetches `/marketplace/item/29168184989452025/` and prints title, price, condition, location, seller, description, and URL. The id is the `story_key` from a search result.

### `facebook-marketplace monitor add crv --query "crv 2023" --query "cr-v 2023" --min-price 20000 --max-price 26000`

Writes a monitor named `crv` to `~/.fb-marketplace/monitors.json`. `--query` is required and can be repeated; those names are stored as the keywords being tracked and searched together. Running `add` again with the same name updates the keywords. `--limit` defaults to 24. This does not run the search.

### `facebook-marketplace monitor check`

Runs every saved search and prints listings whose ids are not in that monitor's seen list, then records those ids (last 500 kept). `facebook-marketplace monitor check crv` checks only `crv`. A missing name exits 1.

### `facebook-marketplace monitor list`

Prints each saved monitor's name, price bounds, seen count, and last check time, then one line per keyword being tracked. Does not call Facebook.

### `facebook-marketplace monitor delete crv`

Deletes the `crv` monitor and its seen ids. A missing name exits 1.

## MCP Tools

### `search_listings`
- **Inputs**: `query` (one name, or several separated by commas), `latitude`, `longitude`, `radius_km` (default 50), `min_price?`, `max_price?`, `category?`, `limit` (default 20 per name), `province?`
- **Output**: Combined text list of title, price, location, seller, and item URL. Duplicate listing ids are dropped.

### `search_location`
- **Inputs**: `query`
- **Output**: Matching places with latitude and longitude

### `get_listing`
- **Inputs**: `listing_id`
- **Output**: Title, price, condition, location, description, seller, images, and item URL

### `monitor_search`
- **Inputs**: `name`, `query` (one name, or several separated by commas), `latitude`, `longitude`, `radius_km`, `min_price?`, `max_price?`, `category?`, `province?`
- **Output**: Confirmation with monitor id and the keywords stored for that name. The same name updates the keywords.

### `check_monitors`
- **Inputs**: `monitor_name?` (optional — check one or all)
- **Output**: New listings since the last check

### `list_monitors`
- **Output**: Saved monitors, their keywords, created time, last check, and seen count

### `delete_monitor`
- **Inputs**: `name`
- **Output**: Deleted or not found

## Auth Flow

1. Copy `~/Library/Application Support/Google/Chrome/<profile>/Cookies` (Chrome locks the live file).
2. Read the Keychain password: `security find-generic-password -w -s "Chrome Safe Storage" -a "Chrome"`.
3. Derive an AES-128 key with PBKDF2-SHA1, salt `saltysalt`, 1003 iterations.
4. Decrypt values prefixed with `v10` (AES-CBC, IV of 16 spaces) and skip Chrome's 32-byte header.
5. Require a `c_user` cookie for `facebook.com`.
6. Fetch `facebook.com/marketplace/` and cache `fb_dtsg`, `lsd`, `jazoest`, and the client revision in memory.
7. On HTTP 401 or 403, drop the cached session. The next request extracts it again.

`CHROME_PROFILE` selects the Chrome profile directory. The default is `Default`.

## File Structure

```
server/
├── pyproject.toml
├── README.md
├── src/facebook_marketplace_mcp/
│   ├── __main__.py     # python -m facebook_marketplace_mcp
│   ├── server.py       # MCPServer tools (stdio)
│   ├── client.py       # Session, GraphQL location lookup, search and listing HTTP
│   ├── auth.py         # Chrome cookie copy, Keychain, AES decrypt
│   ├── queries.py      # Location GraphQL doc_id and variables
│   ├── parser.py       # Search HTML and listing page parsing
│   ├── monitors.py     # ~/.fb-marketplace/monitors.json
│   └── rate_limit.py   # 3 requests/minute with jitter
└── docs/
```

## Rate Limiting

- Default cap is 3 requests per minute.
- When under the cap, later requests wait 0.5–2 seconds.
- At the cap, the server waits until the oldest request is a minute old, plus 1–3 seconds of jitter.
- The User-Agent matches desktop Chrome on macOS.

## Risks

- **ToS**: Automating Facebook violates their Terms of Service. Account ban risk.
- **Location doc_id**: `LOCATION_SEARCH_DOC_ID` in `queries.py` can change on a Facebook deploy. Listing search does not depend on a search `doc_id`.
- **HTML shape**: Search cards and listing pages are parsed from embedded page data. A Marketplace markup change can break titles, prices, or detail fields.
- **Token rotation**: `fb_dtsg` rotates per session and is read again after the cached session is cleared.
- **Search city**: Results follow the Marketplace city on the logged-in Chrome account.
- **Rate limits**: Facebook throttles aggressive scraping. The server limits itself, and heavy use can still trigger CAPTCHAs or account flags.
