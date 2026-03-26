Grid bot for spot trading on binance. It trades the highest fluctuating coin or can stay on one single chosen coin. The idea is to update the parameters dynamically and automatically trail the price.
The bot will change coins over time (at UTC 0) whenever all the following conditions meet:
1. so far the bot has made a profit on the current coin
2. another coin has a higher "volatility x liquidity" rating than the current coin
3. a new daily candle has opened on binance

Usage:
Enter you binacne api key and api secret in line 14 and 15 respectively (has to be created on binance beforehand)
If you wish to stay on one coin (e.g. only BTC) change line 63 to "multi_coin = False" and change line 22 to "PAIR = "BTC_OR_WHATEVER_IN_CAPITAL_LETTERS".
otherwise only api key and api secret suffice to start trading.

All other parameters are not to be changed - except if you really want to experiment with the bot. 



Disclaimer:
This bot is a proof of concept and does not guarantee you to make any money!
Be aware, you will be trading with real assets and will be exposed to real risk of losing money!
BE AWARE, YOU WILL BE TRADING WITH REAL ASSETS AND WILL BE EXPOSED TO REAL RISK OF LOSING MONEY!
Always trade with money you can afford to lose! You have been warned.
