# Drop Monitor Agent

Monitor any Shopify store for new product drops and restocks. Get instant Discord alerts when items appear or come back in stock.

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Discord server with a webhook URL ([how to create one](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks))
- The Shopify store URL you want to monitor

### 2. Install

```bash
git clone https://github.com/jesse-liang92/jesse-liang92.git
cd allyx-agents
pip install httpx pyyaml python-dotenv
```

### 3. Configure

**Add your Discord webhook to `.env`:**

```bash
cp .env.example .env
# Edit .env and set:
DISCORD_DROPS_WEBHOOK=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
```

**Point at your Shopify store in `agents/drop_monitor/config.yaml`:**

```yaml
stores:
  my_store:
    name: "My Store Name"
    collection_url: "https://example-store.com/collections/my-collection"
    products_json: "https://example-store.com/collections/my-collection/products.json"
```

> **How to find the products.json URL:** Take any Shopify collection URL and append `/products.json`. For example:
> - Store page: `https://example.com/collections/new-arrivals`
> - JSON endpoint: `https://example.com/collections/new-arrivals/products.json`
>
> Open it in your browser first to verify it returns JSON. If you get a 404, the store may not be on Shopify.

### 4. Seed existing products

This marks everything currently listed as "seen" so you don't get flooded with alerts for old products:

```bash
python agents/drop_monitor/agent.py --seed
```

### 5. Start watching

```bash
python agents/drop_monitor/agent.py --watch
```

Leave it running. New drops trigger a gold Discord embed with the product image, price, and a direct purchase link.

## Adding a New Shopify Store

Edit `config.yaml` and add an entry under `stores` (for new product alerts) or `restock_watches` (for back-in-stock alerts):

```yaml
# Alert when NEW products are listed
stores:
  my_store:
    name: "Human-Readable Store Name"
    collection_url: "https://store.com/collections/whatever"
    products_json: "https://store.com/collections/whatever/products.json"

# Alert when existing products come BACK IN STOCK
restock_watches:
  my_store_restock:
    name: "My Store Restock Watch"
    collection_url: "https://store.com/collections/all"
    products_json: "https://store.com/collections/all/products.json"
```

Then update the `_get_product_url` function in `agent.py` to map your store key to its base URL:

```python
base_urls = {
    "pvramid_chroma": "https://pvramid.com",
    "pvramid_instock": "https://pvramid.com",
    "my_store": "https://store.com",          # <-- add this
    "my_store_restock": "https://store.com",   # <-- and this
}
```

Run `--seed` again after adding a new store to baseline its products.

## Configuration Reference

### `config.yaml`

| Key | Description | Default |
|-----|-------------|---------|
| `schedule.interval_minutes` | Minutes between new-drop checks | 3 |
| `schedule.active_hours_start` | Start of polling window (PT, fractional ok: 6.01 = 6:00 AM + ~1 min) | 6.01 |
| `schedule.active_hours_end` | End of polling window (PT) | 21 |
| `restock_schedule.interval_minutes` | Minutes between restock checks | 30 |
| `stores.<key>.products_json` | Shopify collection JSON endpoint | required |
| `restock_watches.<key>.products_json` | Shopify collection JSON endpoint for restock monitoring | required |
| `database.path` | SQLite DB path (supports `~`) | `~/.config/allyx/drop_monitor.db` |

### `.env`

| Variable | Description |
|----------|-------------|
| `DISCORD_DROPS_WEBHOOK` | Discord webhook URL for alerts |
| `DISCORD_STATUS_WEBHOOK` | Discord webhook URL for error notifications (optional) |

## CLI Reference

```
python agent.py --watch      # Run continuously (drops every 3 min, restocks every 30 min)
python agent.py --seed       # Seed DB with current products (run once before --watch)
python agent.py --dry-run    # Single poll, print output, no Discord post
python agent.py --list       # Show all tracked products
python agent.py --reset      # Clear the database (will re-alert on next run)
python agent.py              # Single poll (for use with external scheduler)
```

## How It Works

1. Polls Shopify's public `/products.json` endpoint (no API key needed)
2. Compares product IDs against a local SQLite database
3. New products not in the DB trigger a **gold** Discord embed (new drop)
4. Products that flip from `available: false` to `available: true` trigger a **green** Discord embed (restock)
5. Respects Shopify rate limits (429 responses trigger automatic backoff)

## Rate Limiting

At 3-minute intervals you're making ~1 request per 180 seconds -- well below Shopify's thresholds. If the store uses Cloudflare or similar, the agent includes a `User-Agent` header and handles 429 responses with automatic backoff. Shopify does not permanently ban IPs; rate limits are temporary.

## Alert Examples

**New drop (gold):**
> :rotating_light: NEW DROP: Product Name
> Price: $260.00
> [View Product](https://store.com/products/handle)
> *with product image*

**Restock (green):**
> :green_circle: BACK IN STOCK: Product Name
> Price: $95.00
> [Buy Now](https://store.com/products/handle)
> *with product image*
