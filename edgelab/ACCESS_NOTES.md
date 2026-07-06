# ACCESS_NOTES.md — Phase 0 access verification

**Verified live on the eu-west-1 server (co-located), 2026-06-11.** Do not
assume these still hold months later — re-run `edgelab/ws_probe.py` to confirm.

## Polymarket CLOB market WebSocket — VERIFIED, no auth

- URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Subscribe: `{"type":"market","assets_ids":[<up_token>,<down_token>]}`
- No credentials needed for the `market` channel (read-only book data).
- Co-located on the server: ~0.9 ms to the Cloudflare edge (per `arb_ws.py`).

### Event shapes (re-confirmed by live probe, 12 s on one 5m window)
- `book` — full snapshot. **Top-level `asset_id`.** `{bids:[{price,size}...],
  asks:[{price,size}...]}`. Observed 50 bid / 49 ask levels. Bids ARE present
  (the existing `arb_ws.py` simply ignores them — it only tracked asks).
- `price_change` — incremental deltas under key **`price_changes`** (NOT
  `changes`). Each entry carries its OWN `asset_id` + `{price, side, size}`.
  `side`: `BUY`=bid, `SELL`=ask. `size: 0` removes the level. Each entry also
  carries `best_bid`, `best_ask`, and a `hash` — free top-of-book + dedup.

### Data rate (the key fact for the decay curve)
- One window, 12 s: **96 `book` + 4174 `price_change`** events ≈ **~350 ev/s**.
- ⇒ sub-10 ms event granularity. The 100 ms decay bucket is amply resolvable;
  the per-binary latency wall is directly measurable. **Both sides + sizes
  stream**, so CKS OFI is computable.

## REST resync (restart-safety / gap detection)
- `PolymarketClient.get_full_book(token_id)` → `{bids:[[p,s]...],asks:[[p,s]...],
  hash, min_order_size}`, full depth with sizes. Used as a periodic snapshot to
  validate/repair the WS-reconstructed book against the book `hash`.

## Window enumeration (reused from the live bot)
- 5m slug: `btc-updown-5m-<window_start_epoch>` where `start = (now//300)*300`,
  `end = start+300`. 15m analogous on 900 s boundaries.
- Gamma `https://gamma-api.polymarket.com/events?slug=<slug>` →
  `markets[0].clobTokenIds` (JSON list) + `outcomes` (["Up","Down"]) → Up/Down
  token ids; `conditionId` for the trade tape.
- Outcome/strike: Gamma `fetch_resolution` gives the realized outcome. NOTE: the
  live bot reconstructs the "Price to Beat" from **Binance** at window open —
  this is a PROXY, not the resolving feed.

## Resolving feed — THE PROXY TRAP (Edge A only; B does not need it)
- Markets resolve on the **Chainlink Data Streams BTC/USD aggregate**, not any
  single CEX. We do NOT have verified Chainlink Data Streams access here; it
  typically requires a paid provider / credentials. **NOT pursued** — Edge A is
  glance-only.
- Any BTC price we compute fair value against is a **CEX proxy** (Binance et al).
  ⇒ Any positive Edge-A result on the proxy is **presumptively an artifact** of
  (proxy ↔ Chainlink basis) + (σ-estimation error), both of which manufacture
  fake edge. Treat as artifact until proven on the real feed. Edge B (OFI on the
  PM book's own next move) is independent of BTC and unaffected by this trap.

## Implication for the build
- Logger captures the WS stream (both tokens, both sides, µs local stamps) — the
  decay curve and Edge B run on this directly; no resolving feed required.
- Edge A is computed opportunistically on the same logs against a clearly
  labelled CEX proxy and reported with the artifact caveat above.
