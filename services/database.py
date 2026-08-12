import sqlite3
import json
import pandas as pd
import warnings
import datetime as dt_module
from datetime import datetime
from pathlib import Path
from config.settings import DATABASE_URL, BASE_DIR, TIMEZONE
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore", category=UserWarning)

_stats_cache = None
_last_stats_time = 0

_pg_pool = None

class DatabaseManager:
    def __init__(self, db_url=None):
        self.db_url = db_url or DATABASE_URL
        self.is_postgres = self.db_url.startswith("postgresql://") or self.db_url.startswith("postgres://")
        self.sqlite_conn = None

    def get_connection(self):
        global _pg_pool
        if self.is_postgres:
            if _pg_pool is None:
                import psycopg2
                from psycopg2.pool import ThreadedConnectionPool
                from psycopg2.extras import RealDictCursor
                _pg_pool = ThreadedConnectionPool(1, 10, self.db_url, cursor_factory=RealDictCursor)
            return _pg_pool.getconn()
        else:
            if self.sqlite_conn is None:
                if self.db_url.startswith("sqlite:///"):
                    db_path = Path(self.db_url.replace("sqlite:///", ""))
                else:
                    db_path = BASE_DIR / "spotbot.db"
                self.sqlite_conn = sqlite3.connect(db_path, check_same_thread=False)
                self.sqlite_conn.row_factory = sqlite3.Row
            return self.sqlite_conn

    def release_connection(self, conn):
        global _pg_pool
        if self.is_postgres and _pg_pool is not None and conn is not None:
            _pg_pool.putconn(conn)

    def create_tables(self):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            pk_sql = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
            
            create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS trades (
                id {pk_sql},
                order_index INTEGER,
                initial_balance_usdt REAL,
                initial_invested_usdt REAL,
                symbol TEXT,
                quantity REAL,
                buy_price REAL,
                vwap REAL,
                ema_7 REAL,
                ema_15 REAL,
                ema_25 REAL,
                ema_50 REAL,
                ema_100 REAL,
                ema_200 REAL,
                buy_timestamp TEXT,
                profit_target REAL,
                stop_loss REAL,
                stop_limit REAL,
                oco_result TEXT,
                oco_timestamp TEXT,
                trade_result REAL,
                fee REAL,
                trade_result_net REAL,
                total_result_gross REAL,
                total_result_net REAL,
                final_balance_usdt REAL,
                bnb_balance_usdt REAL,
                rsi REAL,
                condition_met TEXT,
                time_interval TEXT,
                candle_open REAL,
                candle_high REAL,
                candle_low REAL,
                candle_close REAL,
                candle_variation REAL,
                candle_amplitude REAL,
                variation_24h REAL,
                candle_volume REAL,
                candle_patterns TEXT,
                macd REAL,
                signal_line REAL,
                bb_lower REAL,
                bb_middle REAL,
                bb_upper REAL,
                trend_up TEXT,
                gemini_response TEXT,
                confluence_score REAL,
                slippage REAL,
                initial_stop_loss REAL,
                dca_levels INTEGER,
                bot_version TEXT,
                raw_data TEXT,
                market_type TEXT,
                leverage INTEGER,
                margin_type TEXT,
                direction TEXT
            )
            """
            cursor.execute(create_table_sql)
            
            events_table_sql = f"""
            CREATE TABLE IF NOT EXISTS trade_events (
                id {pk_sql},
                symbol TEXT,
                event_type TEXT,
                message TEXT,
                created_at TEXT
            )
            """
            cursor.execute(events_table_sql)
            
            # Migrações seguras (adiciona colunas em bancos existentes)
            new_columns = {
                'confluence_score': 'REAL',
                'slippage': 'REAL',
                'initial_stop_loss': 'REAL',
                'dca_levels': 'INTEGER',
                'bot_version': 'TEXT',
                'raw_data': 'TEXT',
                'market_type': 'TEXT',
                'leverage': 'INTEGER',
                'margin_type': 'TEXT',
                'direction': 'TEXT'
            }
            
            for col_name, col_type in new_columns.items():
                if self.is_postgres:
                    try:
                        cursor.execute(f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
                    except Exception:
                        pass
                else:
                    try:
                        cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass
            
            conn.commit()
        except Exception as e:
            print(f"⚠️ Erro ao criar tabelas no banco de dados: {e}")
        finally:
            self.release_connection(conn)

    def add_trade(self, data_row):
        global _stats_cache
        _stats_cache = None
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            def get_val(key, default=None):
                return data_row.get(key, default)

            placeholder = "%s" if self.is_postgres else "?"
            placeholders = ", ".join([placeholder] * 55)

            sql = f"""
            INSERT INTO trades (
                order_index, initial_balance_usdt, initial_invested_usdt, symbol, quantity, buy_price,
                vwap, ema_7, ema_15, ema_25, ema_50, ema_100, ema_200,
                buy_timestamp, profit_target, stop_loss, stop_limit, oco_result, oco_timestamp,
                trade_result, fee, trade_result_net, total_result_gross, total_result_net,
                final_balance_usdt, bnb_balance_usdt, rsi, condition_met, time_interval,
                candle_open, candle_high, candle_low, candle_close, candle_variation, candle_amplitude,
                variation_24h, candle_volume, candle_patterns, macd, signal_line,
                bb_lower, bb_middle, bb_upper, trend_up, gemini_response, 
                confluence_score, slippage, initial_stop_loss, dca_levels, bot_version, raw_data,
                market_type, leverage, margin_type, direction
            ) VALUES ({placeholders})
            """
            
            values = (
                get_val("Índice da Ordem"),
                get_val("Saldo Inicial em USDT"),
                get_val("USDT Inicial Investido"),
                get_val("Símbolo"),
                get_val("Quantidade de Moeda"),
                get_val("Preço de Compra"),
                get_val("VWAP"),
                get_val("EMA 7"),
                get_val("EMA 15"),
                get_val("EMA 25"),
                get_val("EMA 50"),
                get_val("EMA 100"),
                get_val("EMA 200"),
                get_val("Data/Hora da Compra"),
                get_val("Meta de Lucro OCO"),
                get_val("Stop Loss OCO"),
                get_val("Limite de Stop OCO"),
                get_val("Resultado da Ordem OCO"),
                get_val("Data/Hora OCO"),
                get_val("Resultado Parcial da Transação"),
                get_val("Taxa"),
                get_val("Resultado Parcial da Transação Líquido"),
                get_val("Resultado Total Bruto"),
                get_val("Resultado Total Liquido"),
                get_val("Saldo Final em USDT"),
                get_val("Saldo BNB em USDT"),
                get_val("RSI da operação"),
                get_val("Condição Atendida"),
                get_val("Intervalo de tempo (Candles)"),
                get_val("Preço de Abertura (Candle)"),
                get_val("Preço Máximo (Candle)"),
                get_val("Preço Mínimo (Candle)"),
                get_val("Preço de Fechamento (Candle)"),
                get_val("Variação (Candle)"),
                get_val("Amplitude (Candle)"),
                get_val("Variação (24h)"),
                get_val("Volume (Candle)"),
                json.dumps(get_val("Padrões de Candle", {})),
                get_val("MACD"),
                get_val("Linha de Sinal"),
                get_val("Banda Inferior BB"),
                get_val("Banda Média BB"),
                get_val("Banda Superior BB"),
                str(get_val("Tendência de Alta")),
                get_val("Resposta do Gemini"),
                get_val("confluence_score", 0.0),
                get_val("slippage", 0.0),
                get_val("initial_stop_loss", 0.0),
                get_val("dca_levels", 0),
                get_val("bot_version", "v7.0"),
                json.dumps(data_row, default=str),
                get_val("market_type", "SPOT"),
                get_val("leverage", 1),
                get_val("margin_type", "ISOLATED"),
                get_val("direction", "LONG")
            )
            
            cursor.execute(sql, values)
            conn.commit()
        except Exception as e:
            print(f"⚠️ Erro no INSERT em add_trade: {e}")
            raise e
        finally:
            self.release_connection(conn)
            
    def add_event(self, symbol, event_type, message):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            placeholder = "%s" if self.is_postgres else "?"
            sql = f"INSERT INTO trade_events (symbol, event_type, message, created_at) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})"
            now_str = dt_module.datetime.now(TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")
            cursor.execute(sql, (symbol, event_type, message, now_str))
            conn.commit()
        except Exception as e:
            print(f"⚠️ Erro ao registrar evento {event_type} para {symbol}: {e}")
        finally:
            self.release_connection(conn)

    def get_recent_trades(self, limit=20):
        try:
            conn = self.get_connection()
            try:
                query = f"SELECT * FROM trades ORDER BY id DESC LIMIT {limit}"
                df = pd.read_sql_query(query, conn)
            finally:
                self.release_connection(conn)
            
            if df.empty:
                return pd.DataFrame()
                
            df = df.sort_values("id", ascending=True)
            restored_rows = []
            for _, row in df.iterrows():
                raw = row.get("raw_data")
                if raw:
                    try:
                        data = json.loads(raw)
                        data['id'] = row['id']
                        restored_rows.append(data)
                    except Exception:
                        pass
                else:
                    # Fallback para trades antigos (sem raw_data)
                    data = {
                        'id': row.get('id'),
                        'Data/Hora da Compra': row.get('oco_timestamp') or row.get('buy_timestamp', 'N/A'),
                        'Símbolo': row.get('symbol', 'N/A'),
                        'market_type': row.get('market_type', 'SPOT'),
                        'direction': row.get('direction', 'LONG'),
                        'Resultado Total Liquido': row.get('trade_result_net', 0.0),
                    }
                    restored_rows.append(data)
            
            return pd.DataFrame(restored_rows)
        except Exception:
            try:
                self.create_tables()
            except Exception:
                pass
            return pd.DataFrame()

    def get_stats(self):
        global _stats_cache, _last_stats_time
        import time
        now = time.time()
        if _stats_cache is not None and (now - _last_stats_time < 15.0):
            return _stats_cache

        try:
            conn = self.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as total FROM trades WHERE market_type = 'SPOT' OR market_type IS NULL")
                row = cursor.fetchone()
                spot_trades = row["total"] if self.is_postgres else row[0]
                
                cursor.execute("SELECT COUNT(*) as wins FROM trades WHERE (oco_result = 'profit' OR trade_result_net > 0) AND (market_type = 'SPOT' OR market_type IS NULL)")
                row_wins = cursor.fetchone()
                spot_wins = row_wins["wins"] if self.is_postgres else row_wins[0]
                
                cursor.execute("SELECT COUNT(*) as total FROM trades WHERE market_type = 'FUTURES'")
                row_fut = cursor.fetchone()
                futures_trades = row_fut["total"] if self.is_postgres else row_fut[0]

                cursor.execute("SELECT COUNT(*) as wins FROM trades WHERE trade_result_net > 0 AND market_type = 'FUTURES'")
                row_fut_wins = cursor.fetchone()
                futures_wins = row_fut_wins["wins"] if self.is_postgres else row_fut_wins[0]

                cursor.execute("SELECT SUM(trade_result_net) as net_sum FROM trades WHERE market_type = 'SPOT' OR market_type IS NULL")
                row_sum = cursor.fetchone()
                result = row_sum["net_sum"] if self.is_postgres else row_sum[0]
                spot_net_profit = float(result) if result is not None else 0.0

                cursor.execute("SELECT SUM(trade_result_net) as net_sum FROM trades WHERE market_type = 'FUTURES'")
                row_fut_sum = cursor.fetchone()
                fut_result = row_fut_sum["net_sum"] if self.is_postgres else row_fut_sum[0]
                futures_net_profit = float(fut_result) if fut_result is not None else 0.0
                
                spot_win_rate = (spot_wins / spot_trades * 100) if spot_trades > 0 else 0
                futures_win_rate = (futures_wins / futures_trades * 100) if futures_trades > 0 else 0
                total_net_profit = spot_net_profit + futures_net_profit
                
                _stats_cache = {
                    "total_trades": spot_trades + futures_trades,
                    "spot_trades": spot_trades,
                    "futures_trades": futures_trades,
                    "wins": spot_wins + futures_wins,
                    "spot_wins": spot_wins,
                    "futures_wins": futures_wins,
                    "win_rate": spot_win_rate,
                    "spot_win_rate": spot_win_rate,
                    "futures_win_rate": futures_win_rate,
                    "total_net_profit": total_net_profit,
                    "spot_net_profit": spot_net_profit,
                    "futures_net_profit": futures_net_profit
                }
                _last_stats_time = now
                return _stats_cache
            finally:
                self.release_connection(conn)
        except Exception:
            return {
                "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_net_profit": 0.0,
                "spot_trades": 0, "futures_trades": 0, "spot_wins": 0, "futures_wins": 0,
                "spot_win_rate": 0, "futures_win_rate": 0, "spot_net_profit": 0.0, "futures_net_profit": 0.0
            }

    def get_daily_stats(self, date_str=None):
        if not date_str:
            brt_tz = dt_module.timezone(dt_module.timedelta(hours=-3))
            date_str = dt_module.datetime.now(brt_tz).strftime("%d/%m/%Y")
            
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            placeholder = "%s" if self.is_postgres else "?"
            sql = f"SELECT trade_result_net, oco_result, market_type FROM trades WHERE oco_timestamp LIKE {placeholder}"
            cursor.execute(sql, (f"%{date_str}%",))
            rows = cursor.fetchall()
        finally:
            self.release_connection(conn)
        
        trades_count = len(rows)
        daily_pnl = 0.0
        spot_pnl = 0.0
        futures_pnl = 0.0
        wins = 0
        losses = 0
        
        for r in rows:
            pnl = float(r["trade_result_net"] if hasattr(r, "__getitem__") and "trade_result_net" in r else (r[0] if isinstance(r, (list, tuple)) else 0.0))
            market = str(r["market_type"] if hasattr(r, "__getitem__") and "market_type" in r else (r[2] if isinstance(r, (list, tuple)) and len(r) > 2 else "SPOT")).upper()
            
            daily_pnl += pnl
            if market == "FUTURES":
                futures_pnl += pnl
            else:
                spot_pnl += pnl
                
            res = str(r["oco_result"] if hasattr(r, "__getitem__") and "oco_result" in r else (r[1] if isinstance(r, (list, tuple)) else ""))
            if pnl > 0 or "LUCRO" in res.upper() or "TAKE" in res.upper():
                wins += 1
            else:
                losses += 1
                
        win_rate = (wins / trades_count * 100.0) if trades_count > 0 else 0.0
        return {
            "date": date_str,
            "trades": trades_count,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "daily_pnl": daily_pnl,
            "spot_pnl": spot_pnl,
            "futures_pnl": futures_pnl
        }

    def migrate_from_csv(self, csv_path="results.csv"):
        csv_file = BASE_DIR / csv_path
        if not csv_file.exists():
            return

        try:
            df = pd.read_csv(csv_file, on_bad_lines="skip", engine="python")
            print(f"Migrando {len(df)} registros do CSV para o Banco de Dados...")
            
            for _, row in df.iterrows():
                data_row = row.to_dict()
                for k, v in data_row.items():
                    if pd.isna(v):
                        data_row[k] = None
                self.add_trade(data_row)
                
            print("Migração concluída.")
            backup_path = csv_file.with_name(f"{csv_file.stem}_backup{csv_file.suffix}")
            csv_file.rename(backup_path)
            print(f"Arquivo CSV renomeado para {backup_path}")
            
        except Exception as e:
            print(f"Erro na migração do CSV: {e}")

    def export_trades_csv(self, start_date=None, end_date=None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            
            sql = "SELECT order_index, symbol, quantity, buy_price, oco_result, oco_timestamp, trade_result_net, final_balance_usdt FROM trades"
            params = []
            
            if start_date and end_date:
                placeholder = "%s" if self.is_postgres else "?"
                sql += f" WHERE oco_timestamp >= {placeholder} AND oco_timestamp <= {placeholder}"
                params = [start_date, end_date]
            elif start_date:
                placeholder = "%s" if self.is_postgres else "?"
                sql += f" WHERE oco_timestamp >= {placeholder}"
                params = [start_date]
                
            sql += " ORDER BY id DESC"
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        finally:
            self.release_connection(conn)
        
        data = []
        for r in rows:
            data.append({
                "Índice": r["order_index"] if hasattr(r, "__getitem__") else r[0],
                "Símbolo": r["symbol"] if hasattr(r, "__getitem__") else r[1],
                "Quantidade": r["quantity"] if hasattr(r, "__getitem__") else r[2],
                "Preço Compra": r["buy_price"] if hasattr(r, "__getitem__") else r[3],
                "Resultado OCO": r["oco_result"] if hasattr(r, "__getitem__") else r[4],
                "Data/Hora": r["oco_timestamp"] if hasattr(r, "__getitem__") else r[5],
                "PnL Líquido (USDT)": r["trade_result_net"] if hasattr(r, "__getitem__") else r[6],
                "Saldo Final (USDT)": r["final_balance_usdt"] if hasattr(r, "__getitem__") else r[7],
            })
        
        df = pd.DataFrame(data)
        return df.to_csv(index=False)
