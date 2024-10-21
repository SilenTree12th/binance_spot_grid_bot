import numpy as np
from binance.client import Client
import websocket
import json
import os
import time
import math

# Binance API Credentials
API_KEY = ''
API_SECRET = ''

# Create a Binance Client instance
client = Client(API_KEY, API_SECRET)

# Parameters for grid trading
SYMBOL = 'BTCUSDT' # change for different pair
SOCKET = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m" #change for different pair
GRID_SPACING = 1.004  # Minimum grid spacing of 0.4%
CANDLE_LIMIT = 21  # Number of candles to fetch for Bollinger Bands calculation
BB_WINDOW = 20  # Bollinger Band window size
BB_STD_DEV = 10  # Bollinger Band standard deviation multiplier
STEP_SIZE = 0 # In order to create order quantity
REFRESH_COUNTER = 0 # Counter to update all orders after 24h

# Path to store open orders
ORDERS_FILE = "open_orders.json"

# Global variables to track open orders and grid levels
open_orders = []
grid_levels = []
TOTAL_INVESTMENT = 0  # Will be calculated dynamically
TRADE_AMOUNT = 0.0  # Will be calculated dynamically

# Function to fetch the available balance for a given asset
def get_available_balance(asset):
    balance_info = client.get_asset_balance(asset)
    return float(balance_info['free']) + float(balance_info['locked']) 

# Function to initialize
def initialize_all(current_price=None):
    global TOTAL_INVESTMENT
    
    # Get the current price
    current_price = current_price or float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
    
    TOTAL_INVESTMENT = get_available_balance('USDT') + get_available_balance('BTC') * current_price
    print(f"Total investment available: {TOTAL_INVESTMENT} USDT")
        
    # Calculate Bollinger Bands (upper and lower boundaries for the grid)
    upper_band, lower_band = get_bollinger_bands(SYMBOL, CANDLE_LIMIT, BB_WINDOW, BB_STD_DEV)
          
    # Get order amount
    calculate_order_amount(TOTAL_INVESTMENT, current_price, upper_band, lower_band, GRID_SPACING)
    
    # Get StepSize
    get_step_size(SYMBOL)
        
    # Get grid spacing
    calculate_grid_spacing(TOTAL_INVESTMENT, TRADE_AMOUNT, upper_band, lower_band)

    # Update grid levels based on Bollinger Bands
    create_grid_levels(upper_band, lower_band, GRID_SPACING)
    
def get_step_size(symbol):
    global STEP_SIZE
    try:
        exchange_info = client.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                for filter in s['filters']:
                    if filter['filterType'] == 'LOT_SIZE':
                        step_size = filter['stepSize']
                        break
    except Exception as e:
        print(f"Error fetching stepSize: {e}")
        return None
            
    if step_size.find("1") == 0:
        STEP_SIZE = 1 - step_size.find(".")
    else:
        STEP_SIZE = step_size.find("1") - 1
        
# Fetch the last 21 daily candles and calculate Bollinger Bands
def get_bollinger_bands(symbol, candle_limit, window, std_dev_multiplier):
    global GRID_SPACING
    try:
        # Fetch daily candles (1d interval) for the symbol
        candles = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1DAY, limit=candle_limit)
        closing_prices = np.array([float(candle[4]) for candle in candles])  # '4' is the closing price

        # Calculate the moving average
        sma = np.mean(closing_prices[-window:])

        # Calculate standard deviation of the last 20 periods
        rolling_std = np.std(closing_prices[-window:])
        GRID_SPACING = max(rolling_std / 6 / closing_prices[-1] + 1, 1.002)

        # Calculate the Bollinger Bands
        upper_band = sma + (rolling_std * std_dev_multiplier)
        lower_band = sma - (rolling_std * std_dev_multiplier)
        
        while lower_band <= 0:
            lower_band += rolling_std

        print(f"Bollinger Bands: Upper = {upper_band}, Lower = {lower_band}")

        return upper_band, lower_band
    except Exception as e:
        print(f"Error fetching candle data: {e}")
        return None, None

# Fetch minimum quantity for BTCUSDT from Binance exchange info
def get_minimum_trade_amount(symbol):
    try:
        exchange_info = client.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                for filter in s['filters']:
                    if filter['filterType'] == 'NOTIONAL':
                        min_quantity = float(filter['minNotional'])
                        return min_quantity
    except Exception as e:
        print(f"Error fetching minimum trade amount: {e}")
        return None

# Calculate the order amount per grid based on total investment or minimum trade amount
def calculate_order_amount(total_investment, current_price, upper_band, lower_band, grid_spacing):
    global TRADE_AMOUNT
    min_trade_amount = get_minimum_trade_amount(SYMBOL)
    
    # Calculate based on grid size or set to minimum if calculated is less than the minimum
    calculated_amount = total_investment * math.log(grid_spacing) / math.log(upper_band/lower_band)
    TRADE_AMOUNT = max(calculated_amount, min_trade_amount)
    
    print(f"Calculated order amount per grid: {TRADE_AMOUNT:.2f} USDT")
    
# Calculate grid spacing
def calculate_grid_spacing(total_investment, trade_amount, upper_band, lower_band):
    global GRID_SPACING
    GRID_SPACING = (upper_band/lower_band)**(trade_amount/total_investment)
    
    print(f"Calculated geometric grid spacing: {100*(GRID_SPACING-1)}%")
    
# Set up grid levels (buy/sell orders) with minimum percentage spacing within Bollinger Bands
def create_grid_levels(upper_band, lower_band, grid_spacing):
    grid_levels.clear()
    level = upper_band
    iterator = 0
    
    while level > lower_band:
        buy_price = upper_band / grid_spacing**iterator
        sell_price = lower_band * grid_spacing**(iterator + 1)
        grid_levels.append((buy_price, sell_price))
        
        level = buy_price
        iterator += 1
    
    return grid_levels

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
    else:
        print(f"No open orders file found, starting fresh.")
        # Initially place grid orders
        place_grid_orders()

# Function to place buy/sell limit orders and track them
def place_grid_orders():
    global open_orders
    current_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
    
    for buy_price, sell_price in grid_levels:
        try:
            if buy_price < current_price:
                # Place buy limit order and track it
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / buy_price) / float(10**STEP_SIZE), 8),
                    price=f"{buy_price:.2f}"
                )
                open_orders.append(buy_order)
                print(f"Buy order placed at {buy_price}")
    
            else:
                # Buy initial amount to have something to trade with
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / current_price) / float(10**STEP_SIZE), 8),
                    price=f"{current_price:.2f}"
                )
                print(f"Buy order placed at {current_price}")

            if sell_price > current_price:
                # Place sell limit order and track it
                sell_order = client.order_limit_sell(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / sell_price) / float(10**STEP_SIZE), 8),
                    price=f"{sell_price:.2f}"
                )
                open_orders.append(sell_order)
                print(f"Sell order placed at {sell_price}")
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


# Function to place buy/sell limit orders and track them
def refresh_grid_orders():
    global open_orders
    open_orders = []
    current_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
    
    for buy_price, sell_price in grid_levels:
        try:
            if buy_price < current_price:
                # Place buy limit order and track it
                buy_order = client.order_limit_buy(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / buy_price) / float(10**STEP_SIZE), 8),
                    price=f"{buy_price:.2f}"
                )
                open_orders.append(buy_order)
                print(f"Buy order placed at {buy_price}")

            if sell_price > current_price:
                # Place sell limit order and track it
                sell_order = client.order_limit_sell(
                    symbol=SYMBOL,
                    quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / sell_price) / float(10**STEP_SIZE), 8),
                    price=f"{sell_price:.2f}"
                )
                open_orders.append(sell_order)
                print(f"Sell order placed at {sell_price}")
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
                    new_sell_price = float(status['price']) * GRID_SPACING
                    new_sell_order = client.order_limit_sell(
                        symbol=SYMBOL,
                        quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / new_sell_price) / float(10**STEP_SIZE), 8),
                        price=f"{new_sell_price:.2f}"
                    )
                    open_orders.append(new_sell_order)
                    print(f"New sell order placed at {new_sell_price} after buy filled at {status['price']}")
                elif status['side'] == 'SELL':
                    # Place a new buy order at a lower price
                    new_buy_price = float(status['price']) / GRID_SPACING
                    new_buy_order = client.order_limit_buy(
                        symbol=SYMBOL,
                        quantity="{:0.0{}f}".format(math.ceil(TRADE_AMOUNT * 10**STEP_SIZE / new_buy_price) / float(10**STEP_SIZE), 8),
                        price=f"{new_buy_price:.2f}"
                    )
                    open_orders.append(new_buy_order)
                    print(f"New buy order placed at {new_buy_price} after sell filled at {status['price']}")
                open_orders.remove(order)  # Remove filled order from tracking
    
        # Save open orders after checking status
        save_open_orders()
    except Exception as e:
        print(f"Error checking order status: {e}")

# WebSocket to track live price
def on_message(ws, message):
    global REFRESH_COUNTER
    json_message = json.loads(message)
    
    # Check order status every 10 price updates
    if int(json_message['k']['x']) == True:
        REFRESH_COUNTER += 1
        current_price = float(json_message['k']['c'])  # 'c' is the close price of the candlestick
        print(f"Current price: {current_price}")
    
        # Update all trading values
        initialize_all(current_price=current_price)
        
        check_order_status()
        
        if REFRESH_COUNTER % 1440 == 0:
            cancel_all_open_orders()
            refresh_grid_orders()

def on_error(ws, error):
    print(f"WebSocket Error: {error}")
    reconnect(ws)

def on_close(ws):
    print("WebSocket connection closed")
    reconnect(ws)

def on_open(ws):
    print("WebSocket connection opened")
    
def reconnect(ws):
    time.sleep(5)  # Wait for 5 seconds before attempting to reconnect
    print("Reconnecting...")
    ws.run_forever()

# Start WebSocket connection to listen to live BTCUSDT price data
ws = websocket.WebSocketApp(SOCKET, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)

# Initialize before starting WebSocket connection
initialize_all()

# Load existing open orders if any
load_open_orders()

# Run WebSocket
ws.run_forever()

if __name__ == "__main__":
    pass
