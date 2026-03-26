import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
from requests.exceptions import ReadTimeout
import websocket
import json
import os, sys
import time
import math
import datetime
import pandas as pd

# Binance API Credentials
API_KEY = 'YOUR BINANCE API KEY HERE'
API_SECRET = 'YOUR BINANCE API SECRET HERE'

# Create a Binance Client instance
client = Client(API_KEY, API_SECRET)

# Parameters for grid trading
BASE = 'USDT'
PAIR = ''
if len(sys.argv) > 1:
    PAIR = sys.argv[1].upper()
else:
    PAIR = input('Which coin will you trade? ').upper()
SYMBOL = PAIR + BASE # change for different pair
SOCKET = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_1m"
GRID_SPACING = 2/999 + 1  # Minimum grid spacing
BUY_GRID_SPACING = 2/999 + 1
SELL_GRID_SPACING = 2/999 + 1
CANDLE_LIMIT = 21  # Number of candles to fetch for Bollinger Bands calculation
BB_WINDOW = 20  # Bollinger Band window size
BB_STD_DEV = 10  # Bollinger Band standard deviation multiplier
STEP_SIZE = 0 # In order to create order quantity
TICK_SIZE = 0 # In order to set correct prices
MIN_TRADE_AMOUNT = 5.0
#REFRESH_COUNTER = 0 # Counter to update all orders after 24h
TRADE_COUNTER = 0
SMA = 0.0
BB = 0.0
GRID_MULTIPLIER = 1

# Path to store open orders
ORDERS_FILE = f"{SYMBOL.lower()}.json"

# Global variables to track open orders and grid levels
open_orders = []
buy_grid_levels = []
sell_grid_levels = []
BASE_ASSET = 0.0
PAIR_ASSET = 0.0
BUY_TRADE_AMOUNT = 0.0  # Will be calculated dynamically
SELL_TRADE_AMOUNT = 0.0
BUY_AVARAGE = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
SELL_AVARAGE = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
BUY_QUANTITY = 0.0
SELL_QUANTITY = 0.0
TOTAL_PROFIT = 0.0

midnight = False
new_pair = None
multi_coin = True

# Function to fetch the available balance for a given asset
def get_available_balance(asset):
    balance_info = client.get_asset_balance(asset)
    return (float(balance_info['free']) + float(balance_info['locked']))

# Function to initialize
def initialize_all(current_price=None):
    global BASE_ASSET, PAIR_ASSET, SMA, BUY_AVARAGE, SELL_AVARAGE, BUY_QUANTITY, SELL_QUANTITY, TOTAL_PROFIT
    
    # Get exchange info
    get_exchange_info(SYMBOL)
    
    # Get avarage sell and buy prices of past 365 days
    start_time = int((datetime.datetime.utcnow() - datetime.timedelta(days=365)).timestamp() * 1000)
    trades = client.get_my_trades(symbol=SYMBOL, startTime=start_time)
    buy_trades = [t for t in trades if t["isBuyer"]]
    sell_trades = [t for t in trades if not t["isBuyer"]]
    
    buys, BUY_QUANTITY = weighted_avg_price(buy_trades, BUY_AVARAGE)
    BUY_AVARAGE = round(buys*(2/999+1), int(TICK_SIZE))
    print(f"Buy volume: {round(BUY_QUANTITY,8)} {PAIR} Avarage buy price: {BUY_AVARAGE} {BASE}")
    sells, SELL_QUANTITY = weighted_avg_price(sell_trades, SELL_AVARAGE)
    SELL_AVARAGE = round(sells/(2/999+1), int(TICK_SIZE))
    print(f"Sell volume: {round(SELL_QUANTITY,8)} {PAIR} Avarage sell price: {SELL_AVARAGE} {BASE}")
    
    
    # Get the current price
    current_price = current_price or float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
    
    BASE_ASSET = max(get_available_balance(BASE), 0.00000001)
    PAIR_ASSET = max(get_available_balance(PAIR) * current_price, 0.00000001)
        
    # Profits
    realized_profit = min(BUY_QUANTITY, SELL_QUANTITY) * (SELL_AVARAGE - BUY_AVARAGE)
    unrealized_profit = 0.0
    if BUY_QUANTITY >= SELL_QUANTITY:
        unrealized_profit = (BUY_QUANTITY - SELL_QUANTITY) * (current_price - BUY_AVARAGE)
    else:
        unrealized_profit = (SELL_QUANTITY - BUY_QUANTITY) * (SELL_AVARAGE - current_price)
    TOTAL_PROFIT = realized_profit + unrealized_profit
    print(f"365 day realized profit: {round(realized_profit,8)} {BASE}") 
    print(f"365 day unrealized profit: {round(unrealized_profit,8)} {BASE}")
    print(f"365 day total profit: {round(TOTAL_PROFIT,8)} {BASE}")
    
    # Calculate Bollinger Bands (upper and lower boundaries for the grid)
    upper_band, lower_band, SMA = get_bollinger_bands(SYMBOL, CANDLE_LIMIT, BB_WINDOW, BB_STD_DEV)
          
    # Get order amount
    calculate_order_amount(BASE_ASSET, PAIR_ASSET, current_price, upper_band, lower_band, GRID_SPACING)
        
    # Get grid spacing
    calculate_grid_spacing(BASE_ASSET, PAIR_ASSET, BUY_TRADE_AMOUNT, SELL_TRADE_AMOUNT, current_price, upper_band, lower_band)

    # Update grid levels based on Bollinger Bands
    create_grid_levels(SMA, BUY_GRID_SPACING, SELL_GRID_SPACING)
    
def weighted_avg_price(trades, price):
    total_cost = sum(float(t["price"]) * float(t["qty"]) for t in trades)
    total_qty = sum(float(t["qty"]) for t in trades)
    if total_qty == 0:
        return price, total_qty
    else:
        return total_cost / total_qty, total_qty
    
def get_exchange_info(symbol):
    global STEP_SIZE, MIN_TRADE_AMOUNT, TICK_SIZE
    try:
        exchange_info = client.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                for filter in s['filters']:
                    if filter['filterType'] == 'LOT_SIZE':
                        step_size = filter['stepSize']
                    if filter['filterType'] == 'NOTIONAL':
                        MIN_TRADE_AMOUNT = float(filter['minNotional'])
                    if filter['filterType'] == 'PRICE_FILTER':
                        tick_size = filter['tickSize']
    except Exception as e:
        print(f"Error fetching exchange info: {e}")
        return None
            
    if step_size.find("1") == 0:
        STEP_SIZE = 1 - step_size.find(".")
    else:
        STEP_SIZE = step_size.find("1") - 1
        
    if tick_size.find("1") == 0:
        TICK_SIZE = 1 - tick_size.find(".")
    else:
        TICK_SIZE = tick_size.find("1") - 1    
        
# Fetch the last 21 daily candles and calculate Bollinger Bands
def get_bollinger_bands(symbol, candle_limit, window, std_dev_multiplier):
    global GRID_SPACING, BB, GRID_MULTIPLIER
    try:
        # Fetch daily candles (1d interval) for the symbol
        candles = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1DAY, limit=candle_limit)
        closing_prices = np.array([float(candle[4]) for candle in candles])  # '4' is the closing price

        # Calculate the moving average
        sma = np.mean(closing_prices[-window:])

        # Calculate standard deviation of the last 20 periods
        rolling_std = np.std(closing_prices[-window:])
        BB = rolling_std / GRID_MULTIPLIER / closing_prices[-1]
        GRID_SPACING = max(BB + 1 - (TOTAL_PROFIT / (PAIR_ASSET + BASE_ASSET) / 12), 2/999 + 1)

        # Calculate the Bollinger Bands
        upper_band = min(closing_prices[-1]*5, sma + (rolling_std * std_dev_multiplier))
        lower_band = max(closing_prices[-1]/5, sma - (rolling_std * std_dev_multiplier))
        
#         while upper_band >= closing_prices[-1]*5:
#             upper_band -= rolling_std
#             
#         while lower_band <= closing_prices[-1]/5:
#             lower_band += rolling_std

        print(f"Bollinger Bands: Upper = {round(sma+rolling_std,int(TICK_SIZE))}, SMA = {round(sma,int(TICK_SIZE))}, Lower = {round(sma-rolling_std,int(TICK_SIZE))}")

        return upper_band, lower_band, sma
    except Exception as e:
        print(f"Error fetching candle data: {e}")
        return None, None, None

# Calculate the order amount per grid based on total investment or minimum trade amount
def calculate_order_amount(base_asset, pair_asset, current_price, upper_band, lower_band, grid_spacing):
    global BUY_TRADE_AMOUNT, SELL_TRADE_AMOUNT
    min_trade_amount = MIN_TRADE_AMOUNT
    
    # Calculate based on grid size or set to minimum if calculated is less than the minimum
    buy_calculated_amount = base_asset * math.log(grid_spacing) / math.log(upper_band/min(SMA,current_price))
    BUY_TRADE_AMOUNT = max(buy_calculated_amount, min_trade_amount)
    
    sell_calculated_amount = pair_asset * math.log(grid_spacing) / math.log(min(SMA,current_price)/lower_band)
    SELL_TRADE_AMOUNT = max(sell_calculated_amount, min_trade_amount)
    
    print(f"Buy trade amount: {BUY_TRADE_AMOUNT} {BASE}\nSell trade amount: {SELL_TRADE_AMOUNT} {BASE}")
    
# Calculate grid spacing
def calculate_grid_spacing(base_asset, pair_asset, buy_trade_amount, sell_trade_amount, current_price, upper_band, lower_band):
    global BUY_GRID_SPACING, SELL_GRID_SPACING
    
    #trade_amount = min(buy_trade_amount, sell_trade_amount)
    if base_asset < buy_trade_amount:
        base_asset = buy_trade_amount
    if pair_asset < sell_trade_amount:
        pair_asset = sell_trade_amount
    BUY_GRID_SPACING = (min(SMA,current_price)/lower_band)**(buy_trade_amount/base_asset)
    SELL_GRID_SPACING = (upper_band/min(SMA,current_price))**(sell_trade_amount/pair_asset)
    
    print(f"Grid spacing: {100*(GRID_SPACING-1):.4f}%\nCalculated geometric buy grid spacing: {100*(BUY_GRID_SPACING-1):.4f}%\nCalculated geometric sell grid spacing: {100*(SELL_GRID_SPACING-1):.4f}%")
    
# Set up grid levels (buy/sell orders) with minimum percentage spacing within Bollinger Bands
def create_grid_levels(sma, buy_grid_spacing, sell_grid_spacing):
    global buy_grid_levels, sell_grid_levels
    buy_grid_levels.clear()
    sell_grid_levels.clear()
    lower_band = round(SMA / (BB + 1), int(TICK_SIZE))
    upper_band = round(SMA * (BB + 1), int(TICK_SIZE))
    
    buy_level = upper_band
    sell_level = lower_band
    iterator = 0
    
    while buy_level > lower_band:
        buy_price = upper_band / buy_grid_spacing**iterator
        buy_grid_levels.append(buy_price)
        buy_level = buy_price
        iterator += 1
        
    iterator = 0
        
    while sell_level < upper_band:
        sell_price = lower_band * sell_grid_spacing**(iterator)
        sell_grid_levels.append(sell_price)
        sell_level = sell_price
        iterator += 1

# Function to save open orders to a JSON file
def save_open_orders():
    try:
        with open(ORDERS_FILE, 'w') as f:
            json.dump(open_orders, f)
        print(f"Open orders saved to {ORDERS_FILE}")
    except Exception as e:
        print(f"Error saving open orders: {e}")

# Function to load open orders from a JSON file
def load_open_orders():
    global open_orders
    if os.path.exists(ORDERS_FILE):
        try:
            with open(ORDERS_FILE, 'r') as f:
                open_orders = json.load(f)
            print(f"Loaded open orders from {ORDERS_FILE}")
        except Exception as e:
            print(f"Error loading open orders: {e}")
            cancel_all_open_orders()
            refresh_grid_orders()
    else:
        print(f"No open orders file found, starting fresh.")
        # Initially place grid orders
        place_grid_orders()

# Function to place buy/sell limit orders and track them
def place_grid_orders():
    global open_orders
    current_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
    
    for buy_price in buy_grid_levels:
        try:
            if buy_price < current_price:
                # Place buy limit order and track it
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(BUY_TRADE_AMOUNT * 10**STEP_SIZE / buy_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(buy_price, int(TICK_SIZE))}"
                )
                open_orders.append(buy_order)
                print(f"Buy order placed at {round(buy_price, int(TICK_SIZE))}")
    
            else:
                # Buy initial amount to have something to trade with
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(current_price, int(TICK_SIZE))}"
                )
                print(f"Buy order placed at {round(current_price, int(TICK_SIZE))}")
        except Exception as e:
            # Handle any exceptions that occur during order placement
            print(f"Error placing order: {e}")
            continue  # Continue with the next iteration of the loop
            
    for sell_price in sell_grid_levels:
        try:
            if sell_price > current_price:
                # Place sell limit order and track it
                sell_order = client.order_limit_sell(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(SELL_TRADE_AMOUNT * 10**STEP_SIZE / sell_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(sell_price, int(TICK_SIZE))}"
                )
                open_orders.append(sell_order)
                print(f"Sell order placed at {round(sell_price, int(TICK_SIZE))}")
        except Exception as e:
            # Handle any exceptions that occur during order placement
            print(f"Error placing order: {e}")
            continue  # Continue with the next iteration of the loop
    
    # Save open orders to a file after placing them
    save_open_orders()
        
def cancel_all_open_orders(symbol=SYMBOL):
    try:
        # Fetch all open orders for the symbol
        open_orders = client.get_open_orders(symbol=symbol)
        
        # Loop through and cancel each order
        for order in open_orders:
            order_id = order['orderId']
            client.cancel_order(symbol=symbol, orderId=order_id)
            print(f"Canceled order {order_id} for {symbol}")
        
        print(f"All open orders for {symbol} have been canceled.")
    
    except Exception as e:
        print(f"Error canceling open orders: {e}")
        
def buy_low(symbol, quantity, buy_price, grid):
    global TRADE_COUNTER
    try:
        buy_low_order = client.order_limit_buy(
            symbol=symbol,
            quantity=quantity,
            price=f"{buy_price}"
            )

        print(f"Buying at {buy_price} for {float(quantity) * buy_price} {BASE}")
        if grid == True:
            sell_price = max(round(SMA / ((GRID_SPACING - 1) * 10 + 1) * (2/999+1), int(TICK_SIZE)), BUY_AVARAGE)
            quantity = "{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / sell_price) / float(10**STEP_SIZE), 8)
            grid = False
            sell_high(symbol, quantity, sell_price, grid)
        TRADE_COUNTER = 0
            
    except Exception as e:
        pass
        
def sell_high(symbol, quantity, sell_price, grid):
    global TRADE_COUNTER
    try:
        sell_high_order = client.order_limit_sell(
            symbol=symbol,
            quantity=quantity,
            price=f"{sell_price}"
            )
    
        print(f"Selling at {sell_price} for {float(quantity) * sell_price} {BASE}")
        if grid == True:
            buy_price = min(round(SMA * ((GRID_SPACING - 1) * 10 + 1) / (2/999+1), int(TICK_SIZE)), SELL_AVARAGE)
            quantity = "{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / buy_price) / float(10**STEP_SIZE), 8)
            grid = False
            buy_low(symbol, quantity, buy_price, grid)
        TRADE_COUNTER = 0
            
    except Exception as e:
        pass

# Function to place buy/sell limit orders and track them
def refresh_grid_orders():
    global open_orders
    open_orders = []
    current_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
    
    for buy_price in buy_grid_levels:
        try:
            if buy_price < current_price and (buy_price < BUY_AVARAGE or buy_price < SELL_AVARAGE):
                # Place buy limit order and track it
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(BUY_TRADE_AMOUNT * 10**STEP_SIZE / buy_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(buy_price, int(TICK_SIZE))}"
                )
                open_orders.append(buy_order)
                print(f"Buy order placed at {round(buy_price, int(TICK_SIZE))}")
            elif buy_price < current_price:
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / buy_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(buy_price, int(TICK_SIZE))}"
                )
                open_orders.append(buy_order)
                print(f"Buy order placed at {round(buy_price, int(TICK_SIZE))}")
        except Exception as e:
            # Handle any exceptions that occur during order placement
            print(f"Error placing order: {e}")
            continue  # Continue with the next iteration of the loop
            
    for sell_price in sell_grid_levels:
        try:
            if sell_price > current_price and (sell_price > SELL_AVARAGE or sell_price > BUY_AVARAGE):
                # Place sell limit order and track it
                sell_order = client.order_limit_sell(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(SELL_TRADE_AMOUNT * 10**STEP_SIZE / sell_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(sell_price, int(TICK_SIZE))}"
                )
                open_orders.append(sell_order)
                print(f"Sell order placed at {round(sell_price, int(TICK_SIZE))}")
            elif sell_price > current_price:
                sell_order = client.order_limit_sell(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / sell_price) / float(10**STEP_SIZE), 8),
                    price=f"{round(sell_price, int(TICK_SIZE))}"
                )
                open_orders.append(sell_order)
                print(f"Sell order placed at {round(sell_price, int(TICK_SIZE))}")
        except Exception as e:
            # Handle any exceptions that occur during order placement
            print(f"Error placing order: {e}")
            continue  # Continue with the next iteration of the loop
    
    # Save open orders to a file after placing them
    save_open_orders()

# Function to check if an order is filled and replace with the opposite order
def check_order_status():
    global open_orders
    try:
        for order in open_orders:
            status = client.get_order(symbol=SYMBOL, orderId=order['orderId'])
            if status['status'] == 'FILLED':
                if status['side'] == 'BUY':
                    # Place a new sell order at a higher price
                    new_sell_price = float(status['price']) * SELL_GRID_SPACING
                    new_sell_order = client.order_limit_sell(
                        symbol=SYMBOL,
                        quantity="{:0.0{}f}".format(math.ceil(SELL_TRADE_AMOUNT * 10**STEP_SIZE / new_sell_price) / float(10**STEP_SIZE), 8),
                        price=f"{round(new_sell_price, int(TICK_SIZE))}"
                    )
                    open_orders.append(new_sell_order)
                    print(f"New sell order placed at {round(new_sell_price, int(TICK_SIZE))} after buy filled at {status['price']}")
                elif status['side'] == 'SELL':
                    # Place a new buy order at a lower price
                    new_buy_price = float(status['price']) / BUY_GRID_SPACING
                    new_buy_order = client.order_limit_buy(
                        symbol=SYMBOL,
                        quantity="{:0.0{}f}".format(math.ceil(BUY_TRADE_AMOUNT * 10**STEP_SIZE / new_buy_price) / float(10**STEP_SIZE), 8),
                        price=f"{round(new_buy_price, int(TICK_SIZE))}"
                    )
                    open_orders.append(new_buy_order)
                    print(f"New buy order placed at {round(new_buy_price, int(TICK_SIZE))} after sell filled at {status['price']}")
                open_orders.remove(order)  # Remove filled order from tracking
    
        # Save open orders after checking status
        save_open_orders()
    except ReadTimeout:
        print("Error: The request timed out while trying to fetch the order status.")
        # Handle timeout, retry, or return a default response
        # return {"error": "ReadTimeout", "message": "Request timed out"}
        pass
    except BinanceAPIException as e:
        if "Filter failure: PRICE_FILTER" in str(e):
            # Ignore this specific error
            pass
        else:
            print(f"An API error occurred: {e}. Trying to resolve by replacing orders")
            cancel_all_open_orders()
            refresh_grid_orders()
    except Exception as e:
        print(f"Error checking order status: {e}. Replacing all orders to resolve problems.")
        cancel_all_open_orders()
        refresh_grid_orders()
        
# def redeem(amount):
    # """
    # Redeem USDT from Binance Earn Flexible Savings.
    # Args:
        # amount (str): Amount of USDT to redeem. Use "FULL" for full redemption.
        # product_id (str): Product ID for flexible savings (default: USDT).
    # """
    # try:
        # # Redeem Flexible Savings
        # response = client.redeem_simple_earn_flexible_product(productId=BASE+'001', amount=amount)
        # print("Redemption successful:", response)
    # except Exception as e:
        # print("Error redeeming USDT:", e)
        
# def redeem_all():
    # """
    # Redeem USDT from Binance Earn Flexible Savings.
    # Args:
        # amount (str): Amount of USDT to redeem. Use "FULL" for full redemption.
        # product_id (str): Product ID for flexible savings (default: USDT).
    # """
    # try:
        # # Redeem Flexible Savings
        # response = client.redeem_simple_earn_flexible_product(productId=BASE+'001', redeemAll=True)
        # print("Redemption successful:", response)
    # except Exception as e:
        # print("Error redeeming USDT:", e)

# WebSocket to track live price
def on_message(ws, message):
    global TRADE_COUNTER, midnight, new_pair, PAIR, open_orders  #, REFRESH_COUNTER
    json_message = json.loads(message)
    
    # Check order status every 10 price updates
    if int(json_message['k']['x']) == True:
        #REFRESH_COUNTER += 1
        current_price = float(json_message['k']['c'])  # 'c' is the close price of the candlestick
        print(f"Current price: {current_price}")
    
        # Update all trading values
        initialize_all(current_price=current_price)
        
        if SMA / current_price >= BB * 3 + 1 and current_price <= SELL_AVARAGE and BASE_ASSET >= MIN_TRADE_AMOUNT:
            print(f"Buying dip every {(round(1440 * MIN_TRADE_AMOUNT / BASE_ASSET))} minutes - T-Minus: {(round(1440 * MIN_TRADE_AMOUNT / BASE_ASSET)) - TRADE_COUNTER % (round(1440 * MIN_TRADE_AMOUNT / BASE_ASSET)) - 1} minutes")
            TRADE_COUNTER += 1
        elif SMA / current_price >= BB * 2 + 1 and current_price <= SELL_AVARAGE and BASE_ASSET >= MIN_TRADE_AMOUNT:
            print(f"Buying low every {(round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET))} minutes - T-Minus: {(round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET)) - TRADE_COUNTER % (round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET)) - 1} minutes")
            TRADE_COUNTER += 1
        elif SMA / current_price >= BB * 1 + 1 and current_price >= BUY_AVARAGE and PAIR_ASSET >= MIN_TRADE_AMOUNT:
            print(f"Selling downtrend every {(round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET))} minutes - T-Minus: {(round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET)) - TRADE_COUNTER % (round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET)) - 1} minutes")
            TRADE_COUNTER += 1
            
        if current_price / SMA >= BB * 3 + 1 and current_price >= BUY_AVARAGE and PAIR_ASSET >= MIN_TRADE_AMOUNT:
            print(f"Selling peak every {(round(1440 * MIN_TRADE_AMOUNT / PAIR_ASSET))} minutes - T-Minus: {(round(1440 * MIN_TRADE_AMOUNT / PAIR_ASSET)) - TRADE_COUNTER % (round(1440 * MIN_TRADE_AMOUNT / PAIR_ASSET)) - 1} minutes")
            TRADE_COUNTER += 1
        elif current_price / SMA >= BB * 2 + 1 and current_price >= BUY_AVARAGE and PAIR_ASSET >= MIN_TRADE_AMOUNT:
            print(f"Selling high every {(round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET))} minutes - T-Minus: {(round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET)) - TRADE_COUNTER % (round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET)) - 1} minutes")
            TRADE_COUNTER += 1
        elif current_price / SMA >= BB * 1 + 1 and current_price <= SELL_AVARAGE and BASE_ASSET >= MIN_TRADE_AMOUNT:
            print(f"Buying uptrend every {(round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET))} minutes - T-Minus: {(round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET)) - TRADE_COUNTER % (round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET)) - 1} minutes")
            TRADE_COUNTER += 1
            
        #Bear logic
        if SMA / current_price >= BB * 3 + 1 and TRADE_COUNTER % (round(1440 * MIN_TRADE_AMOUNT / BASE_ASSET)) == 0 and current_price <= SELL_AVARAGE:
            buy_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = False
            buy_low(SYMBOL, quantity, buy_price, grid)
        elif SMA / current_price >= BB * 2 + 1 and TRADE_COUNTER % (round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET)) == 0 and current_price <= SELL_AVARAGE:
            buy_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(BUY_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = True
            buy_low(SYMBOL, quantity, buy_price, grid)
        elif SMA / current_price >= BB * 1 + 1 and TRADE_COUNTER % (round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET)) == 0 and current_price >= BUY_AVARAGE:
            sell_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(SELL_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = False
            sell_high(SYMBOL, quantity, sell_price, grid)
        elif max(BUY_AVARAGE, SELL_AVARAGE) / current_price > GRID_SPACING and current_price < min(BUY_AVARAGE, SELL_AVARAGE) and BUY_QUANTITY <= SELL_QUANTITY:
            buy_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(BUY_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = False
            buy_low(SYMBOL, quantity, buy_price, grid)
        
        #Bull logic
        if current_price / SMA >= BB * 3 + 1 and TRADE_COUNTER % (round(1440 * MIN_TRADE_AMOUNT / PAIR_ASSET)) == 0 and current_price >= BUY_AVARAGE:
            sell_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(MIN_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = False
            sell_high(SYMBOL, quantity, sell_price, grid)
        elif current_price / SMA >= BB * 2 + 1 and TRADE_COUNTER % (round(1440 * SELL_TRADE_AMOUNT / PAIR_ASSET)) == 0 and current_price >= BUY_AVARAGE:
            sell_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(SELL_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = True
            sell_high(SYMBOL, quantity, sell_price, grid)
        elif current_price / SMA >= BB * 1 + 1 and TRADE_COUNTER % (round(1440 * BUY_TRADE_AMOUNT / BASE_ASSET)) == 0 and current_price <= SELL_AVARAGE:
            buy_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(BUY_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = False
            buy_low(SYMBOL, quantity, buy_price, grid)
        elif current_price / min(BUY_AVARAGE, SELL_AVARAGE) > GRID_SPACING and current_price > max(BUY_AVARAGE, SELL_AVARAGE) and BUY_QUANTITY > SELL_QUANTITY:
            sell_price = current_price
            quantity = "{:0.0{}f}".format(math.ceil(SELL_TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8)
            grid = False
            sell_high(SYMBOL, quantity, sell_price, grid)
        

        check_order_status()
        
        #time.sleep(3)
        t = time.gmtime()
        #if REFRESH_COUNTER % 1440 == 0:
            # try:
                # if current_price >= SMA:
                    # redeem(str(MIN_TRADE_AMOUNT))
                # else:
                    # redeem_all()
            # except Exception as e:
                # pass
                
        if t.tm_hour == 0 and t.tm_min == 1:
            
           # Get delist schedule
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
                    
            midnight = True
            
            if multi_coin and TOTAL_PROFIT >= 0 or SYMBOL in delisting_soon:
                new_pair = run_volatility(delisting_soon)
                
        if midnight:
            cancel_all_open_orders()

            if new_pair is not None and new_pair != PAIR:
                print(f"Changing pair to {new_pair}!")
                sell_all()
                time.sleep(2)
                open_orders = []
                save_open_orders()
                PAIR = new_pair
                # Restart the script with new pair
                script_path = os.path.abspath(sys.argv[0])
                os.execv(sys.executable, [sys.executable, script_path, PAIR])
                
            refresh_grid_orders()
            midnight = False
                
def run_volatility(delisting_soon):
    delisting_soon = delisting_soon
    
    # Get all USDT pairs
    exchange_info = client.get_exchange_info()
    
    symbols = [s['symbol'] for s in exchange_info['symbols'] if s['status'] == 'TRADING']

    # Filter for USDT pairs only (exclude leveraged tokens etc. if needed)
    all_usdt_pairs = [s for s in symbols if s.endswith('USDT') and not s.endswith('DOWNUSDT') and not s.endswith('UPUSDT')]
    
#     # Get delist schedule
#     delist_data = client.get_spot_delist_schedule()
# 
#     # Current time and cutoff (24h ahead)
#     now = int(time.time() * 1000)              # ms
#     cutoff = now + 24 * 60 * 60 * 1000        # +24h
# 
#     # Build set of pairs that will disappear within 24h
#     delisting_soon = set()
#     for entry in delist_data:
#         delist_time = entry.get("delistTime", 0)
#         if delist_time <= cutoff:  # only exclude if delist in <=24h
#             delisting_soon.update(entry.get("symbols", []))

    # Filter pairs
    active_usdt_pairs = [s for s in all_usdt_pairs if s not in delisting_soon]
    
    # Collect results
    results = []
    counter = 0
    print("Scanned pairs for highest volatility rank:")
    for symbol in active_usdt_pairs:
        vol = get_volatility(symbol)
        if vol is not None:
            results.append({'symbol': symbol, 'volatility_rank': vol})
            counter += 1
            print(f"{counter}", end="\r")
        time.sleep(0.06)
    print(f"{counter}")

    # Sort by volatility
    df = pd.DataFrame(results)
    df = df.sort_values(by='volatility_rank', ascending=False)

    # Print the symbol without "USDT"
    if not df.empty:
        top_symbol = df.iloc[0]['symbol'].replace('USDT', '')
        print(top_symbol)
        return top_symbol.strip().upper()
    else:
        print("No data found.")
        return None
        
# Function to compute volatility rank
def get_volatility(symbol, limit=20):
    try:
        klines = client.get_klines(symbol=symbol, interval='1d', limit=limit)
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[7]) for k in klines]
        avg_vol = sum(volumes) / len(volumes)
        log1p_vol = math.log1p(avg_vol)
        if len(closes) < 2:
            return None
        stdev = pd.Series(closes).std()
        last_close = closes[-1]
        return (stdev / last_close) * log1p_vol
    except Exception:
        return None
                
def sell_all():
    sell_amount = get_available_balance(PAIR)
    try:
        # Place sell market order
        sell_order = client.order_market_sell(
            symbol=SYMBOL,
            quantity="{:0.0{}f}".format(math.floor(sell_amount * 10**STEP_SIZE) / float(10**STEP_SIZE), 8)
        )
        print(f"Selling all {PAIR} to {BASE}")
        # return None
    except Exception as e:
        # Handle any exceptions that occur during order placement
        print(f"Error cancelling order: {e}")
        # return e

def on_error(ws, error):
    print(f"WebSocket Error: {error}")
    #reconnect(ws)

def on_close(ws):
    print("WebSocket connection closed")
    #reconnect(ws)

def on_open(ws):
    print("WebSocket connection opened")
    
# def reconnect(ws):
    # time.sleep(5)  # Wait for 5 seconds before attempting to reconnect
    # print("Reconnecting...")
    # ws.run_forever()

# Start WebSocket connection to listen to live BTCUSDT price data
ws = websocket.WebSocketApp(SOCKET, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)

# Initialize before starting WebSocket connection
initialize_all()

# Load existing open orders if any
load_open_orders()

# Run WebSocket
def connect():
    while True:
        try:
            print("Trying to connect...")
            ws.run_forever()
        except Exception as e:
            print("Exception in WebSocket:", e)
        print("Disconnected... reconnecting in 5 seconds")
        time.sleep(5)

if __name__ == "__main__":
    connect()


