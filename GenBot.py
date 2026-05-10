import discord
import aiosqlite
import os
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
from dotenv import load_dotenv
from datetime import datetime
from datetime import timedelta
from discord.ext import tasks

element_time = 63000 
ALERT_CHANNEL_ID = 1499023994702266522  
CHECK_INTERVAL_MINUTES = 10  
ALERT_THRESHOLD_SECONDS = 86400  
GROUP_STATUS_CHANNELS = {
    "snowplat": 1501993059133427925,
    "tps": 1501993267640799303,
    
}  
STATUS_UPDATE_MINUTES = 30

intents = discord.Intents.default()
intents.message_content = True 
intents.members = True 
bot = commands.Bot(command_prefix='/', intents=intents)

DB_PATH = 'gen1.db'

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS generators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gen_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                elements INTEGER NOT NULL DEFAULT 0,
                UNIQUE(gen_name, group_name)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS fill_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gen_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                elements_added INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                user_name TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        await db.commit()
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                gen_name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                alerted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (gen_name, group_name)
            )
        ''')
        await db.commit()
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS status_message (
                group_name TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL
            )
        ''')
        await db.commit()
       

@bot.event
async def setup_hook():
    await init_db()
    await bot.tree.sync()

@bot.event
async def on_ready():
    
    await bot.change_presence(activity=discord.Game(name="𝗣𝗿𝗲𝗳𝗶𝘅= / "))
    if not check_low_generators.is_running():
        check_low_generators.start()
    
    if not update_status_loop.is_running():
        update_status_loop.start()

    print('=' * 30)
    print(f' Bot online!')
    print(f' Name: {bot.user.name}')
    print(f' ID: {bot.user.id}')
    print(f' Servers: {len(bot.guilds)}')
    for guild in bot.guilds:
        print(f' - {guild.name} ({guild.member_count} users)')
    print('=' * 30)

@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def check_low_generators():
    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if channel is None:
        print(f"⚠️ Didnt find channel {ALERT_CHANNEL_ID}")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        
        async with db.execute('''
            SELECT g.gen_name, g.group_name, g.elements, COALESCE(a.alerted, 0)
            FROM generators g
            LEFT JOIN alerts a 
                ON g.gen_name = a.gen_name AND g.group_name = a.group_name
        ''') as cursor:
            rows = await cursor.fetchall()

        for gen_name, group_name, elements, alerted in rows:
            total_seconds = elements * element_time
            is_low = total_seconds < ALERT_THRESHOLD_SECONDS

            if is_low and not alerted:
               
                try:
                    await channel.send(
                        f"@everyone **GEN : {gen_name}** GROUP ({group_name}) is about to run out element ! less than 24h left pls fill nigger ",
                        allowed_mentions=discord.AllowedMentions(everyone=True)
                    )
                except Exception as e:
                    print(f"failed to send an alert {e}")
                    continue

                await db.execute(
                    '''INSERT INTO alerts (gen_name, group_name, alerted) VALUES (?, ?, 1)
                       ON CONFLICT(gen_name, group_name) DO UPDATE SET alerted = 1''',
                    (gen_name, group_name)
                )

            elif not is_low and alerted:
                
                await db.execute(
                    '''INSERT INTO alerts (gen_name, group_name, alerted) VALUES (?, ?, 0)
                       ON CONFLICT(gen_name, group_name) DO UPDATE SET alerted = 0''',
                    (gen_name, group_name)
                )

        await db.commit()


@check_low_generators.before_loop
async def before_check():
    await bot.wait_until_ready()


async def build_status_embed(group_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT gen_name, elements FROM generators WHERE group_name = ? ORDER BY elements ASC',
            (group_name,)
        ) as cursor:
            results = await cursor.fetchall()

    embed = discord.Embed(
        title=f"📊 {group_name.upper()} — Generator Status (Live)",
        color=discord.Color.blue()
    )

    if not results:
        embed.description = f"No generators in **{group_name}** yet."
        embed.set_footer(text=f"Last update: {datetime.now().strftime('%H:%M')} | Auto-refresh every {STATUS_UPDATE_MINUTES} min")
        return embed

    for gen, elem in results[:25]:
        total_seconds = int(elem * element_time)
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        if total_seconds < ALERT_THRESHOLD_SECONDS:
            prefix = "🔴"
        elif total_seconds < ALERT_THRESHOLD_SECONDS * 2:
            prefix = "🟡"
        else:
            prefix = "🟢"

        embed.add_field(
            name=f"{prefix} {gen}",
            value=f"Elements: **{elem}** | {days}d {hours}h {minutes}m",
            inline=False
        )

    embed.set_footer(text=f"Last update: {datetime.now().strftime('%H:%M')} | Auto-refresh every {STATUS_UPDATE_MINUTES} min")
    return embed


async def refresh_status_message(group_name: str):
    """Edits or sends live status message for a given group."""
    channel_id = GROUP_STATUS_CHANNELS.get(group_name)
    if channel_id is None:
        # Group has no assigned channel - skip silently
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        print(f"⚠️ Channel {channel_id} not found for group {group_name}")
        return
    embed = await build_status_embed(group_name)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT message_id FROM status_message WHERE group_name = ?',
            (group_name,)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            try:
                message = await channel.fetch_message(int(row[0]))
                await message.edit(embed=embed)
                return
            except discord.NotFound:
                # Message was deleted - we'll send a new one
                pass
            except Exception as e:
                print(f"Failed to edit status for {group_name}: {e}")
                return
        try:
            message = await channel.send(embed=embed)
            await db.execute(
                '''INSERT OR REPLACE INTO status_message (group_name, channel_id, message_id) 
                   VALUES (?, ?, ?)''',
                (group_name, str(channel.id), str(message.id))
            )
            await db.commit()
        except Exception as e:
            print(f"Failed to send status for {group_name}: {e}")


async def refresh_all_status_messages():
    """Refreshes live status messages on all group channels."""
    for group_name in GROUP_STATUS_CHANNELS:
        await refresh_status_message(group_name)


@tasks.loop(minutes=STATUS_UPDATE_MINUTES)
async def update_status_loop():
    await refresh_all_status_messages()


@update_status_loop.before_loop
async def before_status():
    await bot.wait_until_ready()

# ============ FUNKCJE AUTOCOMPLETE ============
async def gen_name_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT DISTINCT gen_name FROM generators WHERE gen_name LIKE ? ORDER BY gen_name LIMIT 25',
            (f'%{current}%',)
        ) as cursor:
            rows = await cursor.fetchall()
    return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]


async def group_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT DISTINCT group_name FROM generators WHERE group_name LIKE ? ORDER BY group_name LIMIT 25',
            (f'%{current}%',)
        ) as cursor:
            rows = await cursor.fetchall()
    return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]

@bot.tree.command(name="fill", description="add element to generator")
@app_commands.describe(
    element='How much element did u fill ? ',
    gen_name='What generator did u fill?',
    group='From what group did u fill the gen ?)',
)
@app_commands.autocomplete(gen_name=gen_name_autocomplete, group=group_autocomplete)
async def fill(
    interaction: discord.Interaction,
    element: int,
    gen_name: str,
    group: str
):
    if element <= 0:
            await interaction.response.send_message(
                "CANT ADD 0 ELEMENT TO GENERATOR",
                ephemeral=True
            )
            return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            
            async with db.execute(
                'SELECT elements FROM generators WHERE gen_name = ? AND group_name = ?',
                (gen_name, group)
            ) as cursor:
                result = await cursor.fetchone()

            if result:
                new_total = result[0] + element
                await db.execute(
                    'UPDATE generators SET elements = ? WHERE gen_name = ? AND group_name = ?',
                    (new_total, gen_name, group)
                )
            else:
                new_total = element
                await db.execute(
                    'INSERT INTO generators (gen_name, group_name, elements) VALUES (?, ?, ?)',
                    (gen_name, group, element)
                )

            
            await db.execute(
                '''INSERT INTO fill_history 
                (gen_name, group_name, elements_added, user_id, user_name, timestamp) 
                VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    gen_name,
                    group,
                    element,
                    str(interaction.user.id),
                    interaction.user.name,
                    datetime.now().isoformat()
                )
            )

            await db.commit()

        
        embed = discord.Embed(
            title="✅ Generator filled!",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Generator", value=gen_name, inline=True)
        embed.add_field(name="Group", value=group, inline=True)
        embed.add_field(name="Added", value=f"+{element}", inline=True)
        embed.add_field(name="Total element", value=str(new_total), inline=False)
        total_seconds = int(new_total * element_time)
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        embed.add_field(name="Total time:", value=f"{days} days, {hours} hours, {minutes} mins", inline=False)

        embed.set_footer(text=f"Filled by {interaction.user.name}")
        

        await interaction.response.send_message(embed=embed)
        try:
            await refresh_status_message(group)
        except Exception as e:
            print(f"Failed to refresh status: {e}")

    except Exception as e:
            await interaction.response.send_message(
                f"❌ Error: {str(e)}",
                ephemeral=True
            )
   

@bot.tree.command(name="fillgroup", description="Add elements to ALL generators in a group")
@app_commands.describe(
    element='How much element to add to each generator',
    group='Which group to fill',
)
@app_commands.autocomplete(group=group_autocomplete)
async def fillgroup(
    interaction: discord.Interaction,
    element: int,
    group: str
):
    if element <= 0:
        await interaction.response.send_message(
            "CANT ADD 0 ELEMENT TO GENERATOR",
            ephemeral=True
        )
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            
            async with db.execute(
                'SELECT gen_name FROM generators WHERE group_name = ?',
                (group,)
            ) as cursor:
                generators = await cursor.fetchall()

            if not generators:
                await interaction.response.send_message(
                    f"❌ No generators found in group **{group}**",
                    ephemeral=True
                )
                return

            
            await db.execute(
                'UPDATE generators SET elements = elements + ? WHERE group_name = ?',
                (element, group)
            )

            
            timestamp = datetime.now().isoformat()
            for (gen_name,) in generators:
                await db.execute(
                    '''INSERT INTO fill_history 
                    (gen_name, group_name, elements_added, user_id, user_name, timestamp) 
                    VALUES (?, ?, ?, ?, ?, ?)''',
                    (
                        gen_name,
                        group,
                        element,
                        str(interaction.user.id),
                        interaction.user.name,
                        timestamp
                    )
                )

            await db.commit()

        
        embed = discord.Embed(
            title="✅ Group filled!",
            description=f"Added **+{element}** to every generator in **{group}**",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Group", value=group, inline=True)
        embed.add_field(name="Generators updated", value=str(len(generators)), inline=True)
        embed.add_field(name="Added per gen", value=f"+{element}", inline=True)
        embed.set_footer(text=f"Filled by {interaction.user.name}")

        await interaction.response.send_message(embed=embed)
        try:
            await refresh_status_message(group)
        except Exception as e:
            print(f"Failed to refresh status: {e}")
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Error: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="check", description="Check generator status")
@app_commands.describe(
    gen_name='Generator name (optional)',
    group='Group name (optional)'
)
@app_commands.autocomplete(gen_name=gen_name_autocomplete, group=group_autocomplete)
async def check(
    interaction: discord.Interaction,
    gen_name: str = None,
    group: str = None
):
    async with aiosqlite.connect(DB_PATH) as db:
        if gen_name and group:
            query = 'SELECT gen_name, group_name, elements FROM generators WHERE gen_name = ? AND group_name = ?'
            params = (gen_name, group)
        elif group:
            query = 'SELECT gen_name, group_name, elements FROM generators WHERE group_name = ? ORDER BY elements DESC'
            params = (group,)
        else:
            query = 'SELECT gen_name, group_name, elements FROM generators ORDER BY elements DESC'
            params = ()

        async with db.execute(query, params) as cursor:
            results = await cursor.fetchall()

    if not results:
        await interaction.response.send_message("No generators found.", ephemeral=True)
        return

    embed = discord.Embed(title="📊 Generator Status", color=discord.Color.blue())
    for gen, grp, elem in results[:25]:
        total_seconds = int(elem * element_time)
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        embed.add_field(
            name=f"{gen} ({grp})",
            value=f"Elements: **{elem}** | {days}d {hours}h {minutes}m",
            inline=False
    )
    await interaction.response.send_message(embed=embed)

class ConfirmDeleteView(View):
    def __init__(self, user_id: int, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.user_id = user_id  
        self.confirmed = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This is not your confirmation.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        self.confirmed = True
        self.stop()
        
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: Button):
        self.confirmed = False
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)



@bot.tree.command(name="remove", description="Remove a single generator")
@app_commands.describe(
    gen_name='Generator name to remove',
)
@app_commands.autocomplete(gen_name=gen_name_autocomplete)
async def remove(
    interaction: discord.Interaction,
    gen_name: str
):
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT gen_name, group_name, elements FROM generators WHERE gen_name = ?',
            (gen_name,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        await interaction.response.send_message(
            f"❌ Generator **{gen_name}** not found.",
            ephemeral=True
        )
        return

    found_gen, found_group, elements = result

    
    view = ConfirmDeleteView(interaction.user.id)
    embed = discord.Embed(
        title="⚠️ Confirm deletion",
        description=f"Are you sure you want to delete **{found_gen}** from **{found_group}**?\nElements: **{elements}**\n\nThis cannot be undone.",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    await view.wait()

    if view.confirmed is None:
        await interaction.followup.send("⏱️ Confirmation timed out.", ephemeral=True)
        return

    if not view.confirmed:
        await interaction.followup.send("✖️ Deletion cancelled.", ephemeral=True)
        return

   
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('DELETE FROM generators WHERE gen_name = ?', (found_gen,))
            await db.execute('DELETE FROM alerts WHERE gen_name = ?', (found_gen,))
            await db.commit()

        embed = discord.Embed(
            title="🗑️ Generator removed",
            description=f"**{found_gen}** has been deleted from **{found_group}**",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Deleted by {interaction.user.name}")
        await interaction.followup.send(embed=embed)

        try:
            await refresh_status_message(found_group)
        except Exception as e:
            print(f"fail {e}")

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)



@bot.tree.command(name="removegroup", description="Remove ALL generators in a group")
@app_commands.describe(
    group='Group to remove (deletes all its generators)',
)
@app_commands.autocomplete(group=group_autocomplete)
async def removegroup(
    interaction: discord.Interaction,
    group: str
):
   
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT COUNT(*) FROM generators WHERE group_name = ?',
            (group,)
        ) as cursor:
            count_row = await cursor.fetchone()
            count = count_row[0] if count_row else 0

    if count == 0:
        await interaction.response.send_message(
            f"❌ Group **{group}** has no generators (or doesn't exist).",
            ephemeral=True
        )
        return

    
    view = ConfirmDeleteView(interaction.user.id)
    embed = discord.Embed(
        title="⚠️ Confirm group deletion",
        description=(
            f"Are you sure you want to delete the entire group **{group}**?\n"
            f"This will remove **{count}** generator(s).\n\n"
            f"This cannot be undone."
        ),
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    await view.wait()

    if view.confirmed is None:
        await interaction.followup.send("⏱️ Confirmation timed out.", ephemeral=True)
        return

    if not view.confirmed:
        await interaction.followup.send("✖️ Deletion cancelled.", ephemeral=True)
        return

    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('DELETE FROM generators WHERE group_name = ?', (group,))
            await db.execute('DELETE FROM alerts WHERE group_name = ?', (group,))
            await db.commit()

        embed = discord.Embed(
            title="🗑️ Group removed",
            description=f"Deleted **{count}** generator(s) from group **{group}**",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Deleted by {interaction.user.name}")
        await interaction.followup.send(embed=embed)

       
        try:
            await refresh_status_message(group)
        except Exception as e:
            print(f"fail to refresh {e}")

    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="history", description="Show fill history")
@app_commands.describe(
    gen_name='Filter by generator (optional)',
    limit='How many entries to show (default 10, max 25)'
)
@app_commands.autocomplete(gen_name=gen_name_autocomplete)
async def history(
    interaction: discord.Interaction,
    gen_name: str = None,
    limit: int = 10
):
    limit = max(1, min(limit, 25))

    if gen_name:
        query = '''
            SELECT gen_name, group_name, elements_added, user_name, timestamp
            FROM fill_history
            WHERE gen_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
        '''
        params = (gen_name, limit)
    else:
        query = '''
            SELECT gen_name, group_name, elements_added, user_name, timestamp
            FROM fill_history
            ORDER BY timestamp DESC
            LIMIT ?
        '''
        params = (limit,)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("No history found.", ephemeral=True)
        return

    embed = discord.Embed(title="📜 Fill History", color=discord.Color.blue())

    lines = []
    for gen, grp, added, user_name, timestamp in rows:
        try:
            dt = datetime.fromisoformat(timestamp)
            time_str = dt.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            time_str = timestamp
        lines.append(f"`{time_str}` — **{user_name}** filled `{gen}` ({grp}) with **+{added}**")

    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="listgroups", description="List all groups with generator counts")
async def listgroups(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT group_name, COUNT(*) as gen_count, SUM(elements) as total_elements
            FROM generators
            GROUP BY group_name
            ORDER BY group_name
        ''') as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message("No groups found.", ephemeral=True)
        return

    embed = discord.Embed(title="📁 Groups", color=discord.Color.blue())
    for group_name, gen_count, total_elements in rows:
        total_seconds = int((total_elements or 0) * element_time)
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        embed.add_field(
            name=f"📂 {group_name}",
            value=f"Generators: **{gen_count}** ",
            inline=False
        )

    await interaction.response.send_message(embed=embed)



@bot.tree.command(name="listgens", description="List all generators (optionally filtered by group)")
@app_commands.describe(group='Filter by group (optional)')
@app_commands.autocomplete(group=group_autocomplete)
async def listgens(
    interaction: discord.Interaction,
    group: str = None
):
    async with aiosqlite.connect(DB_PATH) as db:
        if group:
            query = 'SELECT gen_name, group_name, elements FROM generators WHERE group_name = ? ORDER BY gen_name'
            params = (group,)
        else:
            query = 'SELECT gen_name, group_name, elements FROM generators ORDER BY group_name, gen_name'
            params = ()

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No generators found{' in **' + group + '**' if group else ''}.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"🔧 Generators{' in ' + group if group else ''}",
        color=discord.Color.blue()
    )

   
    grouped = {}
    for gen_name, group_name, elements in rows:
        grouped.setdefault(group_name, []).append((gen_name, elements))

    for grp, gens in grouped.items():
        lines = "\n".join(f"• **{g}** — {e} elements" for g, e in gens)
        if len(lines) > 1024:
            lines = lines[:1020] + "..."
        embed.add_field(name=f"📂 {grp}", value=lines, inline=False)

    await interaction.response.send_message(embed=embed)

load_dotenv('gen.env')
bot.run(os.getenv('TOKEN'))
