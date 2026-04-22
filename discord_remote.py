import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import json
import os
import asyncio
from datetime import datetime

# --- KONFIGURATION ---
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
CHANNEL_ID = 1470792864928763906  # ID deines Discord-Kanals (Rechtsklick auf Kanal -> ID kopieren)
SETTINGS_FILE = "settings.json"
MONITOR_FILE = "monitor.json"
ACCOUNTS_FILE = "accounts.json"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Globale Variable für die Dashboard-Nachricht
dashboard_message = None

# --- HELPER ---
def load_json(filename):
    if not os.path.exists(filename): return {}
    try:
        with open(filename, "r") as f: return json.load(f)
    except: return {}

def save_json(filename, data):
    try:
        with open(filename, "w") as f: json.dump(data, f, indent=4)
    except: pass

# --- DAS AUSWAHL-MENÜ (DROPDOWN) ---
class AccountSelect(Select):
    def __init__(self):
        # Accounts laden
        accounts = load_json(ACCOUNTS_FILE)
        options = []
        for login, details in accounts.items():
            options.append(discord.SelectOption(
                label=f"{details.get('name', login)} ({login})", 
                value=login,
                description=f"Server: {details.get('server', 'Unknown')}"
            ))
        
        # FIX: Wenn leer, Dummy-Option anzeigen (sonst crasht Discord mit Error 50035)
        if not options:
            options.append(discord.SelectOption(label="Keine Accounts gespeichert", value="none", description="Nutze !account zum Hinzufügen"))

        # WICHTIG: Hier muss options=options übergeben werden!
        super().__init__(
            placeholder="🔄 Wähle ein Konto...", 
            min_values=1, 
            max_values=1, 
            custom_id="acc_select",
            options=options # <--- DAS HAT GEFEHLT
        )

    async def callback(self, interaction: discord.Interaction):
        selected_login = self.values[0]
        if selected_login == "none":
            await interaction.response.send_message("❌ Keine Accounts konfiguriert! Nutze `!account`.", ephemeral=True)
            return

        # Settings updaten -> Main.py merkt das und loggt um
        data = load_json(SETTINGS_FILE)
        data["target_account"] = selected_login
        data["status"] = "switch_requested" 
        data["trading_active"] = False # Kurze Pause beim Wechsel
        save_json(SETTINGS_FILE, data)
        
        await interaction.response.send_message(f"🔄 **Wechsel eingeleitet!** Der Bot loggt sich jetzt in Konto `{selected_login}` ein...", ephemeral=True)

# --- VIEW 1: DAS DASHBOARD (Knöpfe + Dropdown) ---
class DashboardView(View):
    def __init__(self):
        super().__init__(timeout=None)
        # Dropdown hinzufügen
        self.add_item(AccountSelect())

    @discord.ui.button(label="▶️ START", style=discord.ButtonStyle.green, custom_id="dash_start", row=1)
    async def start_btn(self, interaction: discord.Interaction, button: Button):
        data = load_json(SETTINGS_FILE)
        data["trading_active"] = True
        if data.get("status") != "running":
            data["status"] = "running"
        save_json(SETTINGS_FILE, data)
        await interaction.response.defer()

    @discord.ui.button(label="⏸️ PAUSE", style=discord.ButtonStyle.red, custom_id="dash_stop", row=1)
    async def stop_btn(self, interaction: discord.Interaction, button: Button):
        data = load_json(SETTINGS_FILE)
        data["trading_active"] = False
        save_json(SETTINGS_FILE, data)
        await interaction.response.defer()

    @discord.ui.button(label="🔄 RESET STATS", style=discord.ButtonStyle.blurple, custom_id="dash_reset", row=1)
    async def reset_btn(self, interaction: discord.Interaction, button: Button):
        data = load_json(SETTINGS_FILE)
        data["status"] = "reset_requested"
        data["trading_active"] = True
        save_json(SETTINGS_FILE, data)
        await interaction.response.send_message("✅ Reset angefordert!", ephemeral=True)

# --- VIEW 2: ALARM RESET (Popup bei Zielerreichung) ---
class AlertResetView(View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🔄 RESET & WEITERMACHEN", style=discord.ButtonStyle.green, custom_id="alert_reset")
    async def reset_button(self, interaction: discord.Interaction, button: Button):
        data = load_json(SETTINGS_FILE)
        data["status"] = "reset_requested"
        data["trading_active"] = True
        save_json(SETTINGS_FILE, data)
        await interaction.response.send_message(f"🚀 Reset ausgeführt! Bot startet neu.", ephemeral=False)

# --- LOOP: AKTUALISIERT ALLES ---
@tasks.loop(seconds=10)
async def main_loop():
    global dashboard_message
    
    monitor = load_json(MONITOR_FILE)
    settings = load_json(SETTINGS_FILE)
    if not settings: return

    # A) ALARME PRÜFEN (Push-Nachricht)
    status = settings.get("status", "running")
    channel = bot.get_channel(CHANNEL_ID)
    
    if channel:
        if status == "take_profit":
            settings["status"] = "notified_profit" # Damit wir nicht spammen
            save_json(SETTINGS_FILE, settings)
            await channel.send("🎉 **GLÜCKWUNSCH: TAGESZIEL ERREICHT!**", view=AlertResetView())
        
        elif status == "max_loss":
            settings["status"] = "notified_loss"
            save_json(SETTINGS_FILE, settings)
            await channel.send("🚨 **ALARM: MAX DRAWDOWN ERREICHT!**", view=AlertResetView())

    # B) DASHBOARD AKTUALISIEREN
    if dashboard_message and monitor:
        is_active = settings.get("trading_active", True)
        display_status = settings.get("status", "running")
        
        # Farbe wählen
        color = discord.Color.green() if is_active and display_status == "running" else discord.Color.red()
        if "profit" in display_status: color = discord.Color.gold()
        
        embed = discord.Embed(title="🤖 TRADING COCKPIT", color=color)
        
        # Welches Konto läuft gerade?
        current_acc = monitor.get("account_id", "Unknown")
        
        embed.add_field(name="Zustand", value=f"**{'LÄUFT' if is_active else 'PAUSIERT'}**\nStatus: `{display_status}`", inline=False)
        embed.add_field(name="💳 Aktives Konto", value=f"`{current_acc}`", inline=False)
        
        embed.add_field(name="💰 Equity", value=f"${monitor.get('equity', 0):,.2f}", inline=True)
        embed.add_field(name="📈 PnL Heute", value=f"{monitor.get('profit_today_pct', 0):+.2f}%", inline=True)
        embed.add_field(name="📊 Trades", value=str(monitor.get('open_trades', 0)), inline=True)
        
        embed.set_footer(text=f"Update: {monitor.get('last_update', '??:??')} NY Time")

        try:
            # View neu erstellen, damit Dropdown aktuell bleibt
            await dashboard_message.edit(embed=embed, view=DashboardView())
        except:
            dashboard_message = None

# --- BEFEHLE ---

@bot.command()
async def panel(ctx):
    """Erstellt das Dashboard neu"""
    global dashboard_message
    if dashboard_message:
        try: await dashboard_message.delete()
        except: pass
    
    embed = discord.Embed(title="🤖 Lade System...", color=discord.Color.blue())
    # Hier wird DashboardView initialisiert (lädt Accounts aus Datei)
    dashboard_message = await ctx.send(embed=embed, view=DashboardView())

@bot.command()
async def account(ctx, login: str, password: str, server: str, *, name: str = "Konto"):
    """Fügt ein Konto hinzu: !account 123 pw "Server Name" Name"""
    try: await ctx.message.delete() # Sicherheit
    except: pass
    
    data = load_json(ACCOUNTS_FILE)
    data[login] = {"name": name, "password": password, "server": server}
    save_json(ACCOUNTS_FILE, data)
    
    msg = await ctx.send(f"✅ Konto `{login}` gespeichert! Tippe `!panel` zum Aktualisieren.")
    await asyncio.sleep(5)
    try: await msg.delete()
    except: pass

@bot.command()
async def list_accounts(ctx):
    data = load_json(ACCOUNTS_FILE)
    if not data: await ctx.send("Keine Konten.")
    else:
        text = "**Konten:**\n" + "\n".join([f"• {v['name']} (`{k}`)" for k,v in data.items()])
        await ctx.send(text)

@bot.event
async def on_ready():
    print(f"🎮 Bot Online: {bot.user}")
    if not main_loop.is_running():
        main_loop.start()


if not TOKEN:
    raise RuntimeError("Set DISCORD_BOT_TOKEN before starting the Discord bot.")

bot.run(TOKEN)
