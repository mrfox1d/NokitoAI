import disnake
from disnake import Activity, ActivityType, Status
from disnake.ext import commands
from dotenv import load_dotenv
import os
from data.interaction import Database

db = Database()
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = commands.Bot(intents=disnake.Intents.all(), command_prefix="*")

@bot.event
async def on_ready():
    await bot.change_presence(activity=Activity(type=ActivityType.watching, name="Слушает ваши промпты... | created by mrfox1dddd"), status=Status.dnd)
    await db.init_db()
    print(f"Logged in as {bot.user.name} | {bot.user.id}")
    bot.load_extensions("cogs")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)