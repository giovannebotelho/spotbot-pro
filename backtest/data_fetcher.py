import asyncio
import pandas as pd
from binance import AsyncClient
import sqlite3
import os

DB_PATH = 'backtest/backtest_data.db'

async def fetch_and_save_klines(symbol='BTCUSDT', interval='15m', days_back=365):
    """
    Baixa o histórico de klines da Binance e salva no banco de dados SQLite local.
    """
    client = await AsyncClient.create()
    
    print(f"Baixando histórico de {days_back} dias para {symbol} ({interval})...")
    
    import datetime
    start_time = int((datetime.datetime.now() - datetime.timedelta(days=days_back)).timestamp() * 1000)
    
    klines = await client.futures_historical_klines(
        symbol=symbol,
        interval=interval,
        start_str=start_time
    )
    
    print(f"Baixadas {len(klines)} velas. Processando para o banco de dados...")
    
    columns = [
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ]
    df = pd.DataFrame(klines, columns=columns)
    
    for col in columns:
        if col not in ['open_time', 'close_time', 'ignore']:
            df[col] = df[col].astype(float)
            
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
    
    df.drop('ignore', axis=1, inplace=True)
    
    conn = sqlite3.connect(DB_PATH)
    table_name = f"{symbol}_{interval}"
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    
    print(f"[OK] Dados salvos com sucesso na tabela {table_name} do banco {DB_PATH}!")
    await client.close_connection()

if __name__ == '__main__':
    os.makedirs('backtest', exist_ok=True)
    
    async def run():
        await fetch_and_save_klines('BTCUSDT', '15m', 180) # 6 meses para teste inicial
        await fetch_and_save_klines('BTCUSDT', '1h', 180)
        
    asyncio.run(run())
