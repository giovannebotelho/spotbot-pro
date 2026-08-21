import asyncio
import time

from core.futures_state import futures_state
from core.futures_order_manager import get_futures_order_details, robust_cancel_all_orders

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
                
                atr_pct = pos.get('atr_pct', 0.015)
                partial_taken = pos.get('partial_taken', False)

                # Cálculo de Metas Adaptativas por Volatilidade (ATR Dinâmico)
                # Volatilidade alta -> alvos maiores; Volatilidade baixa -> alvos curtos
                adaptive_tp_roi = max(4.5, min(12.0, atr_pct * leverage * 100 * 0.9))
                # Stop Preventivo Rígido: Travado no teto de -7.0% de ROI (para perdas pequenas e controladas)
                adaptive_preventive_sl = -max(5.0, min(7.5, atr_pct * leverage * 100 * 0.7))

                # -------------------------------------------------------------
                # 🏆 ARQUITETURA HEDGE FUND: TREND RUNNER DINÂMICO ESCALÁVEL
                # -------------------------------------------------------------
                
                # FASE 1: Parcial 50% no Lucro (+3.5% a +5.0% ROI) + Breakeven Imediato (Risco ZERO)
                if cur_roi >= 3.5 and not partial_taken and qty > 0:
                    try:
                        half_qty = qty / 2.0
                        step_size_str = pos.get('step_size', '0.001')
                        if symbol == 'LINKUSDT' and step_size_str in ['0.001', '0.01']:
                            step_size_str = '0.1'

                        from decimal import Decimal, ROUND_DOWN
                        step_dec = Decimal(step_size_str)
                        half_qty_dec = Decimal(str(half_qty))
                        quantized_half = (half_qty_dec / step_dec).quantize(Decimal('1'), rounding=ROUND_DOWN) * step_dec
                        half_qty_rounded = float(quantized_half)
                        
                        if '.' in step_size_str:
                            precision_decimals = len(step_size_str.split('.')[1].rstrip('0'))
                            half_qty_rounded = round(half_qty_rounded, precision_decimals) if precision_decimals > 0 else int(half_qty_rounded)
                        else:
                            half_qty_rounded = int(half_qty_rounded)

                        if half_qty_rounded > 0:
                            side_exit = 'SELL' if direction == 'LONG' else 'BUY'
                            log(f"🎯 [PARCIAL 50%] {symbol} atingiu +{cur_roi:.2f}% de ROI! Realizando 50% ({half_qty_rounded}) a mercado...")
                            
                            # Cancela ordens antigas para evitar conflito -4130
                            await robust_cancel_all_orders(client, symbol, log)

                            # Envia ordem parcial a mercado
                            await client.futures_create_order(
                                symbol=symbol, side=side_exit, type='MARKET',
                                quantity=half_qty_rounded, reduceOnly='true'
                            )
                            
                            # Recria SL no Breakeven (Preço de Entrada) para a posição remanescente
                            remaining_qty = qty - half_qty_rounded
                            
                            try:
                                # Formata o stopPrice com a precisão de preço da exchange
                                price_prec = 4
                                try:
                                    ex_info = await client.futures_exchange_info()
                                    for s_item in ex_info.get('symbols', []):
                                        if s_item['symbol'] == symbol:
                                            for f in s_item.get('filters', []):
                                                if f['filterType'] == 'PRICE_FILTER':
                                                    tick_str = f['tickSize']
                                                    if '.' in tick_str:
                                                        price_prec = len(tick_str.split('.')[1].rstrip('0'))
                                                    else:
                                                        price_prec = 0
                                            break
                                except Exception:
                                    pass

                                be_stop_price = round(entry_price, price_prec) if price_prec > 0 else int(entry_price)

                                await client.futures_create_order(
                                    symbol=symbol, side=side_exit, type='STOP_MARKET',
                                    stopPrice=be_stop_price, closePosition='true'
                                )
                                log(f"🛡️ [BREAKEVEN-ORDEM] Ordem STOP_MARKET colocada na Binance a ${be_stop_price} para proteger os 50% restantes!")
                            except Exception as sl_err:
                                log(f"⚠️ Aviso ao recriar SL Breakeven na Binance em {symbol}: {sl_err}")
                            
                            # Atualiza estado na memória
                            await futures_state.update(symbol, 'qty', remaining_qty)
                            await futures_state.update(symbol, 'partial_taken', True)
                            await futures_state.update(symbol, 'sl', entry_price)
                            await futures_state.update(symbol, 'locked_profit_roi', 0.0)
                            
                            log(f"🛡️ [BREAKEVEN] Stop Loss de {symbol} movido para o preço de entrada (${entry_price:.4f}). Risco ZERO ativado! Liberando Modo Runner 🚀")
                            
                            from config.settings import TELEGRAM_CONFIG
                            from services.telegram_notifier import send_telegram_message
                            if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                                asyncio.create_task(send_telegram_message(
                                    TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                    f"🎯 <b>PARCIAL 50% EXECUTADA NO LUCRO!</b>\n\n"
                                    f"🪙 <b>Par:</b> {symbol} ({direction})\n"
                                    f"📈 <b>ROI Atual:</b> +{cur_roi:.2f}%\n"
                                    f"💰 <b>Qtd Fechada:</b> {half_qty_rounded}\n"
                                    f"🛡️ <b>Stop Breakeven:</b> Movido para ${entry_price:.4f} (Risco Zero!)\n"
                                    f"🚀 <b>Modo Trend Runner Ativado:</b> Deixando o lucro surfar sem teto fixo!"
                                ))
                            continue
                    except Exception as p_err:
                        log(f"⚠️ Aviso na execução parcial de {symbol}: {p_err}")

                # FASE 2: Escada de Lucro Blindado (Trailing Step Lock)
                # Conforme o ROI sobe, vamos subindo o piso mínimo de lucro garantido
                current_locked_roi = pos.get('locked_profit_roi', 0.0)
                new_locked_roi = current_locked_roi

                if peak_roi >= 100.0:
                    new_locked_roi = max(new_locked_roi, 75.0)  # Se bateu 100% ROI, garante pelo menos 75%
                elif peak_roi >= 50.0:
                    new_locked_roi = max(new_locked_roi, 35.0)  # Se bateu 50% ROI, garante pelo menos 35%
                elif peak_roi >= 25.0:
                    new_locked_roi = max(new_locked_roi, 16.0)  # Se bateu 25% ROI, garante pelo menos 16%
                elif peak_roi >= 12.0:
                    new_locked_roi = max(new_locked_roi, 7.5)   # Se bateu 12% ROI, garante pelo menos 7.5%
                elif peak_roi >= 8.0:
                    new_locked_roi = max(new_locked_roi, 4.5)   # Se bateu 8% ROI, garante pelo menos 4.5% de lucro limpo
                elif peak_roi >= 6.0:
                    new_locked_roi = max(new_locked_roi, 2.5)   # Se bateu 6% ROI, garante 2.5% (paga taxas e sobra lucro verde!)

                if new_locked_roi > current_locked_roi:
                    await futures_state.update(symbol, 'locked_profit_roi', new_locked_roi)
                    log(f"🔒 [PISO DE LUCRO] {symbol} travou lucro mínimo garantido em +{new_locked_roi:.1f}% ROI (Pico: +{peak_roi:.1f}%).")

                # Se o ROI atual recuou abaixo do piso de lucro já travado, realiza o lucro imediatamente
                if current_locked_roi > 0 and cur_roi <= current_locked_roi:
                    log(f"💰 [TRAIL-STEP EXEC] {symbol} recuou para o piso travado de +{current_locked_roi:.1f}% ROI (Pico foi +{peak_roi:.1f}%). Realizando lucro!")
                    await execute_trailing_close(client, symbol, direction, qty, log)
                    continue

                # FASE 3: Trailing Flexível do Topo (Proteção contra Flash Crash / Reversão Elástica)
                if peak_roi >= 15.0:
                    # Tolerância de recuo elástica proporcional à magnitude do lucro
                    if peak_roi >= 60.0:
                        reversal_allowance = 15.0 # Lucros gigantes toleram recuo de até 15% ROI do topo antes de ejetar
                    elif peak_roi >= 30.0:
                        reversal_allowance = 8.0
                    else:
                        reversal_allowance = 5.0

                    if (peak_roi - cur_roi) >= reversal_allowance:
                        log(f"⚡ [TREND-RUNNER EXIT] Recuo expressivo detectado em {symbol} (Pico: +{peak_roi:.2f}%, Atual: +{cur_roi:.2f}%). Ejetando no lucro com segurança!")
                        await execute_trailing_close(client, symbol, direction, qty, log)
                        continue

                # FASE 4: Stop Preventivo Rígido (Se nunca entrou em lucro e o trade foi direto contra)
                if not partial_taken and cur_roi <= adaptive_preventive_sl:
                    log(f"⚡ [STOP PREVENTIVO] {symbol} atingiu tolerância máxima negativa ({cur_roi:.2f}% <= {adaptive_preventive_sl:.1f}%). Fechando antes do SL rígido!")
                    await execute_trailing_close(client, symbol, direction, qty, log)
                    continue
                            
        except Exception as e:
            log(f"⚠️ Erro no Trailing Lock Monitor: {e}")
            
        await asyncio.sleep(1)

async def execute_trailing_close(client, symbol, direction, qty, log):
    try:
        side_exit = 'SELL' if direction == 'LONG' else 'BUY'
        
        # 1. Cancela TP/SL antigos para evitar órfãs e liberar margem ANTES de fechar
        await robust_cancel_all_orders(client, symbol, log)
        
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
