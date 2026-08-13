import asyncio
import time

from core.futures_state import futures_state

async def run_trailing_lock_monitor(client, log=print):
    """
    Monitora posições ativas de futuros a cada 1 segundo.
    Se o preço atingir 80% da meta (TP), engatilha a Trava Trailing.
    Se, após engatilhar, o preço recuar 0.50% a partir do pico, fecha a mercado.
    """
    log("🛡️ Trailing Lock Monitor iniciado...")
    while True:
        try:
            active_futures_positions = await futures_state.get_all()
            symbols_to_check = list(active_futures_positions.keys())
            if not symbols_to_check:
                await asyncio.sleep(2)
                continue
                
            # Busca o preço atual apenas dos tickers ativos (Otimização de Rate Limit)
            tasks = [client.futures_symbol_ticker(symbol=s) for s in symbols_to_check]
            tickers = await asyncio.gather(*tasks, return_exceptions=True)
            
            price_map = {}
            for t in tickers:
                if isinstance(t, dict) and 'symbol' in t and 'price' in t:
                    price_map[t['symbol']] = float(t['price'])
            
            for symbol in symbols_to_check:
                if symbol not in active_futures_positions:
                    continue
                    
                pos = active_futures_positions[symbol]
                direction = pos['direction']
                entry_price = pos['entry']
                tp_price = pos['tp']
                qty = pos.get('qty', 0)
                
                cur_price = price_map.get(symbol)
                if not cur_price:
                    continue
                
                leverage = pos.get('leverage', 15)
                
                # Inicializa trackers de trailing se não existirem
                if 'peak_price' not in pos:
                    pos['peak_price'] = cur_price
                    
                # Atualiza o pico
                if direction == 'LONG':
                    if cur_price > pos['peak_price']:
                        pos['peak_price'] = cur_price
                else: # SHORT
                    if cur_price < pos['peak_price'] or pos['peak_price'] == 0:
                        pos['peak_price'] = cur_price
                        
                # Calcula ROIs (em porcentagem)
                if direction == 'LONG':
                    cur_roi = ((cur_price - entry_price) / entry_price) * leverage * 100
                    peak_roi = ((pos['peak_price'] - entry_price) / entry_price) * leverage * 100
                else:
                    cur_roi = ((entry_price - cur_price) / entry_price) * leverage * 100
                    peak_roi = ((entry_price - pos['peak_price']) / entry_price) * leverage * 100
                
                # Regra 1: Take Profit Absoluto (8% ROI)
                if cur_roi >= 8.0:
                    log(f"🎯 [TAKE-PROFIT MAX] {symbol} atingiu o alvo de 8% de ROI! Fechando a mercado.")
                    await execute_trailing_close(client, symbol, direction, qty, log)
                    await futures_state.remove(symbol)
                    continue
                    
                # Regra 2: Trailing Lock Dinâmico (Distância de 3% do Pico de ROI)
                # Só ativamos o lock contínuo se o pico foi positivo (operação chegou a lucrar algo)
                # ou se você quiser que ele atue desde o começo como um SL apertado. Vamos assumir que sempre acompanha o pico.
                if (peak_roi - cur_roi) >= 3.0:
                    log(f"⚡ [TRAILING-LOCK] Recuo detectado em {symbol}! (Pico ROI: {peak_roi:.2f}%, Atual ROI: {cur_roi:.2f}%). Fechando a mercado!")
                    await execute_trailing_close(client, symbol, direction, qty, log)
                    await futures_state.remove(symbol)
                    continue
                            
        except Exception as e:
            log(f"⚠️ Erro no Trailing Lock Monitor: {e}")
            
        await asyncio.sleep(1)

async def execute_trailing_close(client, symbol, direction, qty, log):
    try:
        side_exit = 'SELL' if direction == 'LONG' else 'BUY'
        
        # 1. Cancela TP/SL antigos para evitar órfãs e liberar margem ANTES de fechar
        try:
            await client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception as e:
            log(f"⚠️ Aviso ao cancelar ordens pendentes de {symbol}: {e}")
        
        # 2. Envia a mercado
        try:
            await client.futures_create_order(symbol=symbol, side=side_exit, type='MARKET', quantity=qty, reduceOnly='true')
            log(f"✅ [TRAILING-LOCK] Posição de {symbol} fechada com sucesso em segurança.")
        except Exception as e:
            if "ReduceOnly" not in str(e) and "-2022" not in str(e):
                log(f"⚠️ Erro ao enviar ordem a mercado no Trailing Lock: {e}")
            else:
                log(f"✅ [TRAILING-LOCK] Posição já parece estar fechada.")
                
    except Exception as e:
        log(f"❌ Erro crítico no Trailing Lock de {symbol}: {e}")
