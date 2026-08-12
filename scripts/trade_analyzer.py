import os
import sys
import asyncio
import pandas as pd
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

# Adiciona o diretório raiz ao sys.path para importar configurações
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import API_KEYS, TOP_40_SYMBOLS
from binance.async_client import AsyncClient

BINANCE_API_KEY = API_KEYS['mainnet']['key']
BINANCE_API_SECRET = API_KEYS['mainnet']['secret']

async def fetch_binance_trades_and_context(days_back=30):
    print(f"🔄 Iniciando extração de dados diretamente da Binance ({days_back} dias)...")
    
    client = await AsyncClient.create(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    start_time_ms = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    end_time_ms = int(datetime.now().timestamp() * 1000)
    
    all_trades_data = []
    
    for symbol in TOP_40_SYMBOLS:
        print(f"🔍 Buscando histórico para {symbol}...")
        
        # 1. Busca Trades de Futuros (Em blocos de 7 dias)
        try:
            processed_timestamps = set()
            chunk_start = start_time_ms
            while chunk_start < end_time_ms:
                chunk_end = min(chunk_start + (7 * 24 * 60 * 60 * 1000), end_time_ms)
                futures_trades = await client.futures_account_trades(symbol=symbol, startTime=chunk_start, endTime=chunk_end, limit=1000)
                
                for t in futures_trades:
                    ts = int(t['time'])
                    ts_hour = ts // (1000 * 60 * 60)
                    if ts_hour in processed_timestamps: continue
                    processed_timestamps.add(ts_hour)
                    
                    side = 'BUY' if t['buyer'] else 'SELL'
                    direction = 'LONG' if side == 'BUY' else 'SHORT'
                    
                    all_trades_data.append({
                        'market': 'FUTURES', 'symbol': symbol, 'timestamp': ts,
                        'date': datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M:%S'),
                        'direction': direction, 'price': float(t['price']), 'qty': float(t['qty'])
                    })
                chunk_start = chunk_end
                await asyncio.sleep(0.1)
                
        except Exception as e:
            print(f"⚠️ Erro ao buscar Futuros para {symbol}: {e}")
            
        # 2. Busca Trades Spot (Em blocos de 24 horas)
        try:
            processed_timestamps_spot = set()
            chunk_start = start_time_ms
            while chunk_start < end_time_ms:
                chunk_end = min(chunk_start + (24 * 60 * 60 * 1000), end_time_ms)
                spot_trades = await client.get_my_trades(symbol=symbol, startTime=chunk_start, endTime=chunk_end, limit=1000)
                
                for t in spot_trades:
                    ts = int(t['time'])
                    ts_hour = ts // (1000 * 60 * 60)
                    if ts_hour in processed_timestamps_spot: continue
                    processed_timestamps_spot.add(ts_hour)
                    
                    side = 'BUY' if t['isBuyer'] else 'SELL'
                    if side == 'BUY':
                        all_trades_data.append({
                            'market': 'SPOT', 'symbol': symbol, 'timestamp': ts,
                            'date': datetime.fromtimestamp(ts/1000).strftime('%Y-%m-%d %H:%M:%S'),
                            'direction': 'LONG', 'price': float(t['price']), 'qty': float(t['qty'])
                        })
                chunk_start = chunk_end
                await asyncio.sleep(0.1)
                    
        except Exception as e:
            print(f"⚠️ Erro ao buscar Spot para {symbol}: {e}")
            
        await asyncio.sleep(1) # Respeitar rate limits
        
    print(f"✅ Foram encontrados {len(all_trades_data)} blocos de trades nos últimos {days_back} dias.")
    
    print("📈 Baixando contexto de mercado (Klines de 1h) para cada trade...")
    
    dataset = []
    
    for i, t in enumerate(all_trades_data):
        sym = t['symbol']
        ts = t['timestamp']
        
        # Queremos 30 horas antes e 30 horas depois
        # 1 hora = 60 * 60 * 1000 ms = 3600000 ms
        kline_start = ts - (30 * 3600000)
        kline_end = ts + (30 * 3600000)
        
        print(f"[{i+1}/{len(all_trades_data)}] Baixando Klines para {sym} em {t['date']}...")
        
        try:
            if t['market'] == 'FUTURES':
                klines = await client.futures_klines(symbol=sym, interval='1h', startTime=kline_start, endTime=kline_end, limit=100)
            else:
                klines = await client.get_klines(symbol=sym, interval='1h', startTime=kline_start, endTime=kline_end, limit=100)
                
            if len(klines) >= 30:
                # Separar Klines Antes e Depois
                closes = [float(k[4]) for k in klines]
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                
                # Achar o index da vela da hora da compra
                target_idx = -1
                for j, k in enumerate(klines):
                    k_time = int(k[0])
                    if ts >= k_time and ts < k_time + 3600000:
                        target_idx = j
                        break
                        
                if target_idx != -1 and target_idx >= 5:
                    closes_before = closes[:target_idx+1]
                    closes_after = closes[target_idx+1:]
                    
                    # Calcular indicadores básicos no momento do trade
                    df_before = pd.Series(closes_before)
                    ema20 = df_before.ewm(span=20, adjust=False).mean().iloc[-1]
                    
                    # Performance após a compra (Máxima Excursão e Mínima Excursão em 30h)
                    max_price_after = max(highs[target_idx+1:]) if len(highs) > target_idx+1 else t['price']
                    min_price_after = min(lows[target_idx+1:]) if len(lows) > target_idx+1 else t['price']
                    
                    max_gain_pct = ((max_price_after - t['price']) / t['price']) * 100
                    max_drawdown_pct = ((min_price_after - t['price']) / t['price']) * 100
                    
                    row = {
                        'Trade_Date': t['date'],
                        'Market': t['market'],
                        'Symbol': t['symbol'],
                        'Direction': t['direction'],
                        'Entry_Price': t['price'],
                        'EMA20_1h': ema20,
                        'Price_to_EMA20_Pct': ((t['price'] - ema20) / ema20) * 100,
                        'Max_Gain_30h_Pct': max_gain_pct,
                        'Max_Drawdown_30h_Pct': max_drawdown_pct,
                    }
                    
                    # Adicionar os fechamentos 5 horas antes como features (volatilidade)
                    for h in range(1, 6):
                        idx = target_idx - h
                        if idx >= 0:
                            row[f'Close_minus_{h}h_Pct'] = ((closes[idx] - closes[idx-1]) / closes[idx-1] * 100) if idx > 0 else 0
                        else:
                            row[f'Close_minus_{h}h_Pct'] = 0
                            
                    dataset.append(row)
                    
        except Exception as e:
            print(f"⚠️ Erro nas Klines de {sym}: {e}")
            
        await asyncio.sleep(0.5) # Rate limit Binance API
        
    await client.close_connection()
    
    if dataset:
        df = pd.DataFrame(dataset)
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_dataset.csv')
        df.to_csv(out_path, index=False)
        print(f"\n✅ Concluído! {len(dataset)} amostras ricas extraídas com sucesso.")
        print(f"📁 Arquivo salvo em: {out_path}")
    else:
        print("\n❌ Nenhum dado válido pôde ser extraído.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(fetch_binance_trades_and_context(days_back=3))
