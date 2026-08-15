import asyncio
from binance import AsyncClient
import pandas as pd

def calculate_ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

async def main():
    client = await AsyncClient.create()
    klines = await client.futures_klines(symbol='BTCUSDT', interval='1h', limit=50)
    
    df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df['close'] = df['close'].astype(float)
    
    df['ema20'] = calculate_ema(df['close'], 20)
    
    for index, row in df.tail(10).iterrows():
        print(f"{row['time']} | Close: {row['close']} | EMA20: {row['ema20']}")
        
    await client.close_connection()

asyncio.run(main())
