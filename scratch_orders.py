import asyncio
from binance import AsyncClient
from config.settings import API_KEYS

async def main():
    client = await AsyncClient.create(API_KEYS['mainnet']['key'], API_KEYS['mainnet']['secret'])
    
    open_orders = await client.futures_get_open_orders(symbol='BTCUSDT')
    print(f"Ordens Abertas (BTCUSDT): {len(open_orders)}")
    for o in open_orders:
        print(f"ID: {o['orderId']} | Type: {o['type']} | Status: {o['status']} | ClientId: {o['clientOrderId']}")
        
    await client.close_connection()

asyncio.run(main())
