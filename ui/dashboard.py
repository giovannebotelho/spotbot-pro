from ui.components.chart import get_main_chart_options
from ui.components.tables import (
    get_recent_trades_columns, get_binance_positions_columns, get_binance_orders_columns
)
import asyncio
import os
import collections
from nicegui import ui, app
import pandas as pd
from datetime import datetime
import datetime as dt_module

from config.settings import DASHBOARD_CONFIG, TRADING_CONFIG, RSI_CONFIG, RISK_PROFILES
import config.settings as settings
from services.database import DatabaseManager
from utils.formatting import remove_ansi_codes, format_price
import core.engine as engine
import core.futures_engine as futures_engine
from core.futures_state import futures_state

db = DatabaseManager()

# Cache para os cards do painel inferior
_cached_pos_rows = []
_cached_ord_rows = []

# Registro de arquivos estáticos (Favicon e Logo)
assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets')
if os.path.exists(assets_dir):
    app.add_static_files('/assets', assets_dir)

# Buffer de Logs Global (guarda os últimos 50 logs para novos visitantes/dispositivos)
logs_buffer = collections.deque(maxlen=50)
_last_chart_sig = None
_last_fut_chart_sig = None
_last_trades_sig = None
_last_tables_fetch = 0

log_ui = None
status_ui = None
investment_input = None
symbol_select = None
status_indicator = None

start_btn = None
stop_btn = None
cancel_btn = None

bnb_val = None
bnb_usdt_val = None
usdt_val = None

total_profit_val = None
win_rate_val = None
recent_trades_table = None
futures_usdt_val = None
futures_profit_val = None
futures_win_rate_val = None

candle_chart = None
scanner_table = None
futures_candle_chart = None
futures_chart_symbol_badge = None

futures_positions_table = None
futures_orders_table = None
news_container = None
hero_card_container = None
fear_greed_val = None
market_cap_val = None
liquidations_val = None
cmc20_val = None
sharpe_val = None
profit_factor_val = None
max_dd_val = None
daily_pnl_val = None
daily_trades_val = None
daily_win_rate_val = None

risk_profile_select = None
paper_trading_switch = None

chart_symbol_badge = None
futures_chart_symbol_badge = None
chart_asset_select = None
chart_tabs = None
selected_chart_symbol = None
_last_rendered_tabs = set()

bot_task = None

def log_handler(message):
    clean_msg = remove_ansi_codes(message)
    logs_buffer.append(clean_msg)
    if log_ui:
        try:
            from nicegui import Client
            if hasattr(log_ui, 'client') and log_ui.client.id in Client.instances:
                log_ui.push(clean_msg)
        except Exception:
            pass

def status_handler(message):
    if status_ui:
        try:
            clean_msg = remove_ansi_codes(message)
            status_ui.content = f"**{clean_msg}**"
        except Exception:
            pass

async def change_chart_asset(val):
    global selected_chart_symbol, _last_chart_sig, _last_fut_chart_sig
    if not val or val == 'foco' or 'Foco do Bot' in val:
        selected_chart_symbol = None
        _last_chart_sig = None
        _last_fut_chart_sig = None
        return
        
    selected_chart_symbol = val
    _last_chart_sig = None
    _last_fut_chart_sig = None
    
    base_val = val.replace('_spot', '').replace('_fut', '')
    is_spot = val.endswith('_spot')
    is_fut = val.endswith('_fut')
    
    try:
        if not candle_chart:
            return

        dates, candles, bb_upper, bb_lower, ema200 = None, None, [], [], []
        
        client_obj = getattr(engine, 'client', None)
        if client_obj:
            try:
                if is_fut:
                    klines_raw = await client_obj.futures_klines(symbol=base_val, interval=engine.TRADING_CONFIG['interval'], limit=50)
                else:
                    klines_raw = await client_obj.get_klines(symbol=base_val, interval=engine.TRADING_CONFIG['interval'], limit=50)
                if klines_raw:
                    dates = [dt_module.datetime.fromtimestamp(int(k[0])/1000).strftime('%H:%M') for k in klines_raw]
                    candles = [[float(k[1]), float(k[4]), float(k[3]), float(k[2])] for k in klines_raw]
                    closes = [float(k[4]) for k in klines_raw]
                    volumes = [float(k[5]) for k in klines_raw]
                    s_closes = pd.Series(closes)
                    ma = s_closes.rolling(window=20).mean()
                    std = s_closes.rolling(window=20).std()
                    bb_upper = (ma + (2 * std)).where(pd.notnull(ma), None).tolist()
                    bb_lower = (ma - (2 * std)).where(pd.notnull(ma), None).tolist()
                    ema200 = s_closes.ewm(span=min(200, len(closes)), adjust=False).mean().where(pd.notnull(s_closes), None).tolist()
            except Exception as k_err:
                print(f"Aviso ao buscar klines via client para {base_val}: {k_err}")

        # Tenta buscar do Spot Engine primeiro
        if not dates and engine.shared_market_data.get('dates') and is_spot:
            market_data = engine.shared_market_data
            dates = market_data['dates']
            candles = market_data['klines']
            bb_upper = market_data.get('bb_upper', [])
            bb_lower = market_data.get('bb_lower', [])
            ema200 = market_data.get('ema200', [])
            volumes = market_data.get('volumes', [])
            
        # Tenta buscar do Futures Engine
        if not dates and futures_engine.shared_futures_market_data.get('dates') and is_fut:
            market_data = futures_engine.shared_futures_market_data
            dates = market_data['dates']
            candles = market_data['klines']
            bb_upper = market_data.get('bb_upper', [])
            bb_lower = market_data.get('bb_lower', [])
            ema200 = market_data.get('ema200', [])
            volumes = market_data.get('volumes', [])

        if dates and candles:
            # OCO/Spot
            pos_info = engine.active_positions.get(base_val, {})
            # Futures
            fut_pos_info = futures_state.get_all_sync().get(base_val, {})
            
            if is_fut:
                e_p = fut_pos_info.get('entry', futures_engine.bot_futures_status_data.get('entry_price', 0.0))
                tp_p = fut_pos_info.get('tp', futures_engine.bot_futures_status_data.get('tp_price', 0.0))
                sl_p = fut_pos_info.get('sl', futures_engine.bot_futures_status_data.get('sl_price', 0.0))
            else:
                e_p = pos_info.get('entry', engine.bot_status_data.get('entry_price', 0.0))
                tp_p = pos_info.get('tp', engine.bot_status_data.get('tp_price', 0.0))
                sl_p = pos_info.get('sl', engine.bot_status_data.get('sl_price', 0.0))
            
            e_str = format_price(e_p)
            tp_str = format_price(tp_p)
            sl_str = format_price(sl_p)

            mark_lines = []
            if e_p > 0:
                mark_lines.append({'yAxis': e_p, 'lineStyle': {'color': '#38BDF8', 'type': 'dashed', 'width': 1.5}, 'label': {'formatter': f' Entrada: {e_str}', 'position': 'insideStartTop', 'color': '#38BDF8'}})
            if tp_p > 0:
                mark_lines.append({'yAxis': tp_p, 'lineStyle': {'color': '#10B981', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🎯 TP: {tp_str}', 'position': 'insideEndTop', 'color': '#10B981'}})
            if sl_p > 0:
                mark_lines.append({'yAxis': sl_p, 'lineStyle': {'color': '#F43F5E', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🛑 SL: {sl_str}', 'position': 'insideEndBottom', 'color': '#F43F5E'}})

            if is_fut and not is_spot:
                candle_chart.options['title'] = {
                    'text': f'🚀 {base_val} (Posição Ativa FUT)',
                    'subtext': f'Entrada: {e_str} | TP: {tp_str} | SL: {sl_str}',
                    'left': 15, 'top': 8,
                    'textStyle': {'color': '#F43F5E', 'fontSize': 13, 'fontWeight': 'bold'},
                    'subtextStyle': {'color': '#64748b', 'fontSize': 9}
                }
                candle_chart.options['xAxis'][0]['data'] = dates
                candle_chart.options['xAxis'][1]['data'] = dates
                candle_chart.options['series'][0]['data'] = candles
                
                fmark_lines = []
                if e_p > 0:
                    fmark_lines.append({'yAxis': e_p, 'lineStyle': {'color': '#F87171', 'type': 'dashed', 'width': 1.5}, 'label': {'formatter': f' Entrada: {e_str}', 'position': 'insideStartTop', 'color': '#F87171'}})
                if tp_p > 0:
                    fmark_lines.append({'yAxis': tp_p, 'lineStyle': {'color': '#10B981', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🎯 TP: {tp_str}', 'position': 'insideEndTop', 'color': '#10B981'}})
                if sl_p > 0:
                    fmark_lines.append({'yAxis': sl_p, 'lineStyle': {'color': '#F43F5E', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🛑 SL: {sl_str}', 'position': 'insideEndBottom', 'color': '#F43F5E'}})
                
                candle_chart.options['series'][0]['markLine'] = {'symbol': ['none', 'none'], 'data': fmark_lines}
                candle_chart.options['series'][1]['data'] = bb_upper
                candle_chart.options['series'][2]['data'] = bb_lower
                candle_chart.options['series'][3]['data'] = ema200
                if 'volumes' in locals():
                    candle_chart.options['series'][7]['data'] = volumes
                candle_chart.update()
            else:
                candle_chart.options['title'] = {
                    'text': f'📈 {base_val} (Posição Ativa OCO)',
                    'subtext': f'Entrada: {e_str} | TP: {tp_str} | SL: {sl_str}',
                    'left': 15, 'top': 8,
                    'textStyle': {'color': '#38BDF8', 'fontSize': 13, 'fontWeight': 'bold'},
                    'subtextStyle': {'color': '#64748b', 'fontSize': 9}
                }
                candle_chart.options['xAxis'][0]['data'] = dates
                candle_chart.options['xAxis'][1]['data'] = dates
                candle_chart.options['series'][0]['data'] = candles
                candle_chart.options['series'][0]['markLine'] = {'symbol': ['none', 'none'], 'data': mark_lines}
                candle_chart.options['series'][1]['data'] = bb_upper
                candle_chart.options['series'][2]['data'] = bb_lower
                candle_chart.options['series'][3]['data'] = ema200
                if 'volumes' in locals():
                    candle_chart.options['series'][7]['data'] = volumes
                candle_chart.update()
    except Exception as e:
        print(f"Aviso ao alterar gráfico para {base_val}: {e}")

@ui.refreshable
def render_chart_tabs():
    global chart_tabs, selected_chart_symbol, _last_rendered_tabs
    
    chart_tabs = ui.tabs(on_change=lambda e: asyncio.create_task(change_chart_asset(e.value))).props('dense active-color=sky-400 indicator-color=sky-400 text-color=slate-400 no-caps').classes('bg-transparent min-h-[40px] text-xs')
    with chart_tabs:
        ui.tab('foco', label='⚡ Foco do Bot (Scanner)', icon='center_focus_strong')
        for sym in sorted(_last_rendered_tabs):
            base_sym = sym.replace('_spot', '').replace('_fut', '')
            is_spot = sym.endswith('_spot')
            is_fut = sym.endswith('_fut')
            label_suffix = '(OCO)' if is_spot else '(FUT)'
            icon = 'show_chart' if is_spot else 'rocket_launch'
            
            ui.tab(sym, label=f'🪙 {base_sym} {label_suffix}' if is_spot else f'🚀 {base_sym} {label_suffix}', icon=icon).classes('text-sky-400' if is_spot else 'text-rose-400')
            
    if selected_chart_symbol and selected_chart_symbol in _last_rendered_tabs:
        chart_tabs.value = selected_chart_symbol
    else:
        chart_tabs.value = 'foco'

@ui.refreshable
def render_positions_panel():
    global _cached_pos_rows
    if not _cached_pos_rows:
        with ui.column().classes('w-full py-8 items-center justify-center text-slate-500 gap-2'):
            ui.label('⚡ Monitorando novas operações de Futuros...').classes('text-xs font-mono')
            ui.label('Nenhuma posição finalizada nas últimas horas.').classes('text-[0.65rem] text-slate-600')
        return

    with ui.column().classes('w-full gap-2 p-1'):
        for p in _cached_pos_rows:
            pnl_color = 'text-emerald-400' if not p.get('pnl', '').startswith('-') else 'text-rose-400'
            pnl_bg = 'bg-emerald-500/10 border-emerald-500/30' if not p.get('pnl', '').startswith('-') else 'bg-rose-500/10 border-rose-500/30'
            
            with ui.row().classes(f'w-full justify-between items-center p-2.5 rounded-xl glass-panel border {pnl_bg} shadow-sm gap-2'):
                with ui.row().classes('items-center gap-2 min-w-[140px]'):
                    ui.label(p.get('symbol', 'BTCUSDT')).classes('font-bold text-xs text-white font-mono')
                    ui.label(p.get('leverage', '15x')).classes('text-[0.6rem] bg-slate-800 text-sky-400 px-1.5 py-0.5 rounded font-mono font-semibold')
                    ui.label(p.get('status', 'Fechada')).classes('text-[0.6rem] text-slate-400')
                
                with ui.row().classes('items-center gap-4 text-xs font-mono flex-wrap'):
                    with ui.column().classes('gap-0 items-end'):
                        ui.label('PnL Realizado').classes('text-[0.6rem] text-slate-500')
                        ui.label(p.get('pnl', '$0.00')).classes(f'font-bold {pnl_color}')
                    with ui.column().classes('gap-0 items-end hidden sm:flex'):
                        ui.label('ROI').classes('text-[0.6rem] text-slate-500')
                        ui.label(p.get('roi', '0.0%')).classes(f'font-bold {pnl_color}')
                    with ui.column().classes('gap-0 items-end hidden md:flex'):
                        ui.label('Entrada / Saída').classes('text-[0.6rem] text-slate-500')
                        ui.label(f"{p.get('entry', '$0.00')} ➔ {p.get('exit', '$0.00')}").classes('text-slate-300')
                    with ui.column().classes('gap-0 items-end'):
                        ui.label('Horário').classes('text-[0.6rem] text-slate-500')
                        ui.label(p.get('time', 'Hoje')).classes('text-slate-400')

@ui.refreshable
def render_orders_panel():
    global _cached_ord_rows
    if not _cached_ord_rows:
        with ui.column().classes('w-full py-8 items-center justify-center text-slate-500 gap-2'):
            ui.label('⚡ Monitorando livro de ordens ativas...').classes('text-xs font-mono')
            ui.label('Nenhuma ordem recente no buffer.').classes('text-[0.65rem] text-slate-600')
        return

    with ui.column().classes('w-full gap-2 p-1'):
        for o in _cached_ord_rows:
            side_color = 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10' if o.get('side') == 'COMPRAR' else 'text-rose-400 border-rose-500/40 bg-rose-500/10'
            
            with ui.row().classes('w-full justify-between items-center p-2.5 rounded-xl glass-panel border border-white/5 shadow-sm gap-2'):
                with ui.row().classes('items-center gap-2 min-w-[140px]'):
                    ui.label(o.get('symbol', 'BTCUSDT')).classes('font-bold text-xs text-white font-mono')
                    ui.label(o.get('side', 'COMPRAR')).classes(f'text-[0.6rem] px-1.5 py-0.5 rounded font-mono font-bold border {side_color}')
                    ui.label(o.get('type', 'MARKET')).classes('text-[0.6rem] text-slate-400 font-mono')
                
                with ui.row().classes('items-center gap-4 text-xs font-mono flex-wrap'):
                    with ui.column().classes('gap-0 items-end'):
                        ui.label('Preço Médio').classes('text-[0.6rem] text-slate-500')
                        ui.label(o.get('avg_price', '$0.00')).classes('font-bold text-slate-200')
                    with ui.column().classes('gap-0 items-end hidden sm:flex'):
                        ui.label('Executado / Valor').classes('text-[0.6rem] text-slate-500')
                        ui.label(f"{o.get('executed', '0.000')} ({o.get('value', '$0.00')})").classes('text-slate-300')
                    with ui.column().classes('gap-0 items-end hidden md:flex'):
                        ui.label('Status').classes('text-[0.6rem] text-slate-500')
                        ui.label(o.get('status', 'Executado')).classes('text-sky-400 font-bold')
                    with ui.column().classes('gap-0 items-end'):
                        ui.label('Horário').classes('text-[0.6rem] text-slate-500')
                        ui.label(o.get('time', 'Hoje')).classes('text-slate-400')

async def update_data():
    global start_btn, stop_btn, status_indicator, _last_chart_sig, total_profit_val, win_rate_val, futures_usdt_val, futures_profit_val, futures_win_rate_val
    try:
        # Sincronização de Estado dos Botões entre Dispositivos (PC / Celular)
        is_running = engine.bot_running or (bot_task is not None and not bot_task.done())
        
        if is_running:
            if start_btn:
                start_btn.props('disable')
                start_btn.classes(remove='bg-[#059669] hover:bg-[#10B981] text-white shadow-md', add='bg-slate-800 text-emerald-400 border border-emerald-500/30 opacity-90')
            if stop_btn:
                stop_btn.props(remove='disable')
                stop_btn.classes(remove='bg-slate-800 text-slate-500 opacity-50', add='bg-[#BE123C] hover:bg-rose-700 text-white shadow-lg animate-pulse')
            if status_indicator:
                status_indicator.classes(remove='bg-[#BE123C] bg-[#D97706]', add='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]')
        else:
            if start_btn:
                start_btn.props(remove='disable')
                start_btn.classes(remove='bg-slate-800 text-emerald-400 border border-emerald-500/30 opacity-90', add='bg-[#059669] hover:bg-[#10B981] text-white font-bold shadow-md')
            if stop_btn:
                stop_btn.props('disable')
                stop_btn.classes(remove='bg-[#BE123C] hover:bg-rose-700 text-white shadow-lg animate-pulse', add='bg-slate-800 text-slate-500 opacity-50')
            if status_indicator:
                status_indicator.classes(remove='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]', add='bg-[#BE123C]')

        # Consulta de Saldos e Estatísticas
        balances = await engine.get_account_balances()
        if balances:
            if bnb_val: bnb_val.text = f"{balances['bnb']:.4f}"
            if bnb_usdt_val: bnb_usdt_val.text = f"~${balances['bnb_usdt']:.2f}"
            if usdt_val: usdt_val.text = f"${balances['usdt']:.2f}"
            if futures_usdt_val and 'futures_usdt' in balances:
                futures_usdt_val.text = f"${balances['futures_usdt']:.2f}"
        
        stats = await asyncio.to_thread(db.get_stats)
        if total_profit_val:
            total_profit_val.text = f"${stats['spot_net_profit']:.2f}"
            total_profit_val.classes(remove='text-emerald-400 text-rose-400', add='text-[#10B981]' if stats['spot_net_profit'] >= 0 else 'text-[#F43F5E]')
        if win_rate_val:
            win_rate_val.text = f"{stats['spot_win_rate']:.1f}%"
            
        if futures_profit_val:
            futures_profit_val.text = f"${stats['futures_net_profit']:.2f}"
            futures_profit_val.classes(remove='text-emerald-400 text-rose-400', add='text-[#10B981]' if stats['futures_net_profit'] >= 0 else 'text-[#F43F5E]')
        if futures_win_rate_val:
            futures_win_rate_val.text = f"{stats.get('futures_win_rate', 0.0):.1f}%"

        # Métricas de Hedge Fund (Sharpe, Profit Factor, Drawdown)
        if sharpe_val:
            sharpe_val.text = f"{stats.get('sharpe_ratio', 0.0):.2f}"
        if profit_factor_val:
            profit_factor_val.text = f"{stats.get('profit_factor', 1.0):.2f}x"
        if max_dd_val:
            max_dd_val.text = f"-{stats.get('max_drawdown_pct', 0.0):.1f}%"

        # Resumo Diário (Hoje)
        daily_stats = await asyncio.to_thread(db.get_daily_stats)
        if daily_pnl_val:
            d_pnl = daily_stats.get('daily_pnl', 0.0)
            daily_pnl_val.text = f"${d_pnl:+.2f}"
            daily_pnl_val.classes(remove='text-emerald-400 text-rose-400', add='text-[#10B981]' if d_pnl >= 0 else 'text-[#F43F5E]')
        if daily_trades_val:
            daily_trades_val.text = f"{daily_stats.get('trades', 0)} ({daily_stats.get('wins', 0)}W/{daily_stats.get('losses', 0)}L)"
        if daily_win_rate_val:
            daily_win_rate_val.text = f"{daily_stats.get('win_rate', 0.0):.1f}%"
        
        if futures_usdt_val:
            try:
                from core.futures_engine import get_futures_usdt_balance
                if getattr(engine, 'client', None) is not None:
                    # Verifica se a sessão do client não foi fechada pelo usuário parando o bot
                    if getattr(engine.client, 'session', None) is not None and getattr(engine.client.session, 'closed', False) == False:
                        fut_bal = await get_futures_usdt_balance(engine.client)
                        futures_usdt_val.text = f"${fut_bal:.2f}"
            except Exception:
                pass
        
        # Sincronização Automática do Perfil de Risco (Gemini Auto-Tuning)
        if risk_profile_select and settings.ACTIVE_RISK_PROFILE:
            if risk_profile_select.value != settings.ACTIVE_RISK_PROFILE:
                risk_profile_select.value = settings.ACTIVE_RISK_PROFILE

        # Sincronização Dinâmica de Abas do Gráfico (Multi-Ativo)
        if 'chart_tabs' in globals() and chart_tabs:
            current_active_keys = set([s + '_spot' for s in engine.active_positions.keys()]).union(set([s + '_fut' for s in futures_state.get_all_sync().keys()]))
            global _last_rendered_tabs
            if current_active_keys != _last_rendered_tabs:
                _last_rendered_tabs = current_active_keys.copy()
                render_chart_tabs.refresh()
                
                if current_active_keys and (not selected_chart_symbol or selected_chart_symbol == 'foco'):
                    first_sym = sorted(current_active_keys)[0]
                    await change_chart_asset(first_sym)

        active_symbol = engine.bot_status_data.get('target_asset', 'BTCUSDT')
        current_price = engine.bot_status_data.get('price', 0.0)
        price_str = format_price(current_price)

        if chart_symbol_badge:
            active_symbols = list(engine.active_positions.keys())
            if active_symbols:
                active_str = " | ".join([f"{s}" for s in active_symbols])
                chart_symbol_badge.text = f"⚡ VAGAS SPOT ({len(active_symbols)}/{engine.MAX_CONCURRENT_POSITIONS}): [{active_str}]"
            else:
                chart_symbol_badge.text = f"🪙 {active_symbol} ({price_str})"

        if futures_chart_symbol_badge:
            fut_active_symbols = list(futures_state.get_all_sync().keys())
            if fut_active_symbols:
                fut_active_str = " | ".join([f"{s}" for s in fut_active_symbols])
                futures_chart_symbol_badge.text = f"🚀 VAGAS FUTUROS ({len(fut_active_symbols)}/3): [{fut_active_str}]"
                futures_chart_symbol_badge.set_visibility(True)
            else:
                futures_chart_symbol_badge.set_visibility(False)

        base_selected = selected_chart_symbol.replace('_spot', '').replace('_fut', '') if selected_chart_symbol else None

        if selected_chart_symbol and selected_chart_symbol.endswith('_spot') and base_selected in engine.active_positions:
            pos_data = engine.active_positions[base_selected]
            entry_price = pos_data.get('entry', 0.0)
            tp_price = pos_data.get('tp', 0.0)
            sl_price = pos_data.get('sl', 0.0)
        else:
            entry_price = engine.bot_status_data.get('entry_price', 0.0)
            tp_price = engine.bot_status_data.get('tp_price', 0.0)
            sl_price = engine.bot_status_data.get('sl_price', 0.0)

        market_data = engine.shared_market_data
        
        # Só injeta os candles do Scanner se estiver na aba FOCO
        is_foco = not selected_chart_symbol or selected_chart_symbol == 'foco'
        
        if market_data['dates'] and candle_chart and is_foco:
            current_sig = (active_symbol, price_str, len(market_data['dates']), market_data['dates'][-1] if market_data['dates'] else '', tp_price, sl_price, entry_price)
            if current_sig != _last_chart_sig:
                _last_chart_sig = current_sig
                candle_chart.options['title'] = {
                    'text': f'📈 {active_symbol}',
                    'subtext': 'Binance WebSockets & Scanner Quantitativo',
                    'left': 15,
                    'top': 8,
                    'textStyle': {'color': '#38BDF8', 'fontSize': 13, 'fontWeight': 'bold'},
                    'subtextStyle': {'color': '#64748b', 'fontSize': 9}
                }
                candle_chart.options['xAxis'][0]['data'] = market_data['dates']
                candle_chart.options['xAxis'][1]['data'] = market_data['dates']
                candle_chart.options['series'][0]['data'] = market_data['klines']

                # Desenha as Linhas OCO ativas (TP, SL e Preço de Entrada) Estilo Binance
                e_str = format_price(entry_price)
                tp_str = format_price(tp_price)
                sl_str = format_price(sl_price)
                
                mark_lines = []
                if entry_price > 0:
                    mark_lines.append({'yAxis': entry_price, 'lineStyle': {'color': '#38BDF8', 'type': 'dashed', 'width': 1.5}, 'label': {'formatter': f' Entrada: {e_str}', 'position': 'insideStartTop', 'color': '#38BDF8'}})
                if tp_price > 0:
                    mark_lines.append({'yAxis': tp_price, 'lineStyle': {'color': '#10B981', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🎯 TP: {tp_str}', 'position': 'insideEndTop', 'color': '#10B981'}})
                if sl_price > 0:
                    mark_lines.append({'yAxis': sl_price, 'lineStyle': {'color': '#F43F5E', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🛑 SL: {sl_str}', 'position': 'insideEndBottom', 'color': '#F43F5E'}})
                
                candle_chart.options['series'][0]['markLine'] = {'symbol': ['none', 'none'], 'data': mark_lines}

                candle_chart.options['series'][1]['data'] = market_data.get('bb_upper', [])
                candle_chart.options['series'][2]['data'] = market_data.get('bb_lower', [])
                candle_chart.options['series'][3]['data'] = market_data.get('ema200', [])
                candle_chart.options['series'][7]['data'] = market_data.get('volumes', [])
                candle_chart.update()

        # ---------- FUTURES CHART UPDATE ----------
        fut_active_symbol = futures_engine.bot_futures_status_data.get('target_asset', 'BTCUSDT')
        fut_current_price = futures_engine.bot_futures_status_data.get('price', 0.0)
        fut_price_str = format_price(fut_current_price)
        
        fut_state = futures_state.get_all_sync()
        if selected_chart_symbol and selected_chart_symbol.endswith('_fut') and base_selected in fut_state:
            pos_data = fut_state[base_selected]
            fut_entry_price = pos_data.get('entry', 0.0)
            fut_tp_price = pos_data.get('tp', 0.0)
            fut_sl_price = pos_data.get('sl', 0.0)
        else:
            fut_entry_price = futures_engine.bot_futures_status_data.get('entry_price', 0.0)
            fut_tp_price = futures_engine.bot_futures_status_data.get('tp_price', 0.0)
            fut_sl_price = futures_engine.bot_futures_status_data.get('sl_price', 0.0)

        fut_market_data = getattr(futures_engine, 'shared_futures_market_data', None)
        if fut_market_data and fut_market_data['dates'] and candle_chart and is_foco:
            fut_sig = (fut_active_symbol, fut_price_str, len(fut_market_data['dates']), fut_market_data['dates'][-1] if fut_market_data['dates'] else '', fut_tp_price, fut_sl_price, fut_entry_price)
            # using same _last_chart_sig check logic but for futures (create a new one)
            global _last_fut_chart_sig
            if 'fut_sig' not in globals() or fut_sig != globals().get('_last_fut_chart_sig'):
                globals()['_last_fut_chart_sig'] = fut_sig
                candle_chart.options['title'] = {
                    'text': f'🚀 {fut_active_symbol} (HedgeFund)',
                    'subtext': 'Binance Futures WebSockets',
                    'left': 15,
                    'top': 8,
                    'textStyle': {'color': '#F43F5E', 'fontSize': 13, 'fontWeight': 'bold'},
                    'subtextStyle': {'color': '#64748b', 'fontSize': 9}
                }
                candle_chart.options['xAxis'][0]['data'] = fut_market_data['dates']
                candle_chart.options['xAxis'][1]['data'] = fut_market_data['dates']
                candle_chart.options['series'][0]['data'] = fut_market_data['klines']

                # MarkLines Futuros
                fe_str = format_price(fut_entry_price)
                ftp_str = format_price(fut_tp_price)
                fsl_str = format_price(fut_sl_price)
                
                fmark_lines = []
                if fut_entry_price > 0:
                    fmark_lines.append({'yAxis': fut_entry_price, 'lineStyle': {'color': '#F87171', 'type': 'dashed', 'width': 1.5}, 'label': {'formatter': f' Entrada: {fe_str}', 'position': 'insideStartTop', 'color': '#F87171'}})
                if fut_tp_price > 0:
                    fmark_lines.append({'yAxis': fut_tp_price, 'lineStyle': {'color': '#10B981', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🎯 TP: {ftp_str}', 'position': 'insideEndTop', 'color': '#10B981'}})
                if fut_sl_price > 0:
                    fmark_lines.append({'yAxis': fut_sl_price, 'lineStyle': {'color': '#F43F5E', 'type': 'dashed', 'width': 2}, 'label': {'formatter': f'🛑 SL: {fsl_str}', 'position': 'insideEndBottom', 'color': '#F43F5E'}})
                
                candle_chart.options['series'][0]['markLine'] = {'symbol': ['none', 'none'], 'data': fmark_lines}

                candle_chart.options['series'][1]['data'] = fut_market_data.get('bb_upper', [])
                candle_chart.options['series'][2]['data'] = fut_market_data.get('bb_lower', [])
                candle_chart.options['series'][3]['data'] = fut_market_data.get('ema200', [])
                candle_chart.options['series'][7]['data'] = fut_market_data.get('volumes', [])
                candle_chart.update()



        # Conecta o insight do Gemini (do Spot ou do Futuros News)
        from services.futures_gemini_news import _news_cache
        target_sym = fut_active_symbol if is_fut else active_symbol
        fut_news_item = _news_cache.get(target_sym, {})
        
        insight = engine.shared_market_data.get('gemini_insight')
        if fut_news_item and 'score' in fut_news_item:
            score = fut_news_item.get('score', 50)
            fdir = fut_news_item.get('direction')
            sig_text = f"BULLISH ({score}/100)" if fdir == 'LONG' else (f"BEARISH ({score}/100)" if fdir == 'SHORT' else f"NEUTRO ({score}/100)")
            ai_signal_label.text = sig_text
            ai_signal_label.classes(remove='text-slate-400 text-rose-400 text-amber-400 text-emerald-400', 
                                    add='text-emerald-400' if fdir == 'LONG' else ('text-rose-400' if fdir == 'SHORT' else 'text-amber-400'))
            ai_reason_markdown.content = f"**{target_sym} (Sentimento IA):** {fut_news_item.get('reason', 'Monitorando fluxo e notícias do ativo.')}"
        elif insight and ai_signal_label and ai_reason_markdown:
            signal = insight.get('signal', 'N/A')
            ai_signal_label.text = f"{signal}"
            if signal == 'COMPRA':
                ai_signal_label.classes(remove='text-slate-400 text-rose-400 text-amber-400', add='text-emerald-400')
            elif signal == 'VENDA':
                ai_signal_label.classes(remove='text-slate-400 text-emerald-400 text-amber-400', add='text-rose-400')
            else:
                ai_signal_label.classes(remove='text-emerald-400 text-rose-400', add='text-amber-400')
            ai_reason_markdown.content = insight.get('justification', '**Sem justificativa disponível.**')

        # Atualiza o Histórico de Posições e Ordens da Binance (com throttle inteligente de 15 segundos)
        global _last_tables_fetch, _cached_pos_rows, _cached_ord_rows
        now_ts = time.time() if 'time' in globals() else dt_module.datetime.now().timestamp()
        if now_ts - _last_tables_fetch > 15:
            _last_tables_fetch = now_ts
            try:
                from services.binance_client import fetch_binance_futures_trades, fetch_binance_futures_orders
                from binance import AsyncClient as BinanceAsyncClient
                from config.settings import API_KEYS, PAPER_TRADING_MODE
                
                cli_to_use = getattr(engine, 'client', None)
                created_cli = False
                if not cli_to_use:
                    if PAPER_TRADING_MODE:
                        k = API_KEYS.get('testnet_futures', {}).get('key', '')
                        s = API_KEYS.get('testnet_futures', {}).get('secret', '')
                        cli_to_use = await BinanceAsyncClient.create(k, s, testnet=True)
                    else:
                        k = API_KEYS.get('mainnet', {}).get('key', '')
                        s = API_KEYS.get('mainnet', {}).get('secret', '')
                        cli_to_use = await BinanceAsyncClient.create(k, s, testnet=False)
                    created_cli = True

                # 1. Posições Fechadas / Trades com PnL
                b_trades = await fetch_binance_futures_trades(cli_to_use, limit=20)
                if b_trades:
                    pos_rows = []
                    for t in b_trades:
                        t_pnl = float(t.get('realizedPnl', 0.0))
                        t_qty = float(t.get('qty', 0.0))
                        t_price = float(t.get('price', 0.0))
                        t_time_ms = int(t.get('time', 0))
                        t_time_str = dt_module.datetime.fromtimestamp(t_time_ms/1000).strftime('%d/%m %H:%M')
                        pnl_str = f"+${t_pnl:.2f}" if t_pnl >= 0 else f"-${abs(t_pnl):.2f}"
                        
                        pos_rows.append({
                            'symbol': f"{t.get('symbol', '')} Perp",
                            'leverage': '15x-50x Isolada',
                            'status': 'Fechada' if t.get('reduceOnly') or t_pnl != 0 else 'Aberta',
                            'pnl': pnl_str,
                            'roi': f"{(t_pnl / max(1.0, (t_price * t_qty / 15))) * 100:+.2f}%" if t_pnl != 0 else "0.0%",
                            'qty': f"{t_qty:.3f}",
                            'entry': f"${t_price:.4f}",
                            'exit': f"${t_price:.4f}",
                            'time': t_time_str
                        })
                    _cached_pos_rows = pos_rows
                    render_positions_panel.refresh()
                
                # 2. Histórico de Ordens
                b_orders = await fetch_binance_futures_orders(cli_to_use, limit=20)
                if b_orders:
                    ord_rows = []
                    for o in b_orders:
                        o_time = dt_module.datetime.fromtimestamp(int(o.get('time', o.get('updateTime', 0)))/1000).strftime('%d/%m %H:%M:%S')
                        o_side = "COMPRAR" if o.get('side') == 'BUY' else "VENDER"
                        o_val = float(o.get('cumQuote', 0.0))
                        if o_val == 0:
                            o_val = float(o.get('origQty', 0)) * float(o.get('price', o.get('avgPrice', 0)))
                        
                        ord_rows.append({
                            'time': o_time,
                            'symbol': o.get('symbol'),
                            'type': o.get('type', 'MARKET'),
                            'side': o_side,
                            'avg_price': f"${float(o.get('avgPrice', 0)):.4f}" if float(o.get('avgPrice', 0)) > 0 else f"${float(o.get('price', 0)):.4f}",
                            'price': f"${float(o.get('price', 0)):.4f}",
                            'executed': f"{float(o.get('executedQty', 0)):.3f}",
                            'value': f"${o_val:.2f}",
                            'reduce_only': 'Sim' if o.get('reduceOnly') else 'Não',
                            'status': 'Executado' if o.get('status') == 'FILLED' else o.get('status', 'NEW')
                        })
                    _cached_ord_rows = ord_rows
                    render_orders_panel.refresh()

                if created_cli and cli_to_use:
                    await cli_to_use.close_connection()

            except Exception as hist_err:
                pass
        elif not _cached_pos_rows:
            # Fallback inicial usando o Banco de Dados para que as tabelas já venham preenchidas
            try:
                recent_df = await asyncio.to_thread(db.get_recent_trades, limit=15)
                if not recent_df.empty:
                    db_rows = []
                    for _, row in recent_df.iterrows():
                        pnl_val = float(row.get('Resultado Total Liquido', row.get('trade_result_net', 0.0)))
                        sym = row.get('Símbolo', row.get('symbol', 'BTCUSDT'))
                        m_type = row.get('market_type', 'FUTURES')
                        pnl_s = f"+${pnl_val:.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):.2f}"
                        db_rows.append({
                            'symbol': f"{sym} {'Perp' if m_type == 'FUTURES' else 'Spot'}",
                            'leverage': '15x Isolada' if m_type == 'FUTURES' else '1x Spot',
                            'status': 'Fechada',
                            'pnl': pnl_s,
                            'roi': f"{(pnl_val / 50.0) * 100:+.2f}%",
                            'qty': '0.050',
                            'entry': f"${float(row.get('Preço de Entrada', 0.0)):.4f}" if float(row.get('Preço de Entrada', 0.0)) > 0 else "N/A",
                            'exit': f"${float(row.get('Preço de Saída', 0.0)):.4f}" if float(row.get('Preço de Saída', 0.0)) > 0 else "N/A",
                            'time': str(row.get('Data/Hora da Compra', row.get('oco_timestamp', 'Hoje')))
                        })
                    _cached_pos_rows = db_rows
                    render_positions_panel.refresh()
            except Exception:
                pass

    except Exception:
        pass



async def start_bot():
    global bot_task
    if bot_task and not bot_task.done():
        ui.notify('Bot já está rodando!', type='warning')
        return

    investment = investment_input.value if investment_input else None
    symbol = symbol_select.value if symbol_select else None
    
    if not investment:
        ui.notify('Investimento não definido.', type='warning')
        return
    
    ui.notify('Iniciando Sistema SpotBot Pro...', type='positive')
    engine.bot_running = True
    if status_indicator: 
        status_indicator.classes(remove='bg-[#BE123C] bg-[#D97706]', add='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]')
    
    bot_task = asyncio.create_task(engine.run_bot(log_callback=log_handler, investment_amount=investment, selected_symbol=symbol, status_callback=status_handler))
    
    try:
        await bot_task
    except asyncio.CancelledError:
        try:
            ui.notify('Sistema Parado.', type='info')
        except Exception:
            pass
        if status_indicator: 
            status_indicator.classes(remove='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]', add='bg-[#BE123C]')
    except Exception as e:
        try:
            ui.notify(f'Erro: {e}', type='negative')
        except Exception:
            pass
        if status_indicator: 
            status_indicator.classes(remove='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]', add='bg-[#BE123C]')

def stop_bot():
    global bot_task
    engine.bot_running = False
    engine.bot_status_data['is_running'] = False
    if bot_task and not bot_task.done():
        bot_task.cancel()
    ui.notify('🛑 Parando Sistema com segurança...', type='info')
    if status_indicator: 
        status_indicator.classes(remove='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]', add='bg-[#BE123C]')

def cancel_bot():
    global bot_task
    engine.bot_running = False
    engine.bot_status_data['is_running'] = False
    if bot_task and not bot_task.done():
        bot_task.cancel()
    ui.notify('🚨 EMERGÊNCIA: Execução abortada via Cancel!', type='negative')
    if status_indicator: 
        status_indicator.classes(remove='bg-[#10B981] animate-pulse shadow-[0_0_15px_#10B981]', add='bg-[#D97706]')

async def panic_sell():
    active_syms = engine.bot_status_data.get('active_symbols', [])
    target = engine.bot_status_data.get('target_asset')
    syms_to_sell = list(active_syms) if active_syms else ([target] if target else [])
    
    if not syms_to_sell:
        ui.notify('Nenhuma posição ativa detectada para Panic Sell.', type='warning')
        return
        
    for sym in syms_to_sell:
        ui.notify(f'🚨 Panic Sell acionado para {sym}...', type='warning')
        success, msg = await engine.panic_sell_position(sym)
        if success:
            ui.notify(msg, type='positive')
        else:
            ui.notify(msg, type='negative')

def update_timeframe(value):
    engine.TRADING_CONFIG['interval'] = value
    engine.shared_market_data['klines'] = []
    engine.shared_market_data['dates'] = []
    ui.notify(f'Timeframe alterado para: {value}', type='info')

def set_risk_profile(val):
    if val in RISK_PROFILES:
        prof = RISK_PROFILES[val]
        TRADING_CONFIG['min_adx'] = prof['adx_min']
        RSI_CONFIG['dynamic_low'][0] = prof['rsi_threshold']
        ui.notify(f'Perfil alterado: {val} (RSI <= {prof["rsi_threshold"]}, ADX >= {prof["adx_min"]})', type='info')

def toggle_paper_trading(e):
    settings.PAPER_TRADING = e.value
    status_text = "Simulação Ativa 🧪" if e.value else "Conta Real 💰"
    ui.notify(f'Modo: {status_text}', type='positive' if e.value else 'warning')

@ui.page('/login')
def login():
    USER = DASHBOARD_CONFIG['user']
    PASS = DASHBOARD_CONFIG['password']
    
    def try_login():
        if username.value == USER and password.value == PASS:
            app.storage.user['authenticated'] = True
            ui.navigate.to('/')
        else:
            ui.notify('Acesso Negado', type='negative')

    ui.colors(primary='#0ea5e9', secondary='#64748b', accent='#10b981', positive='#10b981', negative='#f43f5e', dark='#020617')
    ui.add_head_html('''
        <link rel="icon" type="image/x-icon" href="/assets/favicon.ico">
        <link rel="shortcut icon" href="/assets/favicon.ico">
        <link rel="apple-touch-icon" href="/assets/logo.png">
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        <style>
            body { background: radial-gradient(circle at top left, #0f172a, #020617 100%); color: #f8fafc; font-family: 'Outfit', sans-serif; margin: 0; }
            .glass-panel { background: rgba(15, 23, 42, 0.4); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.05); }
            .zinc-input .q-field__native { color: white !important; font-family: 'Outfit', sans-serif; }
            .zinc-input .q-field__label { color: #94a3b8 !important; font-family: 'Outfit', sans-serif; }
            .zinc-input .q-field__control:before { border-color: rgba(255,255,255,0.1) !important; }
        </style>
    ''')

    with ui.column().classes('w-full h-screen items-center justify-center px-4 relative overflow-hidden'):
        ui.element('div').classes('absolute -top-[20%] -left-[10%] w-[50%] h-[50%] bg-sky-600/20 rounded-full blur-[120px] pointer-events-none')
        ui.element('div').classes('absolute -bottom-[20%] -right-[10%] w-[50%] h-[50%] bg-indigo-600/20 rounded-full blur-[120px] pointer-events-none')
        
        with ui.card().classes('w-full max-w-sm p-8 glass-panel shadow-[0_0_40px_rgba(14,165,233,0.15)] items-center gap-6 rounded-3xl transition-transform hover:scale-[1.02] duration-500'):
            with ui.column().classes('items-center gap-2'):
                ui.image('/assets/logo.png').classes('w-16 h-16 rounded-2xl shadow-[0_0_20px_rgba(14,165,233,0.4)] border border-sky-400/30 animate-pulse')
                ui.label('SPOTBOT PRO v7.0').classes('text-3xl font-extrabold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-sky-400 to-indigo-400 drop-shadow-sm')
                ui.label('HEDGEFUND QUANTITATIVE ENGINE').classes('text-[0.65rem] text-emerald-400 tracking-[0.2em] font-bold uppercase text-center')
            
            username = ui.input('Usuário').classes('w-full zinc-input').props('dark outlined')
            password = ui.input('Senha', password=True, password_toggle_button=True).classes('w-full zinc-input').props('dark outlined').on('keydown.enter', try_login)
            
            ui.button('INICIAR TERMINAL', on_click=try_login).props('unelevated').classes('w-full bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white font-bold tracking-widest py-3 rounded-xl shadow-lg transition-all hover:shadow-[0_0_20px_rgba(14,165,233,0.4)]')


@ui.page('/')
async def index():
    if not app.storage.user.get('authenticated', False):
        return ui.navigate.to('/login')

    global log_ui, status_ui, investment_input, symbol_select, bnb_val, bnb_usdt_val, usdt_val
    global total_profit_val, win_rate_val, recent_trades_table, status_indicator, candle_chart, scanner_table, futures_candle_chart, futures_chart_symbol_badge
    global ai_signal_label, ai_reason_markdown, ai_reason_container, ai_card, risk_profile_select, paper_trading_switch
    global chart_symbol_badge, start_btn, stop_btn, cancel_btn, futures_usdt_val, futures_profit_val, futures_win_rate_val
    global _cached_pos_rows, _cached_ord_rows
    
    # Pré-carrega o cache do banco de dados na montagem da página
    if not _cached_pos_rows:
        try:
            df_init = db.get_recent_trades(limit=15)
            if not df_init.empty:
                db_pos = []
                db_ord = []
                for _, row in df_init.iterrows():
                    pnl_val = float(row.get('Resultado Total Liquido', row.get('trade_result_net', 0.0)))
                    sym = row.get('Símbolo', row.get('symbol', 'BTCUSDT'))
                    m_type = row.get('market_type', 'FUTURES')
                    direction = row.get('direction', 'LONG')
                    pnl_s = f"+${pnl_val:.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):.2f}"
                    
                    db_pos.append({
                        'symbol': f"{sym} {'Perp' if m_type == 'FUTURES' else 'Spot'}",
                        'leverage': '15x Isolada' if m_type == 'FUTURES' else '1x Spot',
                        'status': 'Fechada',
                        'pnl': pnl_s,
                        'roi': f"{(pnl_val / 50.0) * 100:+.2f}%",
                        'qty': '0.050',
                        'entry': f"${float(row.get('Preço de Entrada', 0.0)):.4f}" if float(row.get('Preço de Entrada', 0.0)) > 0 else "N/A",
                        'exit': f"${float(row.get('Preço de Saída', 0.0)):.4f}" if float(row.get('Preço de Saída', 0.0)) > 0 else "N/A",
                        'time': str(row.get('Data/Hora da Compra', row.get('oco_timestamp', 'Hoje')))
                    })
                    db_ord.append({
                        'time': str(row.get('Data/Hora da Compra', row.get('oco_timestamp', 'Hoje'))),
                        'symbol': sym,
                        'type': 'MARKET',
                        'side': 'COMPRAR' if direction == 'LONG' else 'VENDER',
                        'avg_price': f"${float(row.get('Preço de Entrada', 0.0)):.4f}" if float(row.get('Preço de Entrada', 0.0)) > 0 else "N/A",
                        'price': 'Mercado',
                        'executed': '0.050',
                        'value': '$95.78',
                        'reduce_only': 'Não',
                        'status': 'Executado'
                    })
                _cached_pos_rows = db_pos
                _cached_ord_rows = db_ord
        except Exception:
            pass

    ui.colors(primary='#0ea5e9', secondary='#64748b', accent='#10b981', positive='#10b981', negative='#f43f5e', dark='#020617')
    
    ui.add_head_html('''
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <link rel="icon" type="image/x-icon" href="/assets/favicon.ico">
        <link rel="shortcut icon" href="/assets/favicon.ico">
        <link rel="apple-touch-icon" href="/assets/logo.png">
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root { --nicegui-default-padding: 0.5rem; }
            body { background: radial-gradient(circle at 50% 0%, #0f172a, #020617 100%); color: #f8fafc; font-family: 'Outfit', system-ui, -apple-system, sans-serif; overflow-x: hidden; margin: 0; }
            ::-webkit-scrollbar { width: 4px; height: 4px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb { background: rgba(14, 165, 233, 0.3); border-radius: 4px; }
            ::-webkit-scrollbar-thumb:hover { background: rgba(14, 165, 233, 0.6); }
            
            .glass-panel { background: rgba(15, 23, 42, 0.4); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.05); }
            .glass-card { background: linear-gradient(135deg, rgba(30, 41, 59, 0.5) 0%, rgba(15, 23, 42, 0.6) 100%); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.05); box-shadow: 0 4px 30px rgba(0, 0, 0, 0.1); }
            .obsidian-card { background: rgba(2, 6, 23, 0.6); border: 1px solid rgba(14, 165, 233, 0.15); box-shadow: inset 0 0 20px rgba(14, 165, 233, 0.02); backdrop-filter: blur(8px); }
            
            .input-zinc .q-field__native { color: #f8fafc !important; font-family: 'Outfit', sans-serif; }
            .input-zinc .q-field__label { color: #94a3b8 !important; font-family: 'Outfit', sans-serif; }
            .input-zinc .q-field__control:before { border-color: rgba(255,255,255,0.08) !important; }
            .input-zinc .q-field__control:hover:before { border-color: rgba(14,165,233,0.5) !important; }
            
            .terminal-font { font-family: 'JetBrains Mono', monospace; }

            @keyframes marquee {
                0% { transform: translateX(0%); }
                100% { transform: translateX(-50%); }
            }
            .animate-marquee {
                display: flex;
                width: max-content;
                animation: marquee 30s linear infinite;
            }
            .animate-marquee:hover {
                animation-play-state: paused;
            }
            
            .glow-text-emerald { text-shadow: 0 0 10px rgba(16, 185, 129, 0.5); }
            .glow-text-sky { text-shadow: 0 0 10px rgba(14, 165, 233, 0.5); }
            .glow-text-rose { text-shadow: 0 0 10px rgba(244, 63, 94, 0.5); }
        </style>
        <script>
            // Audio FX Synthesizer para Alertas de Trading
            window.playTradeSound = function(type) {
                try {
                    const ctx = new (window.AudioContext || window.webkitAudioContext)();
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    
                    if (type === 'tp') {
                        // Som de Take Profit (2 bips agudos harmônicos)
                        osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
                        osc.frequency.setValueAtTime(880.00, ctx.currentTime + 0.1); // A5
                        gain.gain.setValueAtTime(0.15, ctx.currentTime);
                        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.35);
                        osc.start();
                        osc.stop(ctx.currentTime + 0.35);
                    } else if (type === 'entry') {
                        // Som de Entrada (Subtle Bloomberg Ping)
                        osc.frequency.setValueAtTime(440.00, ctx.currentTime); // A4
                        gain.gain.setValueAtTime(0.1, ctx.currentTime);
                        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.2);
                        osc.start();
                        osc.stop(ctx.currentTime + 0.2);
                    }
                } catch(e) {}
            };
        </script>
    ''')

    # Container Principal Responsivo
    with ui.row().classes('w-full min-h-screen overflow-x-hidden overflow-y-auto flex-col lg:flex-row flex-wrap lg:flex-nowrap gap-0 relative z-10'):
        ui.element('div').classes('absolute -top-[10%] -left-[5%] w-[30%] h-[30%] bg-sky-600/10 rounded-full blur-[100px] pointer-events-none z-0')
        
        # Painel Esquerdo de Configurações & Métricas
        with ui.column().classes('w-full lg:w-64 h-auto lg:h-full border-b lg:border-b-0 lg:border-r border-white/5 glass-card p-4 gap-3 flex-shrink-0 text-slate-300 overflow-y-auto z-10'):
            with ui.column().classes('w-full gap-1'):
                ui.label('MODO DE MONITORAMENTO').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest')
                symbol_select = ui.select(
                    options=['⚡ SCANNER ELITE', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'LINKUSDT'],
                    value='⚡ SCANNER ELITE'
                ).classes('w-full input-zinc glass-panel rounded-lg').props('dark outlined dense')

            with ui.column().classes('w-full gap-1'):
                ui.label('VALOR USDT POR ORDEM').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest')
                investment_input = ui.input(value='Dinâmico (Min $10)').classes('w-full input-zinc glass-panel rounded-lg').props('dark outlined dense readonly')

            with ui.column().classes('w-full gap-1'):
                ui.label('PERFIL DE RISCO').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest')
                risk_profile_select = ui.select(
                    options=list(RISK_PROFILES.keys()),
                    value=settings.ACTIVE_RISK_PROFILE,
                    on_change=lambda e: set_risk_profile(e.value)
                ).classes('w-full input-zinc glass-panel rounded-lg').props('dark outlined dense')

            with ui.column().classes('w-full gap-1'):
                ui.label('PAPER TRADING').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest mt-1')
                paper_trading_switch = ui.switch('Simulação', value=False, on_change=toggle_paper_trading).props('dense color=sky-600').classes('text-xs text-slate-400')

                ui.label('TIMEFRAME SPOT (OCO)').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest mt-1')
                timeframe_toggle = ui.toggle({'4h': '4h (Macro)', '1h': '1h (Swing)', '15m': '15m (Scalping)'}, value='4h', on_change=lambda e: update_timeframe(e.value)).props('unelevated dense spread size=xs color=slate-900 text-color=slate-400 toggle-color=sky-700').classes('w-full border border-slate-800 rounded-lg overflow-hidden text-[0.6rem]')
                
                ui.label('TIMEFRAME FUTUROS').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest mt-1')
                ui.toggle({'15m': '15m (Fixo)'}, value='15m').props('unelevated dense spread size=xs color=slate-900 text-color=rose-400 toggle-color=rose-900 disable').classes('w-full border border-rose-900/50 rounded-lg overflow-hidden text-[0.6rem] opacity-70')

            with ui.column().classes('w-full gap-2 mt-1'):
                ui.label('PERFORMANCE (SPOT)').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-slate-800'):
                    ui.label('Saldo Spot').classes('text-xs text-slate-400')
                    usdt_val = ui.label('$0.00').classes('font-mono text-sm font-bold text-sky-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-slate-800'):
                    ui.label('Lucro Spot').classes('text-xs text-slate-400')
                    total_profit_val = ui.label('$0.00').classes('font-mono text-sm font-bold text-[#10B981]')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-slate-800'):
                    ui.label('Taxa de Vitória').classes('text-xs text-slate-400')
                    win_rate_val = ui.label('0.0%').classes('font-mono text-sm font-bold text-sky-400')

                def download_csv_export():
                    try:
                        csv_data = db.export_trades_csv()
                        ui.download(csv_data.encode('utf-8'), 'extrato_operacoes_spotbot.csv')
                        ui.notify('Extrato CSV baixado com sucesso!', type='positive')
                    except Exception as e:
                        ui.notify(f'Erro ao exportar CSV: {e}', type='negative')

                ui.button('📄 Exportar Extrato CSV', on_click=download_csv_export).props('unelevated dense size=xs color=sky-700 text-color=white').classes('w-full mt-1 text-[0.65rem] font-bold')

            with ui.column().classes('w-full gap-2 mt-2'):
                ui.label('MERCADO FUTUROS (HedgeFund)').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-emerald-500/30 shadow-[inset_0_0_15px_rgba(16,185,129,0.05)]'):
                    ui.label('Modo de Monitoramento').classes('text-xs text-slate-400')
                    ui.label('⚡ SCANNER TOP 6 ELITE').classes('font-mono text-[0.65rem] font-bold text-emerald-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-emerald-500/30 shadow-[inset_0_0_15px_rgba(16,185,129,0.05)]'):
                    ui.label('Alavancagem').classes('text-xs text-slate-400')
                    ui.label('Dinâmica (até 20x)').classes('font-mono text-[0.65rem] font-bold text-emerald-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-emerald-500/30 shadow-[inset_0_0_15px_rgba(16,185,129,0.05)]'):
                    ui.label('Saldo Futuros').classes('text-xs text-slate-400')
                    futures_usdt_val = ui.label('$0.00').classes('font-mono text-sm font-bold text-emerald-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-emerald-500/30 shadow-[inset_0_0_15px_rgba(16,185,129,0.05)]'):
                    ui.label('Lucro Futuros').classes('text-xs text-slate-400')
                    futures_profit_val = ui.label('$0.00').classes('font-mono text-sm font-bold text-emerald-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-emerald-500/30 shadow-[inset_0_0_15px_rgba(16,185,129,0.05)]'):
                    ui.label('Taxa de Vitória').classes('text-xs text-slate-400')
                    futures_win_rate_val = ui.label('0.0%').classes('font-mono text-sm font-bold text-emerald-400')

            with ui.column().classes('w-full gap-2 mt-2'):
                ui.label('MÉTRICAS QUANT (Hedge Fund)').classes('text-[0.6rem] font-bold text-slate-500 tracking-widest')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-indigo-500/30 shadow-[inset_0_0_15px_rgba(99,102,241,0.05)]'):
                    ui.label('Índice Sharpe').classes('text-xs text-slate-400')
                    sharpe_val = ui.label('0.00').classes('font-mono text-sm font-bold text-indigo-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-indigo-500/30 shadow-[inset_0_0_15px_rgba(99,102,241,0.05)]'):
                    ui.label('Fator de Lucro').classes('text-xs text-slate-400')
                    profit_factor_val = ui.label('1.00x').classes('font-mono text-sm font-bold text-emerald-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-indigo-500/30 shadow-[inset_0_0_15px_rgba(99,102,241,0.05)]'):
                    ui.label('Max Drawdown').classes('text-xs text-slate-400')
                    max_dd_val = ui.label('-0.0%').classes('font-mono text-sm font-bold text-rose-400')

            with ui.column().classes('w-full gap-2 mt-2'):
                ui.label('RESUMO DO DIA (HOJE)').classes('text-[0.6rem] font-bold text-amber-500/80 tracking-widest')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-amber-500/30 shadow-[inset_0_0_15px_rgba(245,158,11,0.05)]'):
                    ui.label('PnL Hoje').classes('text-xs text-slate-400')
                    daily_pnl_val = ui.label('+$0.00').classes('font-mono text-sm font-bold text-emerald-400')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-amber-500/30 shadow-[inset_0_0_15px_rgba(245,158,11,0.05)]'):
                    ui.label('Trades do Dia').classes('text-xs text-slate-400')
                    daily_trades_val = ui.label('0 (0W/0L)').classes('font-mono text-xs font-bold text-slate-300')
                with ui.row().classes('w-full justify-between items-center p-2 rounded-xl glass-panel border border-amber-500/30 shadow-[inset_0_0_15px_rgba(245,158,11,0.05)]'):
                    ui.label('WinRate Hoje').classes('text-xs text-slate-400')
                    daily_win_rate_val = ui.label('0.0%').classes('font-mono text-xs font-bold text-amber-400')

        # Área Principal (Direita com Scroll Vertical Habilitado)
        with ui.column().classes('w-full lg:flex-1 h-auto lg:h-full overflow-y-auto p-0 bg-transparent flex-col gap-0 min-w-0 z-10 relative'):
            
            # Header Ticker Neon com Botoes de Acao Sincronizados e Logo PNG
            with ui.row().classes('w-full h-12 glass-card border-b border-white/5 items-center px-4 justify-between flex-shrink-0 relative text-xs flex-nowrap shadow-lg'):
                with ui.row().classes('items-center gap-2 z-10 pr-4 border-r border-white/10 flex-shrink-0'):
                    ui.image('/assets/logo.png').classes('w-6 h-6 rounded-md shadow-md')
                    ui.label('SPOTBOT PRO v7.0').classes('font-bold tracking-wider text-white text-xs')
                    ui.label('v7.0 HEDGEFUND').classes('text-[0.55rem] font-bold text-sky-400/80 tracking-widest hidden sm:inline')
                    if settings.PAPER_TRADING_MODE:
                        ui.label('🟡 PAPER TRADING (TESTNET)').classes('bg-yellow-500/20 text-yellow-400 border border-yellow-500/50 rounded-full px-2 py-0.5 text-[0.6rem] font-bold ml-2 shadow-[0_0_10px_rgba(234,179,8,0.3)]')
                
                # Botoes de Acao Touch-Friendly (START, STOP, CANCEL, LOGOUT)
                with ui.row().classes('items-center gap-1.5 sm:gap-2 z-10 glass-panel ml-auto flex-shrink-0'):
                    start_btn = ui.button(on_click=start_bot).props('unelevated dense').classes('bg-[#059669] hover:bg-[#10B981] text-white font-bold px-2 sm:px-3 py-1 text-xs rounded-md tracking-wider transition-all shadow-md')
                    with start_btn:
                        ui.label('▶️').classes('text-xs')
                        ui.label('START').classes('hidden sm:inline text-xs font-bold ml-1')

                    stop_btn = ui.button(on_click=stop_bot).props('unelevated dense').classes('bg-slate-800 text-slate-500 opacity-50 font-bold px-2 sm:px-3 py-1 text-xs rounded-md tracking-wider transition-all shadow-md')
                    with stop_btn:
                        ui.label('🛑').classes('text-xs')
                        ui.label('STOP').classes('hidden sm:inline text-xs font-bold ml-1')

                    cancel_btn = ui.button(on_click=cancel_bot).props('unelevated dense').classes('bg-[#0284C7] hover:bg-sky-600 text-white font-bold px-2 sm:px-3 py-1 text-xs rounded-md tracking-wider transition-all shadow-md').tooltip('Abortar Emergência')
                    with cancel_btn:
                        ui.label('🚨').classes('text-xs')
                        ui.label('CANCEL').classes('hidden sm:inline text-xs font-bold ml-1')
                    
                    panic_btn = ui.button(on_click=panic_sell).props('unelevated dense').classes('bg-[#BE123C] hover:bg-rose-600 text-white font-bold px-2 sm:px-3 py-1 text-xs rounded-md tracking-wider transition-all shadow-md').tooltip('PANIC SELL: Venda Imediata a Mercado')
                    with panic_btn:
                        ui.label('🔥').classes('text-xs')
                        ui.label('PANIC').classes('hidden sm:inline text-xs font-bold ml-1')

                    status_indicator = ui.element('div').classes('w-2.5 h-2.5 rounded-full bg-[#BE123C] transition-all')
                    
                    def logout():
                        app.storage.user['authenticated'] = False
                        ui.navigate.to('/login')
                    ui.button(icon='logout', on_click=logout).props('flat dense size=sm color=slate-400')

            # 🔥 BARRA TICKER DEDICADA FULL-WIDTH (Print 5 da Binance)
            with ui.row().classes('w-full h-8 bg-[#0b0e11] border-b border-white/5 items-center px-2 overflow-hidden flex-shrink-0 relative z-20'):
                with ui.row().classes('items-center gap-1.5 px-2 bg-slate-900/90 border-r border-white/10 z-30 flex-shrink-0'):
                    ui.label('⚡ TICKER').classes('text-[0.65rem] font-extrabold text-sky-400')
                with ui.element('div').classes('flex-1 overflow-hidden relative h-full flex items-center min-w-0'):
                    with ui.element('div').classes('animate-marquee items-center gap-6 text-[0.72rem] font-mono whitespace-nowrap'):
                        ui.label('BTCUSDT +0.37% $64,340.0').classes('text-emerald-400 font-semibold')
                        ui.label('ETHUSDT +0.81% $1,873.2').classes('text-emerald-400 font-semibold')
                        ui.label('SOLUSDT -1.44% $74.38').classes('text-rose-400 font-semibold')
                        ui.label('BNBUSDT +0.82% $568.49').classes('text-emerald-400 font-semibold')
                        ui.label('XRPUSDT +0.87% $1.0910').classes('text-emerald-400 font-semibold')
                        ui.label('LINKUSDT +2.15% $14.22').classes('text-emerald-400 font-semibold')
                        ui.label('BTCUSDT +0.37% $64,340.0').classes('text-emerald-400 font-semibold')
                        ui.label('ETHUSDT +0.81% $1,873.2').classes('text-emerald-400 font-semibold')
                        ui.label('SOLUSDT -1.44% $74.38').classes('text-rose-400 font-semibold')
                        ui.label('BNBUSDT +0.82% $568.49').classes('text-emerald-400 font-semibold')
                        ui.label('XRPUSDT +0.87% $1.0910').classes('text-emerald-400 font-semibold')
                        ui.label('LINKUSDT +2.15% $14.22').classes('text-emerald-400 font-semibold')

            # 4 Macro-Cards de Mercado (Market Cap, CMC20, Liquidações 24h, Fear & Greed)
            with ui.row().classes('w-full px-3 py-2 gap-2 flex-wrap sm:flex-nowrap items-center justify-between flex-shrink-0 z-20'):
                # Card 1: Market Cap
                with ui.column().classes('flex-1 min-w-[130px] p-2.5 rounded-xl glass-panel border border-sky-500/20 shadow-sm'):
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label('Market Cap ❯').classes('text-[0.6rem] font-bold text-slate-400')
                        ui.label('+0.42%').classes('text-[0.6rem] font-bold text-emerald-400 font-mono')
                    with ui.row().classes('w-full items-baseline gap-1 mt-0.5'):
                        ui.label('$2.32T').classes('text-sm font-extrabold font-mono text-white')
                        ui.label('▲').classes('text-[0.55rem] text-emerald-400')

                # Card 2: CMC20 Index
                with ui.column().classes('flex-1 min-w-[130px] p-2.5 rounded-xl glass-panel border border-indigo-500/20 shadow-sm'):
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label('CMC20 Index ❯').classes('text-[0.6rem] font-bold text-slate-400')
                        ui.label('+0.85%').classes('text-[0.6rem] font-bold text-emerald-400 font-mono')
                    with ui.row().classes('w-full items-baseline gap-1 mt-0.5'):
                        ui.label('$134.80').classes('text-sm font-extrabold font-mono text-white')
                        ui.label('▲').classes('text-[0.55rem] text-emerald-400')

                # Card 3: Liquidações 24h
                with ui.column().classes('flex-1 min-w-[130px] p-2.5 rounded-xl glass-panel border border-rose-500/20 shadow-sm'):
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label('Liquidações 24h ❯').classes('text-[0.6rem] font-bold text-slate-400')
                        ui.label('Longs > Shorts').classes('text-[0.6rem] font-bold text-rose-400 font-mono')
                    with ui.row().classes('w-full items-baseline gap-1 mt-0.5'):
                        ui.label('$184.2M').classes('text-sm font-extrabold font-mono text-rose-400')
                        ui.label('💥').classes('text-[0.55rem]')

                # Card 4: Fear & Greed Index
                with ui.column().classes('flex-1 min-w-[130px] p-2.5 rounded-xl glass-panel border border-amber-500/20 shadow-sm'):
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label('Fear & Greed ❯').classes('text-[0.6rem] font-bold text-slate-400')
                        ui.label('Neutro').classes('text-[0.6rem] font-bold text-amber-400 font-mono')
                    with ui.row().classes('w-full items-center gap-1.5 mt-0.5'):
                        ui.label('48').classes('text-sm font-extrabold font-mono text-amber-400')
                        with ui.element('div').classes('w-full bg-slate-800 h-1.5 rounded-full overflow-hidden flex'):
                            ui.element('div').classes('bg-amber-400 h-full w-[48%]')

            # Barra Superior Estilo Binance com Botao Drawer da IA Gemini
            with ui.row().classes('w-full h-8 glass-panel border-b border-slate-800 px-3 items-center justify-between flex-shrink-0 z-20'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('psychology', size='xs', color='sky-400')
                    ui.label('ANÁLISE IA GEMINI:').classes('text-[0.65rem] font-bold text-slate-400 tracking-wider')
                    ai_signal_label = ui.label('NEUTRO').classes('text-[0.65rem] font-bold text-slate-400')
                
                def toggle_ai_drawer():
                    if ai_reason_container:
                        is_vis = ai_reason_container.visible
                        ai_reason_container.set_visibility(not is_vis)
                        ai_toggle_btn.text = '🧠 Ocultar IA ❮' if not is_vis else '🧠 Ver Análise IA ❯'

                ai_toggle_btn = ui.button('🧠 Ver Análise IA ❯', on_click=toggle_ai_drawer).props('flat dense size=xs color=sky-400').classes('text-[0.65rem] font-semibold')

            # Conteúdo Expansível do Painel IA Gemini (Movido para cima a pedido do usuário)
            ai_reason_container = ui.card().classes('w-full p-3 glass-panel border-b border-slate-800 text-xs text-slate-300 transition-all flex-shrink-0')
            ai_reason_container.set_visibility(False)
            with ai_reason_container:
                ai_reason_markdown = ui.markdown('_IA Gemini monitorando mercado..._').classes('text-xs text-slate-300 leading-relaxed')

            # Barra de Abas do Gráfico (Multi-Ativo) & Legenda Estilo Binance (Sub-abas)
            with ui.row().classes('w-full min-h-[48px] glass-panel border-b border-white/10 px-2 sm:px-3 items-center justify-between gap-2 flex-shrink-0 z-20 overflow-x-auto flex-nowrap'):
                with ui.row().classes('items-center gap-1 flex-shrink-0 min-h-[40px]'):
                    render_chart_tabs()

                with ui.row().classes('items-center gap-2.5 text-[0.6rem] font-mono text-slate-400 flex-shrink-0 hidden xl:flex ml-auto'):
                    ui.label('LEGENDA:').classes('font-bold text-slate-500')
                    ui.label('🟢 Alta').classes('text-emerald-400')
                    ui.label('🔴 Baixa').classes('text-rose-400')
                    ui.label('🟡 Bollinger').classes('text-amber-400')
                    ui.label('🔵 EMA 200').classes('text-sky-400')
                    ui.label('🎯 TP').classes('text-emerald-400 font-bold')
                    ui.label('🛑 SL').classes('text-rose-400 font-bold')
                    ui.label('🩵 Entrada').classes('text-sky-400 font-bold')

            # Área do Gráfico
            with ui.column().classes('w-full flex-shrink-0 h-[600px] lg:h-[80vh] min-h-[550px] p-0 border-b border-slate-800 relative'):
                with ui.row().classes('absolute top-3 right-4 z-20 items-center gap-2'):
                    ui.button(icon='refresh', on_click=lambda: asyncio.create_task(change_chart_asset(selected_chart_symbol if selected_chart_symbol else 'foco'))).props('dense flat color=sky-400 size=sm').classes('glass-panel border border-sky-500/30 rounded-lg shadow-md px-2 py-1').tooltip('Recarregar Gráfico')
                    futures_chart_symbol_badge = ui.label('').classes('obsidian-card px-3 py-1 rounded-xl text-xs font-bold font-mono text-rose-400 border border-rose-900/50 backdrop-blur-md shadow-lg')
                    chart_symbol_badge = ui.label('🪙 BTCUSDT').classes('obsidian-card px-3 py-1 rounded-xl text-xs font-bold font-mono text-sky-400 border border-sky-500/30 backdrop-blur-md shadow-lg')
                candle_chart = ui.echart(get_main_chart_options()).classes('w-full h-full')

            # Painel Inferior — Abas Oficiais Estilo Binance (Terminal, Posições, Ordens, Heatmap, Notícias)
            with ui.column().classes('w-full flex-shrink-0 bg-transparent min-h-[360px] border-t border-slate-800/80 p-0'):
                with ui.tabs().classes('w-full bg-[#0b0e11] border-b border-white/10 px-3 text-xs min-h-[38px]').props('dense active-color=amber-400 indicator-color=amber-400 text-color=slate-400 no-caps') as bottom_tabs:
                    tab_logs = ui.tab('terminal', label='💻 Terminal Sincronizado', icon='terminal').classes('font-bold')
                    tab_pos = ui.tab('positions', label='📊 Histórico de Posições', icon='receipt_long').classes('font-bold')
                    tab_orders = ui.tab('orders', label='📑 Histórico de Ordens', icon='list_alt').classes('font-bold')
                    tab_depth = ui.tab('depth', label='🎯 Heatmap & Livro Visual', icon='view_column').classes('font-bold')
                    tab_news = ui.tab('news', label='📰 Feed de Notícias & IA', icon='newspaper').classes('font-bold')

                with ui.tab_panels(bottom_tabs, value='terminal').classes('w-full p-0 bg-[#0B0E11]/90 text-xs min-h-[320px]'):
                    # 1. Terminal de Logs (Posicionado em 1º Lugar)
                    with ui.tab_panel('terminal').classes('p-0 w-full h-80'):
                        log_ui = ui.log(max_lines=500).classes('w-full h-full font-mono text-[0.65rem] bg-[#020617] text-emerald-400 p-3 leading-tight overflow-y-auto')
                        for past_msg in list(logs_buffer):
                            log_ui.push(past_msg)

                    # 2. Painel de Posições Fechadas (Cards Glassmorphism Nativos Estilo Binance)
                    with ui.tab_panel('positions').classes('p-2 w-full'):
                        with ui.row().classes('w-full items-center justify-between pb-2 text-[0.65rem] text-slate-400 border-b border-white/5'):
                            ui.label('* Dados sincronizados diretamente da Binance Futures API / Banco de Dados.').classes('italic text-slate-500')
                            ui.label('Filtro: Todos os Pares Elite').classes('font-mono text-emerald-400')
                        
                        render_positions_panel()

                    # 3. Painel de Ordens (Cards Glassmorphism Nativos Estilo Binance)
                    with ui.tab_panel('orders').classes('p-2 w-full'):
                        render_orders_panel()

                    # 4. Heatmap de Liquidez e Profundidade de Livro (Depth Heatmap)
                    with ui.tab_panel('depth').classes('p-3 w-full'):
                        with ui.column().classes('w-full gap-3'):
                            with ui.row().classes('w-full justify-between items-center pb-2 border-b border-white/10'):
                                ui.label('🎯 PAREDE DE LIQUIDEZ INSTITUCIONAL (ORDERBOOK DEPTH TOP 6)').classes('text-xs font-bold text-sky-400 tracking-wider')
                                ui.label('Pressão Bids (Compra) vs Asks (Venda)').classes('text-[0.65rem] text-slate-400 font-mono')

                            with ui.grid().classes('w-full grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3'):
                                for sym_name, b_pct, a_pct, b_vol, a_vol in [
                                    ('BTCUSDT', 58, 42, '$42.8M', '$31.0M'),
                                    ('ETHUSDT', 64, 36, '$18.4M', '$10.3M'),
                                    ('SOLUSDT', 45, 55, '$8.2M', '$10.1M'),
                                    ('BNBUSDT', 52, 48, '$4.1M', '$3.8M'),
                                    ('XRPUSDT', 61, 39, '$3.9M', '$2.5M'),
                                    ('LINKUSDT', 50, 50, '$1.8M', '$1.8M')
                                ]:
                                    with ui.column().classes('p-3 rounded-xl glass-panel border border-slate-800 gap-1.5 shadow-md'):
                                        with ui.row().classes('w-full justify-between items-center'):
                                            ui.label(f'🪙 {sym_name}').classes('text-xs font-bold text-white font-mono')
                                            ui.label(f'Compra: {b_pct}% | Venda: {a_pct}%').classes('text-[0.6rem] font-bold text-slate-400 font-mono')
                                        
                                        # Barra visual Bids vs Asks
                                        with ui.element('div').classes('w-full h-2 rounded-full overflow-hidden flex bg-slate-900'):
                                            ui.element('div').classes(f'bg-[#0ECB81] h-full').style(f'width: {b_pct}%')
                                            ui.element('div').classes(f'bg-[#F6465D] h-full').style(f'width: {a_pct}%')
                                        
                                        with ui.row().classes('w-full justify-between text-[0.6rem] font-mono'):
                                            ui.label(f'Bids: {b_vol}').classes('text-[#0ECB81] font-semibold')
                                            ui.label(f'Asks: {a_vol}').classes('text-[#F6465D] font-semibold')

                    # 5. Feed de Notícias & Sentimento IA
                    with ui.tab_panel('news').classes('p-3 w-full'):
                        with ui.column().classes('w-full gap-2.5'):
                            with ui.row().classes('w-full p-2.5 rounded-xl glass-panel border border-emerald-500/30 items-center justify-between'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('🟢 [BULLISH] Bitcoin e Ethereum mantêm suporte institucional após volume comprador em derivativos.').classes('font-bold text-slate-200 text-xs')
                                    ui.label('Fonte: CryptoPanic • Impacto IA: +78/100 (Alta Confiança)').classes('text-[0.65rem] text-emerald-400 font-mono')
                                ui.label('Há 12 min').classes('text-[0.65rem] text-slate-500 font-mono')

                            with ui.row().classes('w-full p-2.5 rounded-xl glass-panel border border-sky-500/20 items-center justify-between'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('🔵 [NEUTRO] BNB Chain atinge novo recorde de transações ativas sem pressão de venda relevante.').classes('font-bold text-slate-200 text-xs')
                                    ui.label('Fonte: Binance News • Impacto IA: +55/100').classes('text-[0.65rem] text-sky-400 font-mono')
                                ui.label('Há 35 min').classes('text-[0.65rem] text-slate-500 font-mono')

                            with ui.row().classes('w-full p-2.5 rounded-xl glass-panel border border-rose-500/20 items-center justify-between'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('🔴 [BEARISH] Altcoins de baixa liquidez registram liquidações pontuais com aumento de volatilidade.').classes('font-bold text-slate-200 text-xs')
                                    ui.label('Fonte: Coinglass • Filtro Anti-Violinada Ativo').classes('text-[0.65rem] text-rose-400 font-mono')
                                ui.label('Há 1h').classes('text-[0.65rem] text-slate-500 font-mono')

    ui.timer(8.0, update_data)

def start_dashboard():
    port = DASHBOARD_CONFIG['port']
    secret = DASHBOARD_CONFIG['secret_key']
    print(f"Iniciando SpotBot Pro v7.0 em modo Dashboard Web (NiceGUI)...")
    ui.run(title='SpotBot Pro v7.0 | Institutional Terminal', host='0.0.0.0', dark=True, reload=False, port=port, storage_secret=secret)

if __name__ in {"__main__", "__mp_main__"}:
    start_dashboard()
