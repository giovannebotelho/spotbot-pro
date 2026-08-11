import asyncio
import os
import time
import math
import pandas as pd
import datetime as dt_module
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from binance import BinanceSocketManager
from binance import AsyncClient as BinanceAsyncClient
from binance.exceptions import BinanceAPIException

from config.settings import API_KEYS, TELEGRAM_CONFIG, TRADING_CONFIG, RSI_CONFIG, TRAILING_STOP_CONFIG, SCANNER_CONFIG, TOP_40_SYMBOLS, MAX_CONCURRENT_POSITIONS, RESERVE_FRACTION_FOR_DCA, TIMEZONE
from services.binance_client import extract_closes, extract_volumes, get_usdt_balance, get_order_details, get_klines, get_bnb_price, get_multi_klines
from core.indicators import (
    calculate_rsi, calculate_macd, calculate_bollinger_bands, check_trend, check_candle_patterns,
    calculate_vwap, get_candle_details, calculate_ema, is_market_downward, calculate_relative_strength_rank,
    calculate_fibonacci_supports
)
from core.decision import (
    should_place_order, should_buy, should_sell, adjust_and_place_oco_order, get_min_notional,
    adjust_price_to_tick_size, get_precision, calculate_dynamic_position_slots, place_safe_oco_sell_order,
    calculate_kelly_position_size
)
from core.post_trade import process_order_details, log_and_notify_results, create_data_row, save_to_csv
from services.telegram_notifier import send_telegram_message, send_telegram_document, TelegramBot
from services.news_scanner import fetch_crypto_news
from services.gemini_ai import analyze_news_sentiment_with_gemini, generate_post_trade_synthesis, auto_tune_risk_profile
from services.database import DatabaseManager
from services.pdf_generator import generate_weekly_telemetry_pdf
from utils.formatting import remove_ansi_codes, format_price
from core.futures_state import futures_state
from core.futures_engine import (
    run_futures_bot, bot_futures_status_data, panic_sell_futures_position
)

client = None

environment = os.getenv("BOT_ENVIRONMENT", "mainnet")
if environment == "mainnet":
    api_key = API_KEYS.get('mainnet', {}).get('key', '')
    api_secret = API_KEYS.get('mainnet', {}).get('secret', '')
elif environment == "testnet":
    api_key = API_KEYS.get('testnet_spot', {}).get('key', '')
    api_secret = API_KEYS.get('testnet_spot', {}).get('secret', '')
else:
    api_key = API_KEYS.get('mainnet', {}).get('key', '')
    api_secret = API_KEYS.get('mainnet', {}).get('secret', '')

bot_running = False
active_positions = {}
active_monitoring_tasks = []
_last_autotune_time = 0

bot_status_data = {
    "rsi": 0, "price": 0, "symbol": "", "action": "Iniciando...", "trend": "N/A", "target_asset": "BTCUSDT",
    "active_symbols": [], "active_positions": active_positions
}
shared_market_data = {
    "klines": [], "dates": [], "bb_upper": [], "bb_lower": [], "bb_middle": [], "ema200": [], "volumes": [], "scanner_results": []
}

SHORT_PAUSE = 600
LONG_PAUSE = 3600
stop_loss_count = 0
last_stop_loss_time = None
block_active = False
pause_end_time = None
MAX_RESTARTS = 3
restart_attempts = 0
last_operation_time = None

async def sync_binance_time(client, log=print):
    try:
        res = await client.get_server_time()
        server_time = res['serverTime']
        local_time = int(time.time() * 1000)
        time_offset = server_time - local_time
        client.TIME_OFFSET = time_offset
        log(f"⏱️ Relógio sincronizado com a Binance! (Offset: {time_offset}ms)")
    except Exception as e:
        log(f"⚠️ Aviso ao sincronizar relógio com a Binance: {e}")

async def cancel_all_oco_orders(client, symbol):
    try:
        open_oco_orders = await client.get_open_oco_orders()
        for order_list in open_oco_orders:
            if order_list['symbol'] == symbol:
                order_list_id = order_list['orderListId']
                await client._delete('orderList', signed=True, data={'symbol': symbol, 'orderListId': order_list_id})
                print(f"Ordem OCO ID {order_list_id} cancelada para {symbol}.")
    except Exception as e:
        print(f"Aviso ao cancelar ordens OCO: {e}")

async def check_stop_losses(current_time, log=print):
    global stop_loss_count, last_stop_loss_time, block_active, pause_end_time
    if block_active and current_time > pause_end_time:
        block_active = False
        pause_end_time = None

    if last_stop_loss_time and (current_time - last_stop_loss_time) < timedelta(seconds=900):
        if stop_loss_count > 1:
            log("🚨 Mais de 1 stop loss detectado em 15min. Pausando robô por 1 hora.\n")
            message = "🚨 Mais de 1 stop loss detectado em 15min. Pausando robô por 1 hora."
            asyncio.create_task(send_telegram_message(TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'], message))

            pause_end_time = current_time + timedelta(seconds=LONG_PAUSE)
            block_active = True
            stop_loss_count = 0
            last_stop_loss_time = current_time
            await asyncio.sleep(LONG_PAUSE)
            log("\n ✅ Voltando a operar após pausa de 1 hora.")
            return
    else:
        stop_loss_count = 0

    if stop_loss_count == 1:
        stop_loss_count += 1
        log("🚨 Stop loss detectado. Pausando por 10 minutos.")
        message = "🚨 Stop loss detectado. Pausando por 10 minutos."
        asyncio.create_task(send_telegram_message(TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'], message))
        pause_end_time = current_time + timedelta(seconds=SHORT_PAUSE)
        last_stop_loss_time = current_time
        await asyncio.sleep(SHORT_PAUSE)

async def check_rsi_reset(symbol, log=print):
    global last_operation_time
    if last_operation_time and (dt_module.datetime.now(TIMEZONE) - last_operation_time) > timedelta(seconds=6*60*60):
        current_levels = [RSI_CONFIG['dynamic_low'][i] for i in range(6)]
        default_levels = [RSI_CONFIG['levels'][i] for i in range(6)]
        
        if current_levels != default_levels:
            for i in range(6):
                RSI_CONFIG['dynamic_low'][i] = RSI_CONFIG['levels'][i]
            log(f"\n⏳ Níveis de RSI resetados para {symbol} por inatividade.")
        last_operation_time = dt_module.datetime.now(TIMEZONE)

_cached_balances = {'bnb': 0.0, 'bnb_usdt': 0.0, 'usdt': 0.0}
_last_balance_time = 0
_balance_client = None

async def get_account_balances():
    global _cached_balances, _last_balance_time, _balance_client
    if not api_key or not api_secret:
        return {'bnb': 0.0, 'bnb_usdt': 0.0, 'usdt': 0.0}
    
    now = time.time()
    if now - _last_balance_time < 10.0 and _cached_balances['usdt'] > 0:
        return _cached_balances

    try:
        if _balance_client is None:
            _balance_client = await BinanceAsyncClient.create(api_key, api_secret)
            await sync_binance_time(_balance_client, log=lambda m: None)

        bnb_balance = await _balance_client.get_asset_balance(asset='BNB')
        bnb_balance_free = float(bnb_balance['free'])
        bnb_price_usdt = await get_bnb_price(_balance_client)
        bnb_balance_usdt = bnb_balance_free * bnb_price_usdt
        usdt_balance = await get_usdt_balance(_balance_client)

        _cached_balances = {
            'bnb': bnb_balance_free, 'bnb_usdt': bnb_balance_usdt, 'usdt': usdt_balance
        }
        _last_balance_time = now
        return _cached_balances
    except Exception as e:
        err_msg = str(e) if str(e).strip() else repr(e)
        print(f"⚠️ Aviso ao buscar saldos ({type(e).__name__}): {err_msg}")
        if _balance_client:
            try:
                await _balance_client.close_connection()
            except Exception:
                pass
            _balance_client = None
        return _cached_balances

from core.order_manager import monitor_oco_lifecycle

async def panic_sell_position(symbol, client_instance=None):
    """
    FASE 1 (v6.0): Panic Sell / Encerramento a Mercado de Posição Ativa.
    Cancela a ordem OCO na Binance, executa venda a mercado imediatamente,
    calcula o PnL final e grava o trade no PostgreSQL.
    Funciona inclusive para posições recuperadas via State Recovery!
    """
    global active_positions, bot_status_data
    symbol = symbol.strip().upper()
    log_msg = f"🚨 \033[1;31mPANIC SELL\033[0m: Iniciando encerramento de emergência para {symbol}..."
    print(log_msg)
    
    cli = client_instance or globals().get('client')
    if not cli:
        try:
            cli = await BinanceAsyncClient.create(api_key, api_secret)
            await sync_binance_time(cli, log=lambda m: None)
        except Exception as e:
            return False, f"Erro ao conectar com a Binance: {e}"

    try:
        # 1. Cancela ordens OCO abertas para o simbolo
        open_ocos = await cli.get_open_oco_orders()
        for oco in open_ocos:
            if oco['symbol'] == symbol:
                try:
                    await cli._delete('orderList', signed=True, data={'symbol': symbol, 'orderListId': oco['orderListId']})
                except Exception as c_err:
                    print(f"⚠️ Aviso ao cancelar OCO de {symbol}: {c_err}")

        # 2. Obtem quantidade livre e vende a mercado
        asset_name = symbol.replace("USDT", "")
        bal = await cli.get_asset_balance(asset=asset_name)
        free_qty = float(bal['free']) if bal else 0.0

        info = await cli.get_symbol_info(symbol)
        step_size = float(next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')['stepSize'])
        precision_qty = get_precision(step_size)
        sell_qty = round(math.floor(free_qty / step_size) * step_size, precision_qty)

        if sell_qty <= 0:
            active_positions.pop(symbol, None)
            bot_status_data['active_symbols'] = list(active_positions.keys())
            return False, f"Saldo insuficiente de {asset_name} para efetuar venda a mercado."

        sell_order = await cli.order_market_sell(symbol=symbol, quantity=sell_qty)
        executed_qty = float(sell_order.get('executedQty', sell_qty))
        sell_price = float(sell_order['fills'][0]['price']) if sell_order.get('fills') else float((await cli.get_symbol_ticker(symbol=symbol))['price'])

        pos_info = active_positions.get(symbol, {})
        entry_price = pos_info.get('entry', sell_price)
        trade_result = (sell_price - entry_price) * executed_qty
        trade_result_liquid = trade_result * 0.999 # Desconto de taxa estimado

        timestamp = dt_module.datetime.now(TIMEZONE).strftime("%d/%m/%Y at %H:%M:%S")

        # 3. Salva no banco de dados PostgreSQL/SQLite
        db_mgr = DatabaseManager()
        usdt_bal = await get_usdt_balance(cli)
        data_row = create_data_row(
            1, usdt_bal, usdt_bal, symbol, executed_qty, entry_price, timestamp,
            pos_info.get('tp', 0.0), pos_info.get('sl', 0.0), pos_info.get('sl', 0.0),
            "PANIC SELL (Encerramento Manual)", timestamp, trade_result, trade_result, usdt_bal,
            0, "Panic Sell executado via Dashboard", 0, sell_price, sell_price, sell_price, sell_price, 0,
            0, 0, 0, 0, 0, 0, 0, 0, [], 0, 0, 0, 0, 0, 0, True, 0.001, trade_result_liquid,
            trade_result_liquid, "Encerramento manual a mercado efetuado pelo usuário", 0
        )
        save_to_csv(data_row)
        try:
            db_mgr.add_trade(data_row)
        except Exception as db_err:
            print(f"⚠️ Erro ao registrar Panic Sell no banco: {db_err}")

        # 4. Remove de active_positions
        active_positions.pop(symbol, None)
        bot_status_data['active_symbols'] = list(active_positions.keys())
        if bot_status_data.get('target_asset') == symbol:
            bot_status_data['tp_price'] = 0.0
            bot_status_data['sl_price'] = 0.0
            bot_status_data['entry_price'] = 0.0

        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
            asyncio.create_task(send_telegram_message(
                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                f"🚨 <b>PANIC SELL EXECUTADO!</b>\n\n"
                f"🪙 Par: <b>{symbol}</b>\n"
                f"💵 Preço de Venda: <b>${sell_price:.4f}</b>\n"
                f"📊 PnL: <b>${trade_result_liquid:+.2f} USDT</b>"
            ))

        return True, f"Panic Sell de {symbol} executado a mercado com sucesso por ${sell_price:.4f}!"
    except Exception as err:
        return False, f"Falha no Panic Sell de {symbol}: {err}"

async def run_bot(log_callback=None, investment_amount=None, selected_symbol=None, status_callback=None):
    global restart_attempts, bot_running, last_operation_time, stop_loss_count, last_stop_loss_time
    bot_running = True

    def log(msg, end='\n', flush=False):
        print(msg, end=end, flush=flush)
        if log_callback: log_callback(msg)

    def status(msg):
        if status_callback: status_callback(msg)
        bot_status_data['action'] = remove_ansi_codes(msg)

    if not api_key or not api_secret:
        log("⚠️ ATENÇÃO: As chaves da API Binance não estão preenchidas no .env.")
        status("⚠️ Chaves API ausentes no .env")
        return

    db = DatabaseManager()

    async def handle_telegram_command(command):
        global bot_running
        cmd_parts = command.split()
        cmd = cmd_parts[0].lower()

        if cmd == '/start':
            return "🤖 <b>SpotBot Pro está ativo e monitorando o mercado!</b>"
        
        elif cmd == '/stop':
            bot_running = False
            return "🛑 <b>Comando recebido. Parando o bot com segurança...</b>"

        elif cmd in ['/cancel', '/abort', '/cancelar']:
            bot_running = False
            return "🚨 <b>INTERRUPÇÃO DE EMERGÊNCIA (CANCEL/CTRL+C)! Operações e conexões paralisadas imediatamente.</b>"
        
        elif cmd == '/status':
            target_asset = bot_status_data.get('target_asset', 'BTCUSDT')
            mtf_sc = bot_status_data.get('mtf_score', 80)
            cur_price_str = format_price(bot_status_data.get('price', 0.0))
            
            status_lines = [
                f"⚡ <b>STATUS DO SPOTBOT PRO v6.1 (QUANT)</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"🎯 <b>Modo</b>: {bot_status_data['symbol']}",
                f"⚡ <b>Estado</b>: <i>{bot_status_data['action']}</i>",
                f"",
                f"🔍 <b>Scanner Foco</b>: <b>{target_asset}</b> ({cur_price_str})",
                f"📊 <b>RSI</b>: <b>{bot_status_data['rsi']:.1f}</b> | MTF: <b>{mtf_sc}%</b> 🟢",
                f"📈 <b>Tendência 4h</b>: <b>{bot_status_data['trend']}</b>",
                f"━━━━━━━━━━━━━━━━━━━",
            ]
            
            if active_positions:
                status_lines.append(f"🎰 <b>VAGAS OCO ATIVAS ({len(active_positions)}/{MAX_CONCURRENT_POSITIONS})</b>:")
                c = None
                try:
                    c = await BinanceAsyncClient.create(api_key, api_secret)
                    for i, (sym, pos_info) in enumerate(active_positions.items(), 1):
                        entry = pos_info.get('entry', 0)
                        try:
                            ticker = await c.get_symbol_ticker(symbol=sym)
                            current_p = float(ticker['price'])
                            pnl_pct = ((current_p - entry) / entry) * 100 if entry > 0 else 0
                            emoji = "🟢" if pnl_pct >= 0 else "🔴"
                            status_lines.append(f"  • Slot {i}: <b>{sym}</b> {emoji} <b>{pnl_pct:+.2f}%</b> (Entrada: {format_price(entry)})")
                        except Exception:
                            status_lines.append(f"  • Slot {i}: <b>{sym}</b> (Entrada: {format_price(entry)})")
                except Exception:
                    pass
                finally:
                    if c: await c.close_connection()
            else:
                status_lines.append(f"🎰 <b>VAGAS OCO ATIVAS (0/{MAX_CONCURRENT_POSITIONS})</b>:")
                status_lines.append(f"  • Nenhuma posição aberta no momento.")
                
            return "\n".join(status_lines)
        
        elif cmd == '/saldo':
            c = None
            try:
                c = await BinanceAsyncClient.create(api_key, api_secret)
                await sync_binance_time(c, log=lambda m: None)
                usdt = await get_usdt_balance(c)
                bnb = await c.get_asset_balance(asset='BNB')
                bnb_free = float(bnb['free'])
                bnb_price = await get_bnb_price(c)
                
                db_stats = db.get_stats()
                acc_pnl = db_stats['total_net_profit']
                slots, val_slot = calculate_dynamic_position_slots(usdt, accumulated_net_profit=acc_pnl)
                kelly_val, kelly_pct, is_k_active = calculate_kelly_position_size(db, usdt)
                kelly_str = f"${kelly_val:.2f} USDT ({kelly_pct*100:.1f}% Half-Kelly)" if is_k_active else f"${val_slot:.2f} USDT (Padrão)"
                
                return (
                    f"💰 <b>SALDOS & POSIÇÕES DA CARTEIRA</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 <b>USDT Disponível</b>: <b>${usdt:.2f}</b>\n"
                    f"🪙 <b>Saldo BNB</b>: <b>{bnb_free:.4f} BNB</b> (~${bnb_free*bnb_price:.2f})\n"
                    f"📈 <b>Lucro Acumulado</b>: <b>${acc_pnl:.2f} USDT</b>\n"
                    f"🏆 <b>Lote Kelly Criterion</b>: <b>{kelly_str}</b>\n"
                    f"❄️ <i>Motor de Juros Compostos (Snowball) Ativo!</i>\n\n"
                    f"🎰 <b>Slots Calculados</b>: <b>{slots} posições</b> de <b>${val_slot:.2f} USDT</b> cada."
                )
            except Exception as e:
                return f"Erro ao buscar saldo: {e}"
            finally:
                if c: await c.close_connection()

        elif cmd in ['/top40', '/scanner']:
            c = None
            try:
                c = await BinanceAsyncClient.create(api_key, api_secret)
                await sync_binance_time(c, log=lambda m: None)
                multi_klines = await get_multi_klines(c, TOP_40_SYMBOLS, TRADING_CONFIG['interval'], 50)
                ranked_assets = calculate_relative_strength_rank(multi_klines)
                
                lines = [f"🔥 <b>TOP 5 FORÇA RELATIVA & MOMENTUM (SCANNER 2.0)</b>\n━━━━━━━━━━━━━━━━━━━"]
                for item in ranked_assets[:5]:
                    sym = item['symbol']
                    prc = item['price']
                    rsi_v = item['rsi']
                    rs_v = item['rs_ratio']
                    emoji = "🟢" if rsi_v <= 35 else ("🟡" if rsi_v <= 50 else "⚪")
                    lines.append(f"{emoji} <b>{sym}</b>: <b>{format_price(prc)}</b> | RS: <b>{rs_v:+.1f}%</b> | RSI: <b>{rsi_v:.1f}</b>")
                
                return "\n".join(lines)
            except Exception as e:
                return f"Erro ao buscar scanner: {e}"
            finally:
                if c: await c.close_connection()

        elif cmd in ['/lucro', '/perf']:
            try:
                stats = db.get_stats()
                return (
                    f"📈 <b>PERFORMANCE ACUMULADA</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>Lucro Líquido Total</b>: <b>${stats['total_net_profit']:.2f} USDT</b>\n"
                    f"🎯 <b>Taxa de Vitória</b>: <b>{stats['win_rate']:.1f}%</b>\n"
                    f"📊 <b>Total de Operações</b>: <b>{stats['total_trades']} trades</b>"
                )
            except Exception as e:
                return f"Erro ao ler estatísticas: {e}"

        elif cmd in ['/relatorio', '/pdf']:
            try:
                pdf_path = generate_weekly_telemetry_pdf(db, output_path="docs/Relatorio_Semanal_Telemetria.pdf")
                asyncio.create_task(send_telegram_document(
                    TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                    pdf_path, caption="📊 <b>Relatório Executivo de Telemetria Semanal (PDF) SpotBot Pro v3.0</b>"
                ))
                return "📄 <b>Relatório Executivo em PDF gerado com sucesso! Enviando arquivo no Telegram...</b>"
            except Exception as e:
                return f"Erro ao gerar relatório PDF: {e}"

        elif cmd in ['/ocos', '/ordens', '/posicoes']:
            c = None
            try:
                c = await BinanceAsyncClient.create(api_key, api_secret)
                await sync_binance_time(c, log=lambda m: None)
                open_orders = await c.get_open_orders()
                
                if not open_orders:
                    return "ℹ️ <b>Nenhuma ordem OCO ou posição em aberto no momento.</b>\nO SpotBot Pro está varrendo o mercado em busca de novas oportunidades!"
                
                grouped = {}
                for o in open_orders:
                    sym = o.get('symbol', 'N/A')
                    list_id = o.get('orderListId', -1)
                    key = (sym, list_id)
                    if key not in grouped:
                        grouped[key] = {'symbol': sym, 'tp': 'N/A', 'sl': 'N/A', 'qty': '0'}
                    
                    qty_v = float(o.get('origQty', 0))
                    if qty_v > 0:
                        grouped[key]['qty'] = f"{qty_v:.2f}"
                        
                    o_type = o.get('type', '')
                    if o_type == 'LIMIT_MAKER':
                        price_v = float(o.get('price', 0))
                        if price_v > 0:
                            grouped[key]['tp_raw'] = price_v
                            grouped[key]['tp'] = format_price(price_v)
                    elif 'STOP' in o_type:
                        stop_v = float(o.get('stopPrice', 0))
                        if stop_v == 0:
                            stop_v = float(o.get('price', 0))
                        if stop_v > 0:
                            grouped[key]['sl_raw'] = stop_v
                            grouped[key]['sl'] = format_price(stop_v)

                lines = ["🎯 <b>ORDENS OCO & POSIÇÕES ATIVAS</b>\n━━━━━━━━━━━━━━━━━━━"]
                for (sym, list_id), data in grouped.items():
                    entry_price = active_positions.get(sym, {}).get('entry_price', 0.0)
                    tp_pct = ""
                    sl_pct = ""
                    
                    if entry_price > 0:
                        tp_raw = data.get('tp_raw', 0.0)
                        sl_raw = data.get('sl_raw', 0.0)
                        if tp_raw > 0:
                            tp_pct = f" (+{((tp_raw / entry_price) - 1) * 100:.2f}%)"
                        if sl_raw > 0:
                            sl_pct = f" ({((sl_raw / entry_price) - 1) * 100:.2f}%)"

                    lines.append(
                        f"🪙 Par: <b>{data['symbol']}</b>\n"
                        f"📦 Quantidade: <b>{data['qty']}</b>\n"
                        f"🟢 Take Profit (TP): <b>{data['tp']}</b>{tp_pct}\n"
                        f"🔴 Stop Loss (SL): <b>{data['sl']}</b>{sl_pct}\n"
                    )
                return "\n".join(lines)
            except Exception as e:
                return f"Erro ao buscar ordens OCO: {e}"
            finally:
                if c: await c.close_connection()

        elif cmd in ['/noticias', '/sentimento', '/news']:
            try:
                target_asset = bot_status_data.get('target_asset', 'BTCUSDT')
                headlines = await fetch_crypto_news(target_asset)
                score, is_panic, summary = analyze_news_sentiment_with_gemini(headlines)
                status_emoji = "🚨 PÂNICO" if is_panic else "🟢 ESTÁVEL"
                
                lines = [
                    f"📰 <b>SENTIMENTO DE MERCADO & NOTÍCIAS (IA)</b>\n━━━━━━━━━━━━━━━━━━━",
                    f"🎯 <b>Status</b>: <b>{status_emoji}</b> | <b>Score IA</b>: <b>{score}/100</b>",
                    f"💡 <b>Análise IA Gemini</b>: <i>{summary}</i>\n",
                    f"<b>Manchetes Recentes (CryptoPanic)</b>:"
                ]
                for h in headlines[:4]:
                    lines.append(f"• <i>{h}</i>")
                return "\n".join(lines)
            except Exception as e:
                return f"Erro ao buscar notícias: {e}"

        elif cmd.startswith('/set_risk_'):
            new_prof = cmd.replace('/set_risk_', '').capitalize()
            from config.settings import RISK_PROFILES
            import config.settings as setts
            if new_prof in RISK_PROFILES:
                setts.ACTIVE_RISK_PROFILE = new_prof
                return f"⚙️ <b>Perfil de Risco Alterado!</b>\nNovo Perfil: <b>{new_prof}</b>\n<i>TP: +{RISK_PROFILES[new_prof]['tp_pct']*100:.1f}% | SL: -{RISK_PROFILES[new_prof]['sl_pct']*100:.1f}%</i>"
            return "⚠️ Perfil de risco inválido."

        elif cmd == '/panic_sell_all':
            if active_positions:
                count = len(active_positions)
                for sym in list(active_positions.keys()):
                    asyncio.create_task(panic_sell_position(sym))
                return f"🔥 <b>PANIC SELL DISPARADO!</b>\nEncerrando {count} posição(ões) ativa(s) a mercado imediatamente..."
            return "ℹ️ Nenhuma posição ativa no momento para encerrar."

        elif cmd == '/status_futures':
            c_db = None
            try:
                c_db = db.get_connection()
                cursor = c_db.cursor()
                cursor.execute("SELECT COUNT(*) as total FROM trades WHERE market_type = 'FUTURES'")
                row_total = cursor.fetchone()
                total_trades = row_total["total"] if db.is_postgres else row_total[0]
                
                cursor.execute("SELECT COUNT(*) as wins FROM trades WHERE market_type = 'FUTURES' AND trade_result_net > 0")
                row_wins = cursor.fetchone()
                wins = row_wins["wins"] if db.is_postgres else row_wins[0]
                
                win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
                
                lines = [
                    f"🚀 <b>STATUS DO MOTOR DE FUTUROS</b>",
                    f"━━━━━━━━━━━━━━━━━━━",
                    f"🎯 <b>Trades Realizados (Futuros)</b>: {total_trades}",
                    f"🏆 <b>Win Rate (Futuros)</b>: {win_rate:.1f}%\n"
                ]
                
                active_futures_positions = await futures_state.get_all()
                if not active_futures_positions:
                    lines.append("ℹ️ Nenhuma posição alavancada ativa no momento.")
                else:
                    lines.append("📈 <b>Posições Abertas:</b>")
                    for sym, data in active_futures_positions.items():
                        dir_str = "🟢 LONG" if data.get('direction') == 'LONG' else "🔴 SHORT"
                        lines.append(
                            f"🪙 Par: <b>{sym}</b> | {dir_str}\n"
                            f"🩵 Entrada: <b>${data.get('entry', 0):.4f}</b>\n"
                            f"🎯 TP: <b>${data.get('tp', 0):.4f}</b>\n"
                            f"🛑 SL: <b>${data.get('sl', 0):.4f}</b>\n"
                        )
                return "\n".join(lines)
            except Exception as e:
                return f"Erro ao buscar status do futuros: {e}"
            finally:
                if c_db: db.release_connection(c_db)

        elif cmd == '/saldo_futures':
            c_db = None
            try:
                from services.binance_client import get_futures_usdt_balance
                usdt_balance = await get_futures_usdt_balance(client)
                
                c_db = db.get_connection()
                cursor = c_db.cursor()
                cursor.execute("SELECT SUM(trade_result_net) as pnl FROM trades WHERE market_type = 'FUTURES'")
                row_pnl = cursor.fetchone()
                pnl = row_pnl["pnl"] if db.is_postgres else row_pnl[0]
                pnl = float(pnl) if pnl is not None else 0.0
                
                return (
                    f"💰 <b>SALDOS E PNL (FUTUROS USDS-M)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 Saldo Disponível: <b>${usdt_balance:.2f} USDT</b>\n"
                    f"📈 PnL Acumulado: <b>${pnl:+.2f} USDT</b>"
                )
            except Exception as e:
                return f"Erro ao buscar saldos de futuros: {e}"
            finally:
                if c_db: db.release_connection(c_db)

        elif cmd == '/panic_sell_futures':
            active_futures_positions = await futures_state.get_all()
            if active_futures_positions:
                count = len(active_futures_positions)
                for sym in list(active_futures_positions.keys()):
                    # panic_sell_futures_position expects (client, symbol, qty=0, log=print)
                    asyncio.create_task(panic_sell_futures_position(client, sym, 0, log))
                return f"🔥 <b>PANIC SELL FUTUROS DISPARADO!</b>\nEncerrando {count} posição(ões) alavancada(s) imediatamente..."
            return "ℹ️ Nenhuma posição de Futuros ativa para encerrar."

        elif cmd in ['/ajuda', '/help', '/menu']:
            return (
                "📚 <b>COMANDOS DISPONÍVEIS (SPOTBOT PRO v7.0)</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "📊 /status - Status do ativo em foco, RSI e confluência MTF\n"
                "💰 /saldo - Saldos USDT, BNB e fracionamento de vagas\n"
                "📈 /lucro ou /perf - Lucro líquido acumulado e Win Rate\n"
                "⚡ /posicoes ou /ocos - Ordens OCO e posições ativas\n"
                "📰 /noticias - Sentimento de mercado e notícias CryptoPanic\n"
                "🔥 /top40 ou /scanner - Ranking de Força Relativa do Top 40\n"
                "📄 /relatorio ou /pdf - Gera e envia Relatório Semanal PDF\n"
                "🛑 /stop - Pausa o bot com segurança\n"
                "📱 /menu ou /ajuda - Exibe o menu interativo com subcategorias"
            )
        return "❓ Comando não reconhecido. Digite /ajuda para ver as opções."

    if TELEGRAM_CONFIG.get('bot_token'):
        tg_bot = TelegramBot(TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'], handle_telegram_command)
        asyncio.create_task(tg_bot.start())

    log("\n🚀 \033[5;33mBot SpotBot Pro iniciado!\033[0m 🚀\n")
    
    try:
        db.create_tables()
        db.migrate_from_csv()
    except Exception as e:
        log(f"⚠️ Aviso no banco de dados: {e}")

    await asyncio.sleep(1)
    global client
    try:
        client = await BinanceAsyncClient.create(api_key, api_secret)
        await sync_binance_time(client, log=log)
        bsm = BinanceSocketManager(client)
        
        saldo_inicial_usdt = await get_usdt_balance(client)
        log(f"💰 Saldo USDT disponível: \033[1;32m${saldo_inicial_usdt:.2f}\033[0m")

        is_scanner_mode = False
        if selected_symbol == '⚡ SCANNER TOP 40' or not selected_symbol:
            symbol = "BTCUSDT"
            display_symbol = "⚡ SCANNER TOP 40"
            is_scanner_mode = True
        else:
            symbol = selected_symbol
            display_symbol = selected_symbol

        log(f"🪙 Modo Selecionado: {display_symbol}\n")

        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
            asyncio.create_task(send_telegram_message(
                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                f"<b>🚀 Bot SpotBot Pro iniciado! 🚀</b>\n\n"
                f"💰 Saldo USDT disponível: <b>${saldo_inicial_usdt:.2f}</b>\n"
                f"🪙 Modo Selecionado: <b>{display_symbol}</b>"
            ))
        
        from core.recovery import recover_state
        await recover_state(client, bsm, db, log, status, saldo_inicial_usdt, active_positions, bot_status_data, shared_market_data, active_monitoring_tasks, globals())
        symbol_info = await client.get_symbol_info(symbol)
        tick_size = float(next(filter for filter in symbol_info['filters'] if filter['filterType'] == 'PRICE_FILTER')['tickSize'])
        quote_precision = int(symbol_info['quoteAssetPrecision'])

        # FASE 4 (HedgeFund Edition): Iniciando Motor Paralelo de Futuros
        log("🔄 Sincronizando e Inicializando a Célula de Mercado Futuros...")
        active_monitoring_tasks.append(
            asyncio.create_task(run_futures_bot(client, bsm, db, log, status))
        )

        last_sync_hour = dt_module.datetime.now(TIMEZONE).hour
        last_pdf_sent_day = None
        order_count = 0

        while bot_running:
            try:
                brt_tz = dt_module.timezone(dt_module.timedelta(hours=-3))
                current_dt = dt_module.datetime.now(brt_tz)
                current_hour = current_dt.hour
                
                if current_hour != last_sync_hour:
                    await sync_binance_time(client, log=log)
                    last_sync_hour = current_hour

                if current_dt.weekday() == 6 and current_hour == 20 and last_pdf_sent_day != current_dt.date():
                    last_pdf_sent_day = current_dt.date()
                    try:
                        pdf_path = generate_weekly_telemetry_pdf(db, output_path="docs/Relatorio_Semanal_Telemetria.pdf")
                        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                            asyncio.create_task(send_telegram_document(
                                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                pdf_path, caption="📊 <b>Relatório Executivo de Telemetria Semanal (PDF) SpotBot Pro v3.0</b>"
                            ))
                            log("📄 Relatório Semanal em PDF enviado automaticamente para o Telegram!")
                    except Exception as pdf_err:
                        log(f"⚠️ Erro ao gerar PDF automático de domingo: {pdf_err}")

                # FASE 2: Relatório Diário Automático às 23:59
                if current_hour == 23 and current_dt.minute == 59 and globals().get('_last_daily_report_date') != current_dt.date():
                    globals()['_last_daily_report_date'] = current_dt.date()
                    try:
                        d_stats = db.get_daily_stats()
                        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                            asyncio.create_task(send_telegram_message(
                                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                f"📊 <b>RELATÓRIO DIÁRIO DE TRADING ({d_stats['date']})</b>\n\n"
                                f"🎯 Operações Executadas: <b>{d_stats['trades']}</b>\n"
                                f"🏆 Vitórias / Derrotas: <b>{d_stats['wins']} Wins / {d_stats['losses']} Losses</b>\n"
                                f"📈 Win Rate do Dia: <b>{d_stats['win_rate']:.1f}%</b>\n"
                                f"💰 PnL (Spot): <b>${d_stats['spot_pnl']:+.2f} USDT</b>\n"
                                f"💰 PnL (Futuros): <b>${d_stats['futures_pnl']:+.2f} USDT</b>\n"
                                f"⚖️ PnL Líquido Total: <b>${d_stats['daily_pnl']:+.2f} USDT</b>\n"
                                f"💵 Saldo Livre Atual: <b>${await get_usdt_balance(client):.2f} USDT</b>"
                            ))
                            log("📄 Relatório Diário enviado automaticamente para o Telegram!")
                    except Exception as r_err:
                        log(f"⚠️ Erro ao gerar relatório diário: {r_err}")

                usdt_balance = await get_usdt_balance(client)
                
                # FASE 2: Daily Circuit Breaker (-5.0% Max Drawdown Diário)
                daily_stats = db.get_daily_stats()
                daily_pnl = daily_stats['daily_pnl']
                circuit_breaker_limit = -abs(max(5.0, usdt_balance * 0.05))
                
                if daily_pnl <= circuit_breaker_limit:
                    status(f"🚨 DAILY CIRCUIT BREAKER ATIVADO ({daily_pnl:+.2f} USDT). Novas compras pausadas por 12h...")
                    if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id') and globals().get('_last_cb_alert') != current_dt.date():
                        globals()['_last_cb_alert'] = current_dt.date()
                        asyncio.create_task(send_telegram_message(
                            TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                            f"🚨 <b>DAILY CIRCUIT BREAKER ATIVADO!</b>\n\n"
                            f"📊 Perda acumulada hoje de <b>${daily_pnl:.2f} USDT</b> atingiu o limite de proteção de -5.0%!\n"
                            f"🛡️ Novas compras pausadas por 12 horas enquanto as ordens OCO ativas continuam sendo monitoradas."
                        ))
                    await asyncio.sleep(600)
                    continue

                # FASE 3: Gemini Auto-Tuning de Perfil de Risco a cada 240 min (4h)
                import time
                current_timestamp = time.time()
                if current_timestamp - globals().get('_last_autotune_time', 0) >= 14400:
                    globals()['_last_autotune_time'] = current_timestamp
                    try:
                        db_stats = db.get_stats()
                        acc_pnl = db_stats['total_net_profit']
                        rec_profile, rec_just = auto_tune_risk_profile("ALTA", db_stats['win_rate'], acc_pnl)
                        from config.settings import RISK_PROFILES
                        import config.settings as setts
                        if rec_profile in RISK_PROFILES:
                            if setts.ACTIVE_RISK_PROFILE != rec_profile:
                                setts.ACTIVE_RISK_PROFILE = rec_profile
                                log(f"🧠 \033[1;36mGemini Auto-Tuning\033[0m: Perfil de Risco ajustado para \033[1;32m{rec_profile}\033[0m! ({rec_just})")
                            else:
                                log(f"🧠 \033[1;36mGemini Auto-Tuning\033[0m: Perfil de Risco MANTIDO em \033[1;32m{rec_profile}\033[0m. ({rec_just})")
                                
                            if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                                asyncio.create_task(send_telegram_message(
                                    TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                    f"🧠 <b>GEMINI AUTO-TUNING DE RISCO</b>\n\n"
                                    f"🎯 Perfil Recomendado: <b>{rec_profile}</b>\n"
                                    f"📝 Justificativa IA: <i>{rec_just}</i>"
                                ))
                    except Exception as at_err:
                        log(f"⚠️ Aviso no Gemini Auto-Tuning: {at_err}")

                if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
                    active_list_str = ", ".join(active_positions.keys())
                    status(f"⏳ Posições Máximas Atingidas ({len(active_positions)}/{MAX_CONCURRENT_POSITIONS}): [{active_list_str}]. Monitorando operações...")
                    await asyncio.sleep(6)
                    continue

                db_stats = db.get_stats()
                acc_pnl = db_stats['total_net_profit']

                slots, slot_value = calculate_dynamic_position_slots(
                    usdt_balance,
                    accumulated_net_profit=acc_pnl,
                    max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
                    reserve_fraction_for_dca=RESERVE_FRACTION_FOR_DCA
                )

                active_target_symbol = symbol
                if is_scanner_mode:
                    status(f"⚡ Scanner 2.0 avaliando Top 40... (Vagas Livres: {MAX_CONCURRENT_POSITIONS - len(active_positions)}/{MAX_CONCURRENT_POSITIONS})")
                    multi_klines = await get_multi_klines(client, TOP_40_SYMBOLS, TRADING_CONFIG['interval'], TRADING_CONFIG['limit'])
                    ranked_assets = calculate_relative_strength_rank(multi_klines)
                    if ranked_assets:
                        available_assets = [a for a in ranked_assets if a['symbol'] not in active_positions]
                        if available_assets:
                            active_target_symbol = available_assets[0]['symbol']
                        else:
                            await asyncio.sleep(5)
                            continue

                bot_status_data['target_asset'] = active_target_symbol

                klines = await get_klines(client, active_target_symbol, TRADING_CONFIG['interval'], TRADING_CONFIG['limit'])
                if not klines:
                    await asyncio.sleep(5)
                    continue
            except BinanceAPIException as api_err:
                if api_err.code == -1021:
                    log("⏱️ Erro de dessincronização detectado. Ressincronizando com a Binance...")
                    await sync_binance_time(client, log=log)
                    await asyncio.sleep(2)
                    continue
                else:
                    log(f"Erro na API Binance: {api_err}")
                    await asyncio.sleep(5)
                    continue
            except Exception as e:
                err_desc = str(e) if str(e).strip() else repr(e)
                log(f"⚠️ Instabilidade ao buscar klines ({type(e).__name__}): {err_desc}")
                await asyncio.sleep(5)
                continue

            closes = extract_closes(klines)
            volumes = extract_volumes(klines)
            rsi = calculate_rsi(closes)
            
            volumes_series = pd.Series(volumes)
            volume_ma = volumes_series.dropna().rolling(window=8).mean().iloc[-1]
            
            macd_current, signal_line_current = calculate_macd(closes)
            lower_band, middle_band, upper_band = calculate_bollinger_bands(closes)
            vwap = calculate_vwap(closes, volumes)

            try:
                chart_limit = 50
                if len(klines) > chart_limit:
                    recent_klines = klines[-chart_limit:]
                    shared_market_data['dates'] = [dt_module.datetime.fromtimestamp(int(k[0])/1000).strftime('%H:%M') for k in recent_klines]
                    shared_market_data['klines'] = [[float(k[1]), float(k[4]), float(k[3]), float(k[2])] for k in recent_klines]
                    shared_market_data['volumes'] = [float(k[5]) for k in recent_klines]
                    
                    s_closes = pd.Series(closes)
                    r = s_closes.rolling(window=20)
                    ma = r.mean()
                    std = r.std()
                    shared_market_data['bb_upper'] = (ma + (2 * std)).tail(chart_limit).where(pd.notnull(ma.tail(chart_limit)), None).tolist()
                    shared_market_data['bb_middle'] = ma.tail(chart_limit).where(pd.notnull(ma.tail(chart_limit)), None).tolist()
                    shared_market_data['bb_lower'] = (ma - (2 * std)).tail(chart_limit).where(pd.notnull(ma.tail(chart_limit)), None).tolist()
                    shared_market_data['ema200'] = s_closes.ewm(span=200, adjust=False).mean().tail(chart_limit).where(pd.notnull(s_closes.tail(chart_limit)), None).tolist()
            except Exception:
                pass
                
            # FASE 4: Atualização Periódica do Gemini no Dashboard (1 hora)
            current_timestamp = time.time()
            if current_timestamp - globals().get('_last_gemini_dashboard_update', 0) >= 3600:
                globals()['_last_gemini_dashboard_update'] = current_timestamp
                
                async def update_dashboard_gemini():
                    try:
                        from services.gemini_ai import analyze_with_gemini, interpret_gemini_response
                        import math
                        trend_up = check_trend(klines)
                        
                        gem_resp = await asyncio.to_thread(
                            analyze_with_gemini,
                            "Resumo Gráfico...", "Sem padrões definidos", rsi, f"{macd_current:.2f}/{signal_line_current:.2f}",
                            f"{lower_band:.2f}/{middle_band:.2f}/{upper_band:.2f}", 0, {},
                            closes[-1], closes[-1], closes[-1], closes[-1], volumes[-1], 0, 0,
                            0, 0, 0, 0, 0, 0, vwap, trend_up, 0.65,
                            20, 2, 12, 26, 300, 20, 20, 50, []
                        )
                        if gem_resp:
                            parsed = interpret_gemini_response(gem_resp)
                            if parsed:
                                shared_market_data['gemini_insight'] = parsed
                    except Exception as e:
                        pass
                
                asyncio.create_task(update_dashboard_gemini())

            bot_status_data['symbol'] = display_symbol
            bot_status_data['price'] = closes[-1]
            bot_status_data['rsi'] = rsi
            bot_status_data['trend'] = "Alta" if check_trend(klines) else "Baixa/Neutro"
            
            status(f"📊 RSI ({active_target_symbol}): {rsi:.1f} | Preço: {format_price(closes[-1])}")
            
            if volumes_series.iloc[-1] > volume_ma * (1 + TRADING_CONFIG['volume_avg'] / 100):
                status("⚠️ Alto volume detectado (Volatilidade). Operação em espera.")
                await asyncio.sleep(1)
                continue
                
            trend_is_up = check_trend(klines)
            candle_patterns = check_candle_patterns(klines)
            market_downward = is_market_downward(klines)
            
            await check_rsi_reset(active_target_symbol, log=log)
            
            try:
                if await should_place_order(client, active_target_symbol, status_callback=status) and not market_downward:
                    candle_details = get_candle_details(klines)
                    candle_open = candle_details['open'] if candle_details else 0
                    candle_high = candle_details['high'] if candle_details else 0
                    candle_low = candle_details['low'] if candle_details else 0
                    candle_close = candle_details['close'] if candle_details else 0
                    candle_volume = candle_details['volume'] if candle_details else 0
                    amplitude = ((candle_high - candle_low) / candle_open) * 100 if candle_open != 0 else 0
                    
                    try:
                        price_24h_ago = float(klines[-25][4]) if len(klines) >= 25 else closes[-1]
                        variation_24h = ((closes[-1] - price_24h_ago) / price_24h_ago) * 100 if price_24h_ago != 0 else 0
                        candle_variation = ((candle_close - candle_open) / candle_open) * 100 if candle_open != 0 else 0
                    except Exception:
                        variation_24h, candle_variation = 0, 0

                    ema7 = calculate_ema(closes, 7)
                    ema15 = calculate_ema(closes, 15)
                    ema25 = calculate_ema(closes, 25)
                    ema50 = calculate_ema(closes, 50)
                    ema100 = calculate_ema(closes, 100)
                    ema200 = calculate_ema(closes, 200)

                    buy_result = await should_buy(rsi, trend_is_up, macd_current, signal_line_current, closes[-1], lower_band, middle_band, upper_band, vwap, candle_patterns, candle_open, candle_high, 
                                                  candle_low, candle_close, candle_volume, variation_24h, candle_variation, ema7, ema15, ema25, ema50, ema100, ema200, client, active_target_symbol, klines)
                    
                    if buy_result.get('mtf_score'):
                        bot_status_data['mtf_score'] = buy_result['mtf_score']

                    if buy_result.get('gemini_analysis'):
                         shared_market_data['gemini_insight'] = buy_result['gemini_analysis']

                    if buy_result["buy"]:
                        executed_condition = buy_result["message"]
                        log(f"🟢 Sinal de COMPRA em {active_target_symbol}! Condição: {executed_condition}")
                        
                        target_symbol_info = await client.get_symbol_info(active_target_symbol)
                        min_notional = get_min_notional(target_symbol_info)
                        tick_size = float(next(f for f in target_symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')['tickSize'])
                        step_size = float(next(f for f in target_symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')['stepSize'])
                        
                        pos_multiplier = buy_result.get('position_multiplier', 1.0)
                        safe_usdt_limit = math.floor(usdt_balance * 0.99 * 100) / 100.0
                        
                        # FASE 5 (v5.0): Kelly Criterion Position Sizing Engine
                        k_val, k_pct, is_k_act = calculate_kelly_position_size(db, usdt_balance, default_slot_value=slot_value)
                        target_slot = k_val if is_k_act else slot_value
                        if is_k_act:
                            log(f"🏆 \033[1;36mKelly Criterion Sizing\033[0m: Lote dimensionado em \033[1;32m${target_slot:.2f} USDT\033[0m ({k_pct*100:.1f}% Half-Kelly).")

                        order_val_usdt = max(min_notional, min(safe_usdt_limit, round(target_slot * pos_multiplier, 2)))

                        if usdt_balance < min_notional:
                            log(f"⚠️ Saldo insuficiente (${usdt_balance:.2f}) para o mínimo exigido (${min_notional}).")
                            await asyncio.sleep(5)
                            continue

                        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                            asyncio.create_task(send_telegram_message(
                                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                f"🛒 <b>Ordem de COMPRA Executada!</b>\n\n"
                                f"🪙 Par: <b>{active_target_symbol}</b>\n"
                                f"💵 Preço: <b>{format_price(closes[-1])}</b>\n"
                                f"🎯 Motivo: <i>{executed_condition}</i>\n"
                                f"💰 Valor: <b>${order_val_usdt:.2f} USDT</b> (Slot {len(active_positions)+1}/{MAX_CONCURRENT_POSITIONS})"
                            ))

                        order_count += 1
                        compra = await client.order_market_buy(symbol=active_target_symbol, quoteOrderQty=round(order_val_usdt, quote_precision))
                        
                        # Fix Binance Fee: pega a quantidade real líquida recebida para evitar "Insufficient Balance"
                        base_asset = active_target_symbol.replace('USDT', '')
                        try:
                            asset_balance = await client.get_asset_balance(asset=base_asset)
                            available_qty = float(asset_balance['free'])
                        except Exception:
                            available_qty = float('inf')
                            
                        nominal_qty = float(compra['executedQty'])
                        executed_qty = min(nominal_qty, available_qty)
                        
                        price = float(compra['fills'][0]['price'])
                        timestamp = dt_module.datetime.now(TIMEZONE).strftime("%d/%m/%Y at %H:%M:%S")
                        
                        log(f"✅️ ({order_count:02d}) Comprado: {active_target_symbol} - Qtd: {executed_qty} - Preço: {format_price(price)}")
                        purchase_timestamp = timestamp
                        gemini_response = buy_result.get("gemini_response")

                        oco_order, limit_order_id, stop_order_id, lucro_alvo, stop_loss, stop_limit = await adjust_and_place_oco_order(client, active_target_symbol, executed_qty, tick_size, step_size, klines, log=log)
                        if oco_order and TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                            asyncio.create_task(send_telegram_message(
                                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                f"🎯 <b>Ordem OCO Posicionada!</b>\n\n"
                                f"🪙 Par: <b>{active_target_symbol}</b>\n"
                                f"🟢 Take Profit (TP): <b>{format_price(lucro_alvo)}</b> (+{((lucro_alvo/price)-1)*100:.2f}%)\n"
                                f"🔴 Stop Loss (SL): <b>{format_price(stop_loss)}</b> ({((stop_loss/price)-1)*100:.2f}%)"
                            ))
                        last_operation_time = dt_module.datetime.now(TIMEZONE)

                        # Atualizar dicionário de status para o UI Dashboard exibir as linhas
                        bot_status_data['target_asset'] = active_target_symbol
                        bot_status_data['price'] = price
                        bot_status_data['entry_price'] = price
                        bot_status_data['tp_price'] = lucro_alvo
                        bot_status_data['sl_price'] = stop_loss

                        confluence_score = 0.0
                        if buy_result:
                            if buy_result.get('mtf_score') is not None:
                                confluence_score = float(buy_result['mtf_score'])
                            elif isinstance(buy_result.get('gemini_analysis'), dict):
                                confluence_score = float(buy_result['gemini_analysis'].get('score', 0))
                        slippage = ((price - closes[-1]) / closes[-1]) * 100 if closes[-1] > 0 else 0.0

                        # Lança o monitoramento em background para NÃO bloquear o scanner!
                        task = asyncio.create_task(monitor_oco_lifecycle(
                            client, bsm, active_target_symbol, oco_order, limit_order_id, stop_order_id,
                            price, executed_qty, order_val_usdt, lucro_alvo, stop_loss, target_symbol_info,
                            tick_size, step_size, log, status, saldo_inicial_usdt, order_count, purchase_timestamp,
                            executed_condition, rsi, vwap, candle_open, candle_high, candle_low, candle_close,
                            candle_volume, variation_24h, candle_variation, ema7, ema15, ema25, ema50, ema100,
                            ema200, candle_patterns, amplitude, macd_current, signal_line_current, lower_band,
                            middle_band, upper_band, trend_is_up, gemini_response, db, confluence_score, slippage,
                            active_positions, bot_status_data, globals()
                        ))
                        active_monitoring_tasks.append(task)
            except Exception as trade_exec_err:
                log(f"⚠️ Erro recuperável na execução de ordem: {trade_exec_err}")

            await asyncio.sleep(1)

    except asyncio.CancelledError:
        log("\n🛑 Bot parado pelo usuário.")
        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
            asyncio.create_task(send_telegram_message(
                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                "🛑 <b>Bot parado pelo usuário.</b>"
            ))
    except Exception as e:
        log(f"\n⚠️ Erro de execução: {e}")
    finally:
        bot_running = False
        bot_status_data['is_running'] = False
        for t in active_monitoring_tasks:
            if not t.done():
                t.cancel()
        active_monitoring_tasks.clear()
        if client:
            await client.close_connection()
