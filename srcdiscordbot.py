from datetime import datetime
import discord
from discord.ext import tasks
import aiohttp
import asyncio
import json
import os
import asyncio

from keep_alive import keep_alive

# --- CONFIG ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") 
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GAME_ID = "j1l9qz1g"  # Ocarina of Time game id on speedrun.com
CHECK_INTERVAL = 300  # seconds between API checks
DATA_FILE = "run_messages.json"

# --- Discord Setup ---
intents = discord.Intents.default()
bot = discord.Client(intents=intents)

keep_alive()

# --- Persistent Storage ---
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        try:
            run_messages = json.load(f)
        except:
            run_messages = {}
            with open(DATA_FILE, "w") as f:
                json.dump(run_messages, f)
else:
    run_messages = {}  # {run_id: discord_message_id}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(run_messages, f)

# --- Speedrun.com API Helper ---
async def fetch_json(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 404:
                return None  # Run was deleted
            return await resp.json()

async def get_pending_runs():
    url = f"https://www.speedrun.com/api/v1/runs?game={GAME_ID}&status=new&max=100&embed=category,platform,players"
    data = await fetch_json(url)
    if not data:
        return []
    return data["data"]

async def get_run_info(run_id):
    url = f"https://www.speedrun.com/api/v1/runs/{run_id}?embed=category,platform,players"
    data = await fetch_json(url)
    return data

# --- Core Logic ---
@tasks.loop(seconds=CHECK_INTERVAL)
async def check_runs():
    channel = bot.channel
    print(f"Hit checked runs: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. Check for new pending runs
    pending_runs = await get_pending_runs()

    # 2. Filter the runs so that runs that aren't already in discord are checked.
    filtered_runs = [run for run in pending_runs if run["id"] not in run_messages]

    for run in filtered_runs:
        run_id = run["id"]

        category = run.get('category', {}).get('data', {})
        category_name = category.get('name', 'Unknown Category')

        platform = run.get('platform', {}).get('data', {}).get('name', 'Unknown Platform')
        emulated = run.get('system', {}).get('emulated', False)

        if emulated:
            platform = platform + " (emu)"

        player = run.get('players', {}).get('data', [{}])[0]
        runner_name = player.get('names', {}).get(
            'international', player.get('name', 'Unknown Player'))

        time_sec = run.get('times', {}).get('primary_t', 0)
        mins, sec = divmod(time_sec, 60)
        hrs, mins = divmod(mins, 60)
        time_str = f"{int(hrs)}:{int(mins):02d}:{sec:05.2f}" if hrs else f"{int(mins)}:{sec:05.2f}" 

        color = discord.Color.blue()

        embed = discord.Embed(
            title=f"⏱️ Speedrun Submission - Status: New",
            color=color
        )

        embed.add_field(name="Player Name", value=runner_name, inline=True)
        embed.add_field(name="Category", value=category_name, inline=True)
        embed.add_field(name="Time", value=time_str, inline=True)
        embed.add_field(name="Platform", value=platform, inline=True)
        embed.add_field(name="Submitted On", value=run.get('date', 'Unknown'), inline=True)
        embed.add_field(name="Link", value=f"[View Run]({run.get('weblink', 'Unknown')})", inline=True)
        embed.set_footer(text=f"Last Changed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            msg = await safe_send(channel, embed=embed)
            run_messages[run_id] = {"MessageId": msg.id, "Status": "new"}
            save_data()
        except Exception as e:
            print(f"Failed to send message for run {run_id}: {e}")

        save_data()
        
        await asyncio.sleep(5)

    # 2. Loop through existing runs in json to see if the status has been changed (run verified / deleted / rejected).
    for run_id, message_details in list(run_messages.items()):
        message_id = message_details["MessageId"]
        storedStatus = message_details["Status"]

        # If the run status is one of these don't bother updating them any further as it's just extra api calls.
        if storedStatus == "deleted" or storedStatus == "verified" or storedStatus == "rejected":
            continue

        run_data = await get_run_info(run_id)

        # The run has been deleted from SRC.
        if run_data is None:            
            try:
                msg = await channel.fetch_message(message_id)
                await edit_embed_title_footer(msg, f"⏱️ Speedrun Submission - Status: Deleted", f"Last Changed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "Deleted", discord.Color.dark_grey())
            except discord.NotFound:
                pass
            run_messages[run_id] = { "MessageId": msg.id, "Status": "deleted" }
            save_data()
            continue
        
        status = run_data.get('data', {}).get('status', {}).get('status', 'Unknown')
        
        discordColor = discord.Color.blue()

        if status == "verified":
            discordColor = discord.Color.green()
        elif status == "rejected":
            discordColor = discord.Color.red()

        statusText = status.capitalize()
        
        if status == "verified" or status == "rejected":
            try:
                msg = await channel.fetch_message(message_id)
                await edit_embed_title_footer(msg, f"⏱️ Speedrun Submission - Status: {statusText}", f"Last Changed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", discordColor)
            except discord.NotFound:
                pass
            run_messages[run_id] = { "MessageId": msg.id, "Status": status }
            save_data()

        await asyncio.sleep(5)

async def edit_embed_title_footer(message: discord.Message, new_title: str, new_footer: str, new_color: discord.Color | None = None):
    if not message.embeds:
        print("Message has no embed to edit.")
        return

    old_embed = message.embeds[0]

    # Use new_color if given, else keep the old color
    color = new_color or old_embed.color

    # Create a new embed with updated title and footer, copying other properties
    new_embed = discord.Embed(
        title=new_title,
        url=old_embed.url,
        description=old_embed.description,
        color=color
    )

    # Copy all fields
    for field in old_embed.fields:  
        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)

    # Set new footer text
    new_embed.set_footer(text=new_footer)

    # Edit the message
    try:
        await safe_edit(message, embed=new_embed)
    except discord.errors.HTTPException as e:
        if e.status == 429:
            await asyncio.sleep(10)  # rate limit cooldown

async def safe_send(channel: discord.TextChannel, **kwargs):
    for attempt in range(5):
        try:
            return await channel.send(**kwargs)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(5)
            else:
                raise  # re-raise other HTTP errors            
    raise Exception("Failed to send message after retries")

async def safe_edit(message: discord.Message, **kwargs):
    for attempt in range(5):
        try:
            return await message.edit(**kwargs)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(5)
            else:
                raise
    raise Exception("Failed to edit message after retries")

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    channel = await bot.fetch_channel(CHANNEL_ID)
    bot.channel = channel

    check_runs.start()

bot.run(DISCORD_TOKEN)
