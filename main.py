import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
import json
import os
import random
import datetime
import threading
from flask import Flask

app = Flask('')

@app.route('/')
def home():
    return "機器人正在雲端運行中！"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    # 加入 use_reloader=False 避免 Flask 自己重複啟動導致衝突
    try:
        app.run(host="0.0.0.0", port=port, use_reloader=False)
    except Exception as e:
        print(f"Flask 啟動失敗 (可能已在運行): {e}")

# 設定機器人
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

RED_TEAM_ROLE_ID = 1509553715181391972  # 替換為紅隊身分組 ID
BLUE_TEAM_ROLE_ID = 1509553764581769427 # 替換為藍隊身分組 ID

# --- 🛠️ 絕對路徑防禦存檔區塊 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, "bot_data_team")
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

DATA_FILE = os.path.join(DATA_FOLDER, "team_game_stats.json")

# 載入數據
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            game_data = json.load(f)
    except:
        game_data = {"teams": {"red": 0, "blue": 0}, "users": {}}
else:
    game_data = {"teams": {"red": 0, "blue": 0}, "users": {}}

# 🟢 加在這裡
LAST_EVENT_TIME = datetime.datetime.min

# --- 🛒 商店價格設定區 (隨時可以調整) ---
SHOP_PRICES = {
    "精準打擊": 15,    # 原 20 -> 降至 15
    "幸運翻倍卡": 40,  # 原 60 -> 降至 40
    "陣營核彈": 150,   # 原 200 -> 降至 150
    "積分竊盜術": 80,  # 原 120 -> 降至 80
    "全服大聲公": 80,  # 原 100 -> 降至 80
    "全民大投票": 100  # 原 150 -> 降至 100
}

# 📊 Google Sheets 連線設定
from google.oauth2.service_account import Credentials
import gspread

SHEET_CONNECTED = False
sheet = None
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

try:
    creds_json = os.environ.get("GOOGLE_CREDS")
    
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open("TeamGameData").sheet1
        SHEET_CONNECTED = True
        print("📊 【系統】雲端 Google Sheets 連線成功！")
    elif os.path.exists("creds.json"):
        with open("creds.json", "r", encoding="utf-8") as f:
            creds_dict = json.load(f)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open("TeamGameData").sheet1
        SHEET_CONNECTED = True
        print("📊 【系統】本地 Google Sheets 連線成功！")
except Exception as e:
    print(f"❌ 【系統】試算表連線失敗：{e}，自動降級純本地模式。")

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(game_data, f, indent=4, ensure_ascii=False)
    
    if SHEET_CONNECTED:
        threading.Thread(target=sync_to_sheets, daemon=True).start()

def sync_to_sheets():
    try:
        if not SHEET_CONNECTED:
            return
        
        # 準備資料
        data = []
        for uid, udata in game_data.get("users", {}).items():
            data.append([
                str(uid),
                udata.get("points", 0),
                udata.get("total_msg", 0),
                udata.get("daily_msg", 0),
                udata.get("last_checkin", "")
            ])
        
        # 不要刪除，直接從第 2 行開始覆蓋寫入
        if data:
            sheet.update(range_name='A2', values=data)
        
        # 分數同步
        sheet.update_acell("H1", f"紅隊總分: {game_data['teams']['red']}")
        sheet.update_acell("H2", f"藍隊總分: {game_data['teams']['blue']}")
    except Exception as e:
        print(f"📊 【雲端同步錯誤】: {e}")

# --- 1. 每日午夜重設任務 (台灣時間 UTC+8，即 UTC 16:00) ---
@tasks.loop(time=datetime.time(hour=16, minute=0))
async def reset_daily_stats():
    for user_id in game_data["users"]:
        game_data["users"][user_id]["daily_msg"] = 0
    save_data()
    # 確保雲端數據也同步重置
    if SHEET_CONNECTED:
        threading.Thread(target=sync_to_sheets, daemon=True).start()
    print("【系統】每日午夜已重設所有人本日發言量。")

# --- 2. 監聽發言：自動平衡計分 + 個人積分 + 落後隊伍專屬寶藏 ---
@bot.event
async def on_message(message):
    global LAST_EVENT_TIME

    if message.author.bot or message.webhook_id is not None or message.guild is None:
        return

    user_id = str(message.author.id)
    # 初始化資料
    if user_id not in game_data["users"]:
        game_data["users"][user_id] = {"points": 0, "total_msg": 0, "daily_msg": 0, "last_checkin": ""}

    user_role_ids = [role.id for role in message.author.roles]
    team = "red" if RED_TEAM_ROLE_ID in user_role_ids else "blue" if BLUE_TEAM_ROLE_ID in user_role_ids else None

    # --- 🟢 修改後的簽到系統：個人與團隊同時加分 ---
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if "早安" in message.content and game_data["users"][user_id].get("last_checkin") != today_str:
        # 1. 更新個人資料
        game_data["users"][user_id]["points"] += 10
        game_data["users"][user_id]["last_checkin"] = today_str
        
        # 2. 同步增加隊伍分數 (需先確認 user 有隊伍)
        if team:
            game_data["teams"][team] += 10
            
        save_data() # 存檔並自動同步到 Google Sheets
        await message.channel.send(f"☀️ {message.author.mention} 簽到成功！為 {('🔴 紅隊' if team == 'red' else '🔵 藍隊')} 貢獻了 10 分，自己也獲得 **+10 積分**！")
    # -------------------------------------------

    if team:
        # 判斷是否為落後隊伍
        red_score = game_data["teams"]["red"]
        blue_score = game_data["teams"]["blue"]
        is_losing = (team == "red" and red_score < blue_score) or (team == "blue" and blue_score < red_score)
        
        event_points_gained = 0
        event_text = ""

        # 🎰 寶藏機制：僅落後隊伍有 1% 機率觸發暴擊
        dice = random.random()
        current_time = datetime.datetime.now()
        
        if is_losing and (current_time - LAST_EVENT_TIME).total_seconds() >= 15:
            if dice < 0.01:  # 1% 觸發
                event_points_gained = 50
                event_text = f"🔥 **【落後方獎勵：發現大寶藏！】** {message.author.mention} 挖到寶藏！隊伍與個人大賺 **+50 分**！"
                LAST_EVENT_TIME = current_time
                game_data["teams"][team] += 50

        # 基本加分
        game_data["teams"][team] += 1
        game_data["users"][user_id]["points"] += (1 + event_points_gained)
        game_data["users"][user_id]["total_msg"] += 1
        game_data["users"][user_id]["daily_msg"] += 1
        save_data()

        if event_text:
            embed = discord.Embed(description=event_text, color=discord.Color.gold())
            await message.channel.send(embed=embed)

    await bot.process_commands(message)

# --- 3. 空投補給箱機制（按鈕互動） ---
class AirdropView(View):
    def __init__(self):
        super().__init__(timeout=30) # 限時 30 秒搶奪
        self.claimed_users = []

    @discord.ui.button(label="🎒 搶奪空投物資！ (+10分)", style=discord.ButtonStyle.premium, custom_id="airdrop_claim")
    async def claim(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        user_role_ids = [role.id for role in interaction.user.roles]

        # 檢查是否有隊伍
        team = "red" if RED_TEAM_ROLE_ID in user_role_ids else "blue" if BLUE_TEAM_ROLE_ID in user_role_ids else None
        if not team:
            await interaction.response.send_message("❌ 你還沒有加入任何陣營，沒資格搶空投！", ephemeral=True)
            return

        if user_id in self.claimed_users:
            await interaction.response.send_message("❌ 這個空投箱你已經拿過囉，留點給別人吧！", ephemeral=True)
            return

        # 成功搶到
        self.claimed_users.append(user_id)
        
        # 初始化數據安全檢查
        if user_id not in game_data["users"]:
            game_data["users"][user_id] = {
                "points": 0, 
                "total_msg": 0, 
                "daily_msg": 0, 
                "last_checkin": ""
            }

        game_data["teams"][team] += 10
        game_data["users"][user_id]["points"] += 10
        save_data()

        team_name = "🔴 紅隊" if team == "red" else "🔵 藍隊"
        await interaction.response.send_message(f"📦 成功搜刮補給箱！幫 {team_name} 和自己各注入 **+10 積分**！", ephemeral=False)

# 管理員手動投下空投的指令
@bot.command(name="airdrop")
@commands.has_permissions(administrator=True)
async def spawn_airdrop(ctx):
    embed = discord.Embed(
        title="🛩️ 空投補給箱正在降落！",
        description="廣播、廣播！戰場上空降下了限時空投物資！\n點擊下方按鈕，可以立刻為你的隊伍與個人錢包 **+10 分**！\n*(倒數 30 秒後補給箱將會消失，每人限領一次！)*",
        color=discord.Color.purple()
    )
    await ctx.send(embed=embed, view=AirdropView())

# --- 4. 隨機選隊伍按鈕（自動平衡） ---
class AutoBalanceView(View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🎲 隨機加入隊伍（自動平衡）", style=discord.ButtonStyle.success, custom_id="join_random")
    async def join_random(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        red_role = guild.get_role(RED_TEAM_ROLE_ID)
        blue_role = guild.get_role(BLUE_TEAM_ROLE_ID)
        
        if not red_role or not blue_role:
            await interaction.response.send_message("❌ 伺服器身分組設定錯誤！", ephemeral=True)
            return
        if red_role in interaction.user.roles or blue_role in interaction.user.roles:
            await interaction.response.send_message("❌ 你已經在隊伍裡了！", ephemeral=True)
            return

        red_count = len(red_role.members)
        blue_count = len(blue_role.members)

        final_team = "blue" if red_count > blue_count else "red" if blue_count > red_count else random.choice(["red", "blue"])

        if final_team == "red":
            await interaction.user.add_roles(red_role)
            await interaction.response.send_message(f"平衡機制啟動！你被分配到 🔴 紅隊！", ephemeral=True)
        else:
            await interaction.user.add_roles(blue_role)
            await interaction.response.send_message(f"平衡機制啟動！你被分配到 🔵 藍隊！", ephemeral=True)

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_game(ctx):
    embed = discord.Embed(title="⚔️ 陣營對抗賽 - 隊伍招募中！", description="點擊下方按鈕自動分隊！發言賺取積分吧！", color=discord.Color.dark_magenta())
    await ctx.send(embed=embed, view=AutoBalanceView())

@bot.command()
@commands.is_owner()
async def load_json(ctx):
    await ctx.send("請上傳包含 JSON 資料的檔案！")
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.attachments
    
    try:
        msg = await bot.wait_for('message', check=check, timeout=60.0)
        attachment = msg.attachments[0]
        content = await attachment.read()
        new_data = json.loads(content.decode('utf-8'))
        
        global game_data
        game_data = new_data
        save_data()
        await ctx.send("✅ 資料已強制覆蓋並同步至雲端！")
    except Exception as e:
        await ctx.send(f"❌ 載入失敗：{e}")

# --- 5. 查詢個人資產與戰況：!status ---
@bot.command(name="status")
async def status(ctx):
    guild = ctx.guild
    red_role = guild.get_role(RED_TEAM_ROLE_ID)
    blue_role = guild.get_role(BLUE_TEAM_ROLE_ID)
    
    red_count = len(red_role.members) if red_role else 0
    blue_count = len(blue_role.members) if blue_role else 0

    user_id = str(ctx.author.id)
    
    # 讀取個人數據
    p_stats = game_data["users"].get(user_id, {"points": 0, "total_msg": 0, "daily_msg": 0, "last_checkin": ""})
    
    # 檢查今日是否已簽到
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    has_checked_in = p_stats.get("last_checkin") == today_str
    checkin_status = "✅ 已簽到" if has_checked_in else "❌ 未簽到 (說「早安」領取 10 積分！)"
    
    embed = discord.Embed(title="📊 陣營戰況與個人資產", color=discord.Color.green())
    
    # 個人資產面板
    embed.add_field(
        name=f"👤 {ctx.author.display_name} 的錢包",
        value=f"🪙 個人積分 (金幣)：**{p_stats['points']}**\n💬 今日發言：`{p_stats['daily_msg']}` 則\n📊 累計發言：`{p_stats['total_msg']}` 則\n☀️ 今日簽到：{checkin_status}",
        inline=False
    )
    # 陣營總分面板
    embed.add_field(name="🔴 紅隊總計", value=f"人數：{red_count} 人\n總分數：**{game_data['teams']['red']}** 分", inline=True)
    embed.add_field(name="🔵 藍隊總計", value=f"人數：{blue_count} 人\n總分數：**{game_data['teams']['blue']}** 分", inline=True)
    
    await ctx.send(embed=embed)

# --- 6. 全新按鈕分類商店與通知系統 ---

# 紀錄頻道的 ID
RECORD_CHANNEL_ID = 1329573312329809961

# 呼叫商店的指令
@bot.command(name="shop", aliases=["商店"])
async def shop(ctx):
    user_id = str(ctx.author.id)
    user_points = game_data["users"].get(user_id, {}).get("points", 0)
    
    embed = discord.Embed(
        title="🏪 陣營黑市大補貼", 
        description=f"歡迎光臨！請選擇你想購買的商品分類：\n目前你的個人資產：🪙 **{user_points}** 積分", 
        color=discord.Color.gold()
    )
    
    # 這裡只顯示預覽，控制交給下方的 View
    await ctx.send(embed=embed, view=ShopCategoryView())


# 類別選擇介面 (戰術 vs 福利)
class ShopCategoryView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="⚔️ 戰術比賽類商品", style=discord.ButtonStyle.danger, custom_id="shop_battle")
    async def battle_shop(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="⚔️ 戰術比賽商店 (自動扣敵方分數)", color=discord.Color.red())
        embed.add_field(name=f"💥 1. 精準打擊 — 價格 {SHOP_PRICES['精準打擊']} 積分", value="效果：直接扣除敵方陣營 **10 分**。", inline=False)
        embed.add_field(name=f"🎰 2. 幸運翻倍卡 — 價格 {SHOP_PRICES['幸運翻倍卡']} 積分", value="效果：接下來 5 句話有 50% 機率獲得 5 倍積分。\n*(購買後需等管理員稍後人工開啟或排進紀錄)*", inline=False)
        embed.add_field(name=f"☢️ 3. 陣營核彈 — 價格 {SHOP_PRICES['陣營核彈']} 積分", value="效果：直接摧毀敵方陣營 **120 分**！", inline=False)
        embed.add_field(name=f"🪓 4. 積分竊盜術 — 價格 {SHOP_PRICES['積分竊盜術']} 積分", value="效果：隨機偷取敵方一名在線成員 80~150 個人積分。", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=BattleItemsView())

    @discord.ui.button(label="🎁 社群福利類商品", style=discord.ButtonStyle.success, custom_id="shop_welfare")
    async def welfare_shop(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="🎁 社群福利商店 (管理員人工發放)", color=discord.Color.green())
        embed.add_field(name=f"📣 1. 全服大聲公 — 價格 {SHOP_PRICES['全服大聲公']} 積分", value="效果：由管理員幫你在公告頻道發表一句宣言並 Tag 全員！", inline=False)
        embed.add_field(name=f"📊 2. 全民大投票 — 價格 {SHOP_PRICES['全民大投票']} 積分", value="效果：獲得一次出題權，管理員會在投票頻道幫你發起一個話題投票！", inline=False)
        embed.set_footer(text="提示：按下購買後，紀錄會送至主要聊天室，管理員看到後會為您服務。")
        
        await interaction.response.edit_message(embed=embed, view=WelfareItemsView())


# 驗證錢包與發送主要聊天室紀錄的共用函式
async def process_purchase(interaction, cost, item_name, is_battle_item=False):
    user_id = str(interaction.user.id)
    user_role_ids = [role.id for role in interaction.user.roles]
    
    # 檢查隊伍
    my_team = "red" if RED_TEAM_ROLE_ID in user_role_ids else "blue" if BLUE_TEAM_ROLE_ID in user_role_ids else None
    if not my_team:
        await interaction.response.send_message("❌ 你還沒有加入任何陣營，無法購買！", ephemeral=True)
        return False, None
    
    # 🟢 新增：落後隊伍打 8 折優惠
    red_score = game_data["teams"]["red"]
    blue_score = game_data["teams"]["blue"]
    
    discounted_cost = cost
    if (my_team == "blue" and red_score > blue_score) or (my_team == "red" and blue_score > red_score):
        discounted_cost = int(cost * 0.8) # 打八折

    # 檢查點數 (使用折扣後的價格)
    user_points = game_data["users"].get(user_id, {}).get("points", 0)
    if user_points < discounted_cost:
        await interaction.response.send_message(f"❌ 你的積分不夠！購買【{item_name}】需要 `{discounted_cost}` 積分{' (已打8折)' if discounted_cost != cost else ''}，你目前只有 `{user_points}` 點。", ephemeral=True)
        return False, None

    # 扣點 (改用折扣價)
    game_data["users"][user_id]["points"] -= discounted_cost
    save_data()

    # 抓取主要聊天室
    record_channel = interaction.guild.get_channel(RECORD_CHANNEL_ID)
    my_team_name = "🔴 紅隊" if my_team == "red" else "🔵 藍隊"
    
    return True, (record_channel, my_team, my_team_name, discounted_cost)


# 戰術類別道具按鈕執行
class WelfareItemsView(View):
    def __init__(self): super().__init__(timeout=60)

    @discord.ui.button(label=f"📣 購買大聲公 ({SHOP_PRICES['全服大聲公']}分)", style=discord.ButtonStyle.primary)
    async def buy_broadcast(self, interaction: discord.Interaction, button: Button):
        success, info = await process_purchase(interaction, SHOP_PRICES["全服大聲公"], "全服大聲公")
        if success:
            record_channel, _, team_name, discounted_cost = info
            discount_note = " (已打8折)" if discounted_cost != SHOP_PRICES["全服大聲公"] else ""
            await interaction.response.send_message("✅ 購買成功！請等待管理員與你聯繫發表大聲公！", ephemeral=True)
            if record_channel:
                await record_channel.send(f"🛍️ **【黑市購物紀錄】** {team_name} 的 {interaction.user.mention} 剛剛花費了 **{discounted_cost} 積分{discount_note} 購買了 📣 **【全服大聲公】**！請管理員協助發放福利！")

    @discord.ui.button(label=f"📊 購買全民大投票 ({SHOP_PRICES['全民大投票']}分)", style=discord.ButtonStyle.primary)
    async def buy_vote(self, interaction: discord.Interaction, button: Button):
        success, info = await process_purchase(interaction, SHOP_PRICES["全民大投票"], "全民大投票")
        if success:
            record_channel, _, team_name, discounted_cost = info
            discount_note = " (已打8折)" if discounted_cost != SHOP_PRICES["全民大投票"] else ""
            await interaction.response.send_message("✅ 購買成功！請把你想問大家的問題整理好，等待管理員去投票頻道幫你出題！", ephemeral=True)
            if record_channel:
                await record_channel.send(f"🛍️ **【黑市購物紀錄】** {team_name} 的 {interaction.user.mention} 剛剛花費了 **{discounted_cost} 積分{discount_note} 購買了 📊 **【全民大投票】**！請管理員去投票頻道幫忙出題！")


# 戰術對抗類別道具按鈕執行 (維持自動化扣大比分)
class BattleItemsView(View):
    def __init__(self): super().__init__(timeout=60)

    @discord.ui.button(label=f"💥 精準打擊 ({SHOP_PRICES['精準打擊']}分)", style=discord.ButtonStyle.secondary)
    async def buy_smash(self, interaction: discord.Interaction, button: Button):
        success, info = await process_purchase(interaction, SHOP_PRICES["精準打擊"], "精準打擊")
        if success:
            record_channel, my_team, team_name, discounted_cost = info
            discount_note = " (已打8折)" if discounted_cost != SHOP_PRICES["精準打擊"] else ""
            enemy_team = "blue" if my_team == "red" else "red"
            enemy_name = "🔵 藍隊" if enemy_team == "blue" else "🔴 紅隊"
            
            game_data["teams"][enemy_team] -= 10
            save_data()
            
            await interaction.response.send_message(f"💥 成功發動打擊！敵方分數 -10！", ephemeral=True)
            if record_channel:
                await record_channel.send(f"💥 **【戰術打擊紀錄】** {team_name} 的 {interaction.user.mention} 花費了 **{discounted_cost} 積分{discount_note} 購買了 **【精準打擊】**，{enemy_name} 的團隊分數被強行扣除 **-10** 分！")

    @discord.ui.button(label=f"🎰 幸運翻倍卡 ({SHOP_PRICES['幸運翻倍卡']}分)", style=discord.ButtonStyle.secondary)
    async def buy_lucky(self, interaction: discord.Interaction, button: Button):
        success, info = await process_purchase(interaction, SHOP_PRICES["幸運翻倍卡"], "幸運翻倍卡")
        if success:
            record_channel, _, team_name, discounted_cost = info
            discount_note = " (已打8折)" if discounted_cost != SHOP_PRICES["幸運翻倍卡"] else ""
            await interaction.response.send_message("✅ 購買成功！", ephemeral=True)
            if record_channel:
                await record_channel.send(f"🛍️ **【黑市購物紀錄】** {team_name} 的 {interaction.user.mention} 花費了 **{discounted_cost} 積分{discount_note} 購買了 🎰 **【幸運翻倍卡】**！")

    @discord.ui.button(label=f"☢️ 陣營核彈 ({SHOP_PRICES['陣營核彈']}分)", style=discord.ButtonStyle.secondary)
    async def buy_nuke(self, interaction: discord.Interaction, button: Button):
        success, info = await process_purchase(interaction, SHOP_PRICES["陣營核彈"], "陣營核彈")
        if success:
            record_channel, my_team, team_name, discounted_cost = info
            discount_note = " (已打8折)" if discounted_cost != SHOP_PRICES["陣營核彈"] else ""
            enemy_team = "blue" if my_team == "red" else "red"
            enemy_name = "🔵 藍隊" if enemy_team == "blue" else "🔴 紅隊"
            
            game_data["teams"][enemy_team] -= 120
            save_data()
            
            await interaction.response.send_message(f"☢️ 核彈爆炸！敵方分數 -120！", ephemeral=True)
            if record_channel:
                await record_channel.send(f"☢️ **【毀滅打擊紀錄】** {team_name} 的 {interaction.user.mention} 花費了 **{discounted_cost} 積分{discount_note} 引爆了 **【陣營核彈】**！{enemy_name} 哀鴻遍野，總分數暴跌 **-120** 分！")

    @discord.ui.button(label=f"🪓 積分竊盜術 ({SHOP_PRICES['積分竊盜術']}分)", style=discord.ButtonStyle.secondary)
    async def buy_steal(self, interaction: discord.Interaction, button: Button):
        success, info = await process_purchase(interaction, SHOP_PRICES["積分竊盜術"], "積分竊盜術")
        if success:
            record_channel, my_team, team_name, discounted_cost = info
            discount_note = " (已打8折)" if discounted_cost != SHOP_PRICES["積分竊盜術"] else ""
            enemy_team = "blue" if my_team == "red" else "red"
            
            # 🟢 修正：精準抓出「目前真正待在敵方身分組」且身上有錢的受害者
            enemy_role_id = BLUE_TEAM_ROLE_ID if my_team == "red" else RED_TEAM_ROLE_ID
            enemy_role = interaction.guild.get_role(enemy_role_id)
            
            enemy_users = []
            if enemy_role:
                for u_id, data in game_data["users"].items():
                    # 檢查此 ID 是否在敵方身分組成員中，且有錢
                    if data.get("points", 0) > 0 and enemy_role.get_member(int(u_id)) is not None:
                        enemy_users.append(u_id)
            if not enemy_users:
                # 退錢
                user_id = str(interaction.user.id)
                game_data["users"][user_id]["points"] += discounted_cost
                save_data()
                await interaction.response.send_message("❌ 敵方陣營所有人都是窮光蛋，沒錢可偷！積分已退還。", ephemeral=True)
                return
                
            victim_id = random.choice(enemy_users)
            steal_amount = random.randint(80, 150)
            
            # 實際扣除被害者身上的錢（最少扣到 0）
            actual_stolen = min(game_data["users"][victim_id]["points"], steal_amount)
            game_data["users"][victim_id]["points"] -= actual_stolen
            
            # 加到小偷身上
            user_id = str(interaction.user.id)
            game_data["users"][user_id]["points"] += actual_stolen
            save_data()
            
            await interaction.response.send_message(f"🪓 偷竊成功！你從對手那裡摸走了 {actual_stolen} 積分！", ephemeral=True)
            if record_channel:
                victim_user = interaction.guild.get_member(int(victim_id))
                victim_name = victim_user.mention if victim_user else f"ID: {victim_id}"
                await record_channel.send(f"🪓 **【黑市小偷紀錄】** {team_name} 的 {interaction.user.mention} 花費了 **{discounted_cost} 積分{discount_note} 發動 **【積分竊盜術】**！把敵方成員 {victim_name} 口袋裡的 **{actual_stolen} 積分** 直接摸走了！")


# --- 7. 機器人上線通知 ---
@bot.event
async def on_ready():
    print(f"機器人已啟動：{bot.user}")
    
    # 強制從 Sheets 讀取分數並覆蓋 game_data
    if SHEET_CONNECTED:
        try:
            # 讀取 Sheet 上的分數
            red_str = sheet.acell("H1").value
            blue_str = sheet.acell("H2").value
            
            # 確保正確解析 (處理 "紅隊總分: 2345" 的格式)
            game_data['teams']['red'] = int(red_str.split(': ')[1])
            game_data['teams']['blue'] = int(blue_str.split(': ')[1])
            
            # 讀取 Sheet 上的用戶數據
            try:
                records = sheet.get_all_records()
                if records:
                    game_data['users'] = {}
                    for record in records:
                        # 確保數據有所有必要字段
                        user_id = str(record.get('user_id', ''))
                        if user_id:
                            game_data['users'][user_id] = {
                                'points': record.get('points', 0),
                                'total_msg': record.get('total_msg', 0),
                                'daily_msg': record.get('daily_msg', 0),
                                'last_checkin': record.get('last_checkin', '')
                            }
                    print(f"【系統】已強制從雲端同步 {len(game_data['users'])} 筆用戶數據！")
            except Exception as e:
                print(f"【用戶數據同步失敗】: {e}")
                
            print("【系統】已強制從雲端同步分數回記憶體！")
        except Exception as e:
            print(f"【同步失敗】: {e}")
    
    if not reset_daily_stats.is_running():
        reset_daily_stats.start()

# 🟢 修正：本機抓引號內的 Token，上雲端 Render 會自動抓環境變數中的 DISCORD_TOKEN
TOKEN = os.environ.get("DISCORD_TOKEN")

if __name__ == "__main__":
    # 建立一個執行緒來跑 Flask，這樣就不會擋住下面的 bot.run
    web_thread = threading.Thread(target=run_flask, daemon=True)
    web_thread.start()
    
    if not TOKEN:
        print("❌ 錯誤：找不到 DISCORD_TOKEN，請檢查 Render 環境變數設定！")
    else:
        bot.run(TOKEN)
