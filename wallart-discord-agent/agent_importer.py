from __future__ import annotations
import os, asyncio, logging
from typing import Dict, Any, Optional
import yaml, requests, discord
from discord import app_commands
from discord.ext import commands, tasks

# .env loader
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('agent')

TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID', '0'))
AGENT_SOURCE_URL = os.getenv('AGENT_SOURCE_URL')
AGENT_POLL_SEC = int(os.getenv('AGENT_POLL_SEC', '600'))
TEAM_ROLE_NAME = os.getenv('TEAM_ROLE_NAME', 'Team')
BOT_ROLE_NAME = os.getenv('BOT_ROLE_NAME', 'Bot')

if not TOKEN:
    raise SystemExit('DISCORD_TOKEN fehlt.')

intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents)

class SourceCache:
    def __init__(self):
        self.etag = None
        self.last_modified = None
        self.last_text = None
CACHE = SourceCache()

def load_blueprint(source: str) -> Dict[str, Any]:
    if source.startswith('http://') or source.startswith('https://'):
        headers = {}
        if CACHE.etag: headers['If-None-Match'] = CACHE.etag
        if CACHE.last_modified: headers['If-Modified-Since'] = CACHE.last_modified
        r = requests.get(source, headers=headers, timeout=15)
        if r.status_code == 304:
            text = CACHE.last_text or ''
        else:
            r.raise_for_status()
            text = r.text
            CACHE.last_text = text
            CACHE.etag = r.headers.get('ETag', CACHE.etag)
            CACHE.last_modified = r.headers.get('Last-Modified', CACHE.last_modified)
    elif source.startswith('file://'):
        with open(source[7:], 'r', encoding='utf-8') as f: text = f.read()
    else:
        with open(source, 'r', encoding='utf-8') as f: text = f.read()
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict): raise ValueError('Blueprint YAML muss ein Mapping sein.')
    return data

async def ensure_role(guild: discord.Guild, name: str) -> discord.Role:
    role = discord.utils.get(guild.roles, name=name)
    if role: return role
    log.info(f'Create role: {name}')
    return await guild.create_role(name=name, mentionable=True)

async def ensure_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    cat = discord.utils.get(guild.categories, name=name)
    if cat: return cat
    log.info(f'Create category: {name}')
    return await guild.create_category(name)

async def ensure_text_channel(guild, category, name, topic, flags, team, bot_role):
    ch = discord.utils.get(guild.text_channels, name=name)
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
    readonly = bool(flags.get('readonly')); staff_only = bool(flags.get('staff_only')); allow_bot_post = bool(flags.get('allow_bot_post'))
    if readonly:
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        if team: overwrites[team] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        if allow_bot_post and bot_role: overwrites[bot_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if staff_only:
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        if team: overwrites[team] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)
        if bot_role: overwrites[bot_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if ch:
        await ch.edit(category=category, topic=topic or ch.topic, overwrites=overwrites); return ch
    log.info(f'Create text channel: #{name}')
    return await guild.create_text_channel(name=name, category=category, topic=topic, overwrites=overwrites)

async def ensure_voice_channel(guild, category, name):
    ch = discord.utils.get(guild.voice_channels, name=name)
    if ch: await ch.edit(category=category); return ch
    log.info(f'Create voice channel: 🔊 {name}'); return await guild.create_voice_channel(name=name, category=category)

async def ensure_stage_channel(guild, category, name):
    ch = discord.utils.get(guild.channels, name=name)
    if ch:
        try: await ch.edit(category=category)
        except Exception: pass
        return ch
    log.info(f'Create stage channel: 🎙 {name}')
    try: return await guild.create_stage_channel(name=name, category=category)
    except AttributeError: return await guild.create_voice_channel(name=name, category=category)

async def apply_blueprint(guild, blueprint):
    summary = {'roles_created':0,'categories_created':0,'channels_created':0,'channels_updated':0}
    roles = blueprint.get('roles', []) or []
    role_map = {}
    for rname in roles:
        r = await ensure_role(guild, rname); role_map[rname]=r; await asyncio.sleep(0.2)
        if rname not in [x.name for x in guild.roles]: summary['roles_created']+=1
    team = role_map.get(TEAM_ROLE_NAME) or discord.utils.get(guild.roles, name=TEAM_ROLE_NAME)
    bot_role = role_map.get(BOT_ROLE_NAME) or discord.utils.get(guild.roles, name=BOT_ROLE_NAME)
    for cat in blueprint.get('categories', []) or []:
        cname = cat.get('name'); 
        if not cname: continue
        category = await ensure_category(guild, cname)
        if category.name not in [c.name for c in guild.categories]: summary['categories_created']+=1
        await asyncio.sleep(0.2)
        for item in cat.get('channels', []) or []:
            ch_name = item.get('name'); ch_type = (item.get('type') or 'text').lower(); topic = item.get('topic'); flags = item.get('flags', {}) or {}
            if ch_type == 'text':
                before = discord.utils.get(guild.text_channels, name=ch_name)
                await ensure_text_channel(guild, category, ch_name, topic, flags, team, bot_role); await asyncio.sleep(0.3)
                summary['channels_updated' if before else 'channels_created'] += 1
            elif ch_type == 'voice':
                before = discord.utils.get(guild.voice_channels, name=ch_name)
                await ensure_voice_channel(guild, category, ch_name); await asyncio.sleep(0.3)
                summary['channels_updated' if before else 'channels_created'] += 1
            elif ch_type == 'stage':
                before = discord.utils.get(guild.channels, name=ch_name)
                await ensure_stage_channel(guild, category, ch_name); await asyncio.sleep(0.3)
                summary['channels_updated' if before else 'channels_created'] += 1
    return summary

async def dryrun_blueprint(guild, blueprint):
    lines = ['Dry-Run Plan:']
    role_names = [r.name for r in guild.roles]
    for r in blueprint.get('roles', []) or []:
        if r not in role_names: lines.append(f'+ create role: {r}')
    cat_names = [c.name for c in guild.categories]
    for cat in blueprint.get('categories', []) or []:
        cname = cat.get('name')
        if cname not in cat_names: lines.append(f'+ create category: {cname}')
        for item in cat.get('channels', []) or []:
            ch_name = item.get('name'); ch_type = (item.get('type') or 'text').lower()
            if ch_type == 'text': ch = discord.utils.get(guild.text_channels, name=ch_name)
            elif ch_type == 'voice': ch = discord.utils.get(guild.voice_channels, name=ch_name)
            else: ch = discord.utils.get(guild.channels, name=ch_name)
            lines.append(('+ create ' if not ch else '~ update ') + f'{ch_type} channel: {cname}/#{ch_name}')
    return '\n'.join(lines)

@bot.event
async def on_ready():
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)
    if guild is None: log.error('Bot ist auf keinem Guild oder GUILD_ID falsch.')
    else:
        log.info(f'Ready as {bot.user} on {guild.name}')
        try: synced = await bot.tree.sync(guild=guild); log.info(f'Slash-Commands synced: {len(synced)}')
        except Exception as e: log.error(f'Command sync failed: {e}')
    if AGENT_SOURCE_URL: agent_poll.start()

@bot.tree.command(name='dryrun', description='Zeigt, was geändert würde, ohne es anzuwenden.')
@app_commands.describe(source='YAML-Quelle (URL oder Pfad). Wenn leer: AGENT_SOURCE_URL oder default.yaml')
async def dryrun(interaction: discord.Interaction, source: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    src = source or AGENT_SOURCE_URL or 'blueprint.yaml'; bp = load_blueprint(src)
    plan = await dryrun_blueprint(interaction.guild, bp)
    await interaction.followup.send(f'Quelle: `{src}`\n```{plan}```', ephemeral=True)

@bot.tree.command(name='sync', description='Wendet den Blueprint auf den Server an (keine Löschungen).')
@app_commands.describe(source='YAML-Quelle (URL oder Pfad). Wenn leer: AGENT_SOURCE_URL oder default.yaml')
async def sync(interaction: discord.Interaction, source: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    src = source or AGENT_SOURCE_URL or 'blueprint.yaml'; bp = load_blueprint(src)
    summary = await apply_blueprint(interaction.guild, bp)
    await interaction.followup.send(f'Quelle: `{src}`\n**Ergebnis:** {summary}', ephemeral=True)

@bot.tree.command(name='audit', description='Kurzer Drift-Report vs. Blueprint.')
@app_commands.describe(source='YAML-Quelle (URL oder Pfad). Wenn leer: AGENT_SOURCE_URL oder default.yaml')
async def audit(interaction: discord.Interaction, source: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    src = source or AGENT_SOURCE_URL or 'blueprint.yaml'; bp = load_blueprint(src)
    plan = await dryrun_blueprint(interaction.guild, bp)
    await interaction.followup.send(f'Quelle: `{src}`\n```{plan}```', ephemeral=True)

@bot.tree.command(name='lockdown', description='Sperrt alle nicht-internen Textkanäle für @everyone (lesen ja, schreiben nein).')
async def lockdown(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    changed = 0
    for ch in interaction.guild.text_channels:
        if ch.category and ch.category.name.startswith('🔒 '): continue
        ow = ch.overwrites_for(interaction.guild.default_role)
        if ow.send_messages is not False:
            ow.send_messages = False
            await ch.set_permissions(interaction.guild.default_role, overwrite=ow)
            changed += 1; await asyncio.sleep(0.2)
    await interaction.followup.send(f'Lockdown aktiv. Kanäle geändert: {changed}', ephemeral=True)

@bot.tree.command(name='unlock', description='Hebt Lockdown auf.')
async def unlock(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    changed = 0
    for ch in interaction.guild.text_channels:
        ow = ch.overwrites_for(interaction.guild.default_role)
        if ow.send_messages is False:
            ow.send_messages = None
            await ch.set_permissions(interaction.guild.default_role, overwrite=ow)
            changed += 1; await asyncio.sleep(0.2)
    await interaction.followup.send(f'Unlock durchgeführt. Kanäle geändert: {changed}', ephemeral=True)

@tasks.loop(seconds=AGENT_POLL_SEC)
async def agent_poll():
    if not AGENT_SOURCE_URL: return
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)
    if not guild: return
    try:
        bp = load_blueprint(AGENT_SOURCE_URL)
        log.info('Agent: applying blueprint diffs…')
        summary = await apply_blueprint(guild, bp)
        log.info(f'Agent summary: {summary}')
    except Exception as e:
        log.error(f'Agent error: {e}')

if __name__ == '__main__':
    bot.run(TOKEN)
