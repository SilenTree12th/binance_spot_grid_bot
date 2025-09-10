from binance.client import Client
import time
from datetime import datetime

# Init client
client = Client(api_key="", api_secret="")

# 1. Get all USDT pairs
exchange_info = client.get_exchange_info()
symbols = [s['symbol'] for s in exchange_info['symbols'] if s['status'] == 'TRADING']

# Filter for USDT pairs only (exclude leveraged tokens etc. if needed)
all_usdt_pairs = [s for s in symbols if s.endswith('USDT') and not s.endswith('DOWNUSDT') and not s.endswith('UPUSDT')]

# 2. Get delist schedule
delist_data = client.get_spot_delist_schedule()

# Current time and cutoff (24h ahead)
now = int(time.time() * 1000)              # ms
cutoff = now + 24 * 60 * 60 * 1000        # +24h

# Build set of pairs that will disappear within 24h
delisting_soon = set()
for entry in delist_data:
    delist_time = entry.get("delistTime", 0)
    if delist_time <= cutoff:  # only exclude if delist in <=24h
        delisting_soon.update(entry.get("symbols", []))

# 3. Filter pairs
active_usdt_pairs = [s for s in all_usdt_pairs if s not in delisting_soon]

print("Total USDT pairs:", len(all_usdt_pairs))
print("Delisting within 24h:", delisting_soon)
print("Active USDT pairs (safe for >=24h):", len(active_usdt_pairs))
print(active_usdt_pairs[:20])  # show a preview

# 4. Print human-readable delist times for all entries in delist_data
print("\n=== Upcoming Delistings ===")
for entry in delist_data:
    delist_time = entry.get("delistTime")
    symbols = entry.get("symbols", [])
    # convert ms → seconds → datetime
    dt = datetime.fromtimestamp(delist_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{dt}  →  {', '.join(symbols)}")
