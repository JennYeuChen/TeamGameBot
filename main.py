import discord
from discord.ext import commands
from discord.ui import Button, View
import os
import random
import threading
from flask import Flask

# --- Flask 伺服器 (防止休眠) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), use_reloader=False)

# --- 21 點遊戲系統 ---
def get_deck():
    cards = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    suits = ['♥', '♦', '♣', '♠']
    deck = [rank + suit for rank in cards for suit in suits]
    random.shuffle(deck)
    return deck

def calculate_score(hand):
    score = 0
    aces = 0
    for card in hand:
        rank = card[:-1]
        if rank in ['J', 'Q', 'K']: score += 10
        elif rank == 'A':
            score += 11
            aces += 1
        else: score += int(rank)
    while score > 21 and aces:
        score -= 10
        aces -= 1
    return score

class BlackjackView(View):
    def __init__(self, owner, deck, user_hand, dealer_hand):
        super().__init__(timeout=60)
        self.owner = owner
        self.deck = deck
        self.user_hand = user_hand
        self.dealer_hand = dealer_hand

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.owner:
            await interaction.response.send_message("❌ 這是別人的牌局！", ephemeral=True)
            return False
        return True

    def get_embed(self, title):
        # 將手牌轉換為純數值字串 (例如將 '10♥' 簡化為 '10')
        user_display = [card[:-1] for card in self.user_hand]
        dealer_display = [self.dealer_hand[0][:-1], "🎴"]
        
        embed = discord.Embed(title=title, color=discord.Color.green())
        embed.add_field(name="你的手牌", value=f"{', '.join(user_display)} (點數: {calculate_score(self.user_hand)})", inline=False)
        embed.add_field(name="莊家手牌", value=f"{', '.join(dealer_display)}", inline=False)
        return embed

    @discord.ui.button(label="抽牌", style=discord.ButtonStyle.primary, custom_id="hit")
    async def hit(self, interaction: discord.Interaction, button: Button):
        self.user_hand.append(self.deck.pop())
        score = calculate_score(self.user_hand)
        if score > 21:
            await interaction.response.edit_message(embed=self.get_embed("💥 爆掉啦！你輸了"), view=None)
            self.stop()
        else:
            await interaction.response.edit_message(embed=self.get_embed("繼續抽牌..."), view=self)

    @discord.ui.button(label="跳過 (停牌)", style=discord.ButtonStyle.secondary, custom_id="stand")
    async def stand(self, interaction: discord.Interaction, button: Button):
        while calculate_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())
        
        u_s, d_s = calculate_score(self.user_hand), calculate_score(self.dealer_hand)
        res = "🎉 你贏了！" if d_s > 21 or u_s > d_s else "😭 莊家獲勝" if u_s < d_s else "平手！"
        
        # 結果頁面也顯示數字化牌面
        user_display = [card[:-1] for card in self.user_hand]
        dealer_display = [card[:-1] for card in self.dealer_hand]
        
        embed = discord.Embed(title=f"結果：{res}", color=discord.Color.gold())
        embed.add_field(name="你的牌", value=f"{', '.join(user_display)} ({u_s})")
        embed.add_field(name="莊家牌", value=f"{', '.join(dealer_display)} ({d_s})")
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

# --- 機器人設定 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"機器人已啟動: {bot.user}")

@bot.command(name="21")
async def bj(ctx):
    thread = await ctx.message.create_thread(name=f"🃏 {ctx.author.name} 的 21 點")
    deck = get_deck()
    view = BlackjackView(ctx.author, deck, [deck.pop(), deck.pop()], [deck.pop(), deck.pop()])
    await thread.send(embed=view.get_embed("新遊戲開始！"), view=view)

# --- 啟動 ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(os.environ.get("DISCORD_TOKEN"))
