import aiohttp
import asyncio
from pathlib import Path

async def send_telegram_message(bot_token, chat_id, message, reply_markup=None):
    """Envia uma mensagem assíncrona para o Telegram com suporte a botões inline."""
    if not bot_token or not chat_id:
        return None

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                return await response.json()
    except Exception as e:
        # Log simplificado para não poluir o terminal em caso de instabilidade momentânea da rede
        return None

async def send_telegram_document(bot_token, chat_id, file_path, caption=""):
    """Envia um arquivo PDF ou documento assíncrono para o Telegram."""
    if not bot_token or not chat_id:
        return None

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        data = aiohttp.FormData()
        data.add_field('chat_id', str(chat_id))
        if caption:
            data.add_field('caption', caption)
            data.add_field('parse_mode', 'HTML')
        
        with open(file_path, 'rb') as f:
            data.add_field('document', f, filename=Path(file_path).name, content_type='application/pdf')
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    return await response.json()
    except Exception as e:
        print(f"🚨 Erro ao enviar documento no Telegram: {e}")
        return None

class TelegramBot:
    def __init__(self, token, allowed_chat_id, command_handler):
        self.token = token
        self.allowed_chat_id = str(allowed_chat_id)
        self.command_handler = command_handler
        self.running = False
        self.offset = 0
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_menu_keyboard(self, menu_type="main"):
        """Retorna os teclados inline interativos com navegação por submenus sofisticados."""
        if menu_type == "status":
            return {
                "inline_keyboard": [
                    [
                        {"text": "📊 Status do Robô", "callback_data": "/status"},
                        {"text": "💰 Saldos (USDT/BNB)", "callback_data": "/saldo"},
                    ],
                    [
                        {"text": "📈 PnL & Performance", "callback_data": "/lucro"},
                    ],
                    [
                        {"text": "🔙 Voltar", "callback_data": "sub_spot"},
                    ]
                ]
            }
        elif menu_type == "posicoes":
            return {
                "inline_keyboard": [
                    [
                        {"text": "⚡ Ordens OCO Ativas", "callback_data": "/posicoes"},
                    ],
                    [
                        {"text": "🔥 PANIC SELL (Venda Geral)", "callback_data": "/panic_sell_all"},
                    ],
                    [
                        {"text": "🔙 Voltar", "callback_data": "sub_spot"},
                    ]
                ]
            }
        elif menu_type == "risco":
            return {
                "inline_keyboard": [
                    [
                        {"text": "🛡️ Conservador", "callback_data": "/set_risk_conservador"},
                        {"text": "⚖️ Moderado", "callback_data": "/set_risk_moderado"},
                    ],
                    [
                        {"text": "🚀 Agressivo", "callback_data": "/set_risk_agressivo"},
                    ],
                    [
                        {"text": "🔙 Voltar", "callback_data": "sub_spot"},
                    ]
                ]
            }
        elif menu_type == "scanner":
            return {
                "inline_keyboard": [
                    [
                        {"text": "🏆 Top 6 Ranking", "callback_data": "/top40"},
                        {"text": "🔄 Rescan de Mercado", "callback_data": "/status"},
                    ],
                    [
                        {"text": "🔙 Voltar", "callback_data": "sub_spot"},
                    ]
                ]
            }
        elif menu_type == "ia":
            return {
                "inline_keyboard": [
                    [
                        {"text": "🧠 Sentimento & Notícias", "callback_data": "/noticias"},
                        {"text": "📊 Relatório PDF", "callback_data": "/relatorio"},
                    ],
                    [
                        {"text": "🔙 Voltar", "callback_data": "sub_spot"},
                    ]
                ]
            }
        elif menu_type == "config":
            return {
                "inline_keyboard": [
                    [
                        {"text": "📄 Gerar PDF Semanal", "callback_data": "/relatorio"},
                        {"text": "⏱️ Sync Relógio", "callback_data": "/status"},
                    ],
                    [
                        {"text": "🛑 Parar Robô", "callback_data": "/stop"},
                        {"text": "📱 Ajuda Completa", "callback_data": "/ajuda"},
                    ],
                    [
                        {"text": "🔙 Voltar", "callback_data": "sub_spot"},
                    ]
                ]
            }
        elif menu_type == "futures":
            return {
                "inline_keyboard": [
                    [
                        {"text": "📊 Status & Ordens Abertas", "callback_data": "/status_futures"},
                    ],
                    [
                        {"text": "💰 PnL & Saldo USDS-M", "callback_data": "/saldo_futures"},
                    ],
                    [
                        {"text": "🔥 PANIC SELL (FUTUROS)", "callback_data": "/panic_sell_futures"},
                    ],
                    [
                        {"text": "🔙 Voltar ao Menu Principal", "callback_data": "sub_main"},
                    ]
                ]
            }
        elif menu_type == "spot":
            return {
                "inline_keyboard": [
                    [
                        {"text": "📊 Status & Saldos", "callback_data": "sub_status"},
                        {"text": "📈 Posições OCO", "callback_data": "sub_posicoes"},
                    ],
                    [
                        {"text": "⚡ Top 6 Elite Scanner", "callback_data": "sub_scanner"},
                        {"text": "⚙️ Perfil de Risco", "callback_data": "sub_risco"},
                    ],
                    [
                        {"text": "🤖 Análise IA Gemini", "callback_data": "sub_ia"},
                        {"text": "🛠️ Configs & Operações", "callback_data": "sub_config"},
                    ],
                    [
                        {"text": "🔙 Voltar ao Menu Principal", "callback_data": "sub_main"},
                    ]
                ]
            }
        else: # "main"
            return {
                "inline_keyboard": [
                    [
                        {"text": "🟢 MERCADO SPOT", "callback_data": "sub_spot"},
                    ],
                    [
                        {"text": "🚀 MERCADO FUTUROS", "callback_data": "sub_futures"},
                    ]
                ]
            }

    async def start(self):
        self.running = True
        print("🤖 Telegram Bot ouvindo comandos...")
        async with aiohttp.ClientSession() as session:
            while self.running:
                try:
                    updates = await self.get_updates(session)
                    for update in updates:
                        await self.process_update(update, session)
                        self.offset = update['update_id'] + 1
                except Exception as e:
                    print(f"⚠️ Erro no loop do Telegram: {e}")
                    await asyncio.sleep(5)
                await asyncio.sleep(1)

    async def stop(self):
        self.running = False

    async def get_updates(self, session):
        try:
            url = f"{self.base_url}/getUpdates?offset={self.offset}&timeout=10"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('result', [])
                return []
        except Exception:
            return []

    async def process_update(self, update, session):
        # Suporte a Clique em Botões Inline
        if 'callback_query' in update:
            cb = update['callback_query']
            chat_id = str(cb.get('message', {}).get('chat', {}).get('id', ''))
            cb_id = cb.get('id')
            cmd_data = cb.get('data', '')
            
            if chat_id == self.allowed_chat_id:
                try:
                    await session.post(f"{self.base_url}/answerCallbackQuery", json={"callback_query_id": cb_id})
                except Exception:
                    pass
                    
                if cmd_data.startswith('sub_'):
                    menu_type = cmd_data.replace('sub_', '')
                    titles = {
                        "main": "🤖 <b>MENU PRINCIPAL — SPOTBOT PRO v7.0</b>\nSelecione o ambiente de operação:",
                        "spot": "🟢 <b>MERCADO SPOT</b>\nGerencie suas posições OCO e análises sem alavancagem:",
                        "status": "📊 <b>PAINEL SPOT (Status & Saldos)</b>\nConsulte o estado do robô, saldos e performance acumulada:",
                        "posicoes": "📈 <b>PAINEL SPOT (Posições OCO)</b>\nMonitore ordens ativas ou execute encerramentos:",
                        "futures": "🚀 <b>PAINEL DE MERCADO FUTUROS (HedgeFund)</b>\nControle posições alavancadas Long/Short de forma independente:",
                        "risco": "⚙️ <b>CONFIGURAÇÃO DE PERFIL DE RISCO</b>\nSelecione a estratégia de gestão de banca desejada:",
                        "scanner": "⚡ <b>SCANNER DE FORÇA RELATIVA TOP 6 ELITE</b>\nAcompanhe o ranking dos principais ativos selecionados do mercado:",
                        "ia": "🤖 <b>PAINEL INTELIGÊNCIA IA GEMINI</b>\nAnálise de sentimento e relatórios preditivos:",
                        "config": "🛠️ <b>OPERAÇÕES & CONFIGURAÇÕES</b>\nComandos de sistema, relatórios PDF e controle de operação:"
                    }
                    msg_text = titles.get(menu_type, "🤖 <b>PAINEL SPOTBOT PRO v7.0</b>")
                    await send_telegram_message(self.token, chat_id, msg_text, reply_markup=self.get_menu_keyboard(menu_type))
                elif cmd_data.startswith('/'):
                    response_text = await self.command_handler(cmd_data)
                    if response_text:
                        if cmd_data in ['/menu', '/ajuda', '/help']:
                            target_menu = "main"
                        else:
                            target_menu = "futures" if 'futures' in cmd_data else "spot"
                        await send_telegram_message(self.token, chat_id, response_text, reply_markup=self.get_menu_keyboard(target_menu))
            return

        message = update.get('message', {})
        chat_id = str(message.get('chat', {}).get('id', ''))
        text = message.get('text', '')

        if chat_id != self.allowed_chat_id:
            return

        if text.startswith('/'):
            response_text = await self.command_handler(text)
            if response_text:
                if text in ['/menu', '/ajuda', '/help']:
                    target_menu = "main"
                else:
                    target_menu = "futures" if 'futures' in text else "spot"
                await send_telegram_message(self.token, chat_id, response_text, reply_markup=self.get_menu_keyboard(target_menu))
