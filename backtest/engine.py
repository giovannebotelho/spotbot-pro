import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'backtest/backtest_data.db'

def load_data(symbol='BTCUSDT', interval='15m'):
    conn = sqlite3.connect(DB_PATH)
    table_name = f"{symbol}_{interval}"
    query = f"SELECT * FROM {table_name} ORDER BY open_time ASC"
    df = pd.read_sql(query, conn)
    conn.close()
    
    df['open_time'] = pd.to_datetime(df['open_time'])
    df['close_time'] = pd.to_datetime(df['close_time'])
    df.set_index('open_time', inplace=True)
    return df

def calculate_indicators(df, df_1h=None):
    # ATR (Average True Range)
    df['high_low'] = df['high'] - df['low']
    df['high_close'] = np.abs(df['high'] - df['close'].shift())
    df['low_close'] = np.abs(df['low'] - df['close'].shift())
    ranges = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = ranges.rolling(window=14).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD & Histogram
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    sig = macd.ewm(span=9, adjust=False).mean()
    df['macd_hist'] = macd - sig
    
    # 1H MTF
    if df_1h is not None:
        df_1h['ema20_1h'] = df_1h['close'].ewm(span=20, adjust=False).mean()
        # Shift 1 para garantir que só olhamos o candle de 1h fechado
        df_1h_shifted = df_1h[['ema20_1h', 'close']].shift(1)
        df_1h_shifted.rename(columns={'close': 'close_1h'}, inplace=True)
        df = pd.merge_asof(df, df_1h_shifted, left_index=True, right_index=True, direction='backward')

    return df

class Backtester:
    def __init__(self, df, initial_balance=100.0, risk_per_trade=0.02, 
                 sl_multiplier=1.5, tp_multiplier=1.0, max_roi=0.10, trailing_rate=0.015):
        self.df = df
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.sl_multiplier = sl_multiplier
        self.tp_multiplier = tp_multiplier
        self.max_roi = max_roi
        self.trailing_rate = trailing_rate
        
        self.equity_curve = []
        self.trades = []
        
    def run(self):
        position = None
        entry_price = 0
        qty = 0
        sl_price = 0
        hard_tp_price = 0
        
        # Variáveis de Trailing Stop
        highest_price = 0
        lowest_price = float('inf')
        
        # Variáveis pra sinal
        prev_macd_hist = 0
        
        for index, row in self.df.iterrows():
            if pd.isna(row['atr']) or pd.isna(row['rsi']):
                continue
                
            cur_price = row['close']
            cur_high = row['high']
            cur_low = row['low']
            
            # --- EXIT LOGIC ---
            if position == 'LONG':
                highest_price = max(highest_price, cur_high)
                trailing_stop = highest_price * (1 - self.trailing_rate)
                
                # Checa SL, TP ou Trailing Stop
                if cur_low <= sl_price:
                    loss = (sl_price - entry_price) * qty
                    self.balance += loss
                    self.trades.append({'time': index, 'type': 'SL', 'pnl': loss})
                    position = None
                elif cur_low <= trailing_stop and trailing_stop > entry_price:
                    # Trailing stop atingido no lucro
                    profit = (trailing_stop - entry_price) * qty
                    self.balance += profit
                    self.trades.append({'time': index, 'type': 'TRAILING', 'pnl': profit})
                    position = None
                elif cur_high >= hard_tp_price:
                    profit = (hard_tp_price - entry_price) * qty
                    self.balance += profit
                    self.trades.append({'time': index, 'type': 'TP', 'pnl': profit})
                    position = None
                    
            elif position == 'SHORT':
                lowest_price = min(lowest_price, cur_low)
                trailing_stop = lowest_price * (1 + self.trailing_rate)
                
                if cur_high >= sl_price:
                    loss = (entry_price - sl_price) * qty
                    self.balance += loss
                    self.trades.append({'time': index, 'type': 'SL', 'pnl': loss})
                    position = None
                elif cur_high >= trailing_stop and trailing_stop < entry_price:
                    profit = (entry_price - trailing_stop) * qty
                    self.balance += profit
                    self.trades.append({'time': index, 'type': 'TRAILING', 'pnl': profit})
                    position = None
                elif cur_low <= hard_tp_price:
                    profit = (entry_price - hard_tp_price) * qty
                    self.balance += profit
                    self.trades.append({'time': index, 'type': 'TP', 'pnl': profit})
                    position = None

            # --- ENTRY LOGIC ---
            if position is None:
                # MTF Filter
                mtf_bullish = row.get('close_1h', 0) > row.get('ema20_1h', 999999)
                mtf_bearish = row.get('close_1h', 0) < row.get('ema20_1h', 0)
                
                # RSI & MACD Exhaustion
                macd_hist = row['macd_hist']
                
                # Bullish: MACD hist negativo perdendo força (subindo) + RSI sobrevendido
                bullish_signal = (row['rsi'] < 40) and (macd_hist > prev_macd_hist) and (prev_macd_hist < 0)
                # Bearish: MACD hist positivo perdendo força (caindo) + RSI sobrecomprado
                bearish_signal = (row['rsi'] > 60) and (macd_hist < prev_macd_hist) and (prev_macd_hist > 0)
                
                if bullish_signal and mtf_bullish:
                    position = 'LONG'
                    entry_price = cur_price
                    highest_price = entry_price
                    
                    sl_dist = min(row['atr'] * self.sl_multiplier, cur_price * self.max_roi)
                    sl_price = entry_price - sl_dist
                    
                    hard_tp_price = entry_price + (row['atr'] * self.tp_multiplier)
                    
                    # Position Sizing: Risk = Balance * risk_per_trade
                    risk_amount = self.balance * self.risk_per_trade
                    qty = risk_amount / sl_dist if sl_dist > 0 else 0
                    
                elif bearish_signal and mtf_bearish:
                    position = 'SHORT'
                    entry_price = cur_price
                    lowest_price = entry_price
                    
                    sl_dist = min(row['atr'] * self.sl_multiplier, cur_price * self.max_roi)
                    sl_price = entry_price + sl_dist
                    
                    hard_tp_price = entry_price - (row['atr'] * self.tp_multiplier)
                    
                    risk_amount = self.balance * self.risk_per_trade
                    qty = risk_amount / sl_dist if sl_dist > 0 else 0
                    
            prev_macd_hist = row['macd_hist']
            self.equity_curve.append({'time': index, 'equity': self.balance})
            
        return pd.DataFrame(self.equity_curve), self.trades

if __name__ == '__main__':
    df_15m = load_data('BTCUSDT', '15m')
    df_1h = load_data('BTCUSDT', '1h')
    df_15m = calculate_indicators(df_15m, df_1h)
    
    tester = Backtester(df_15m)
    equity_df, trades = tester.run()
    
    print(f"Saldo Final: ${equity_df['equity'].iloc[-1]:.2f}")
    win_trades = [t for t in trades if t['pnl'] > 0]
    win_rate = len(win_trades) / len(trades) if trades else 0
    print(f"Win Rate: {win_rate*100:.2f}% | Total Trades: {len(trades)}")
