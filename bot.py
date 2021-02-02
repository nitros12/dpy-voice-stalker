from __future__ import annotations

import os
import csv
import enum
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from typing import Dict, Iterable, List, Optional

import discord
import matplotlib
import matplotlib.pyplot as plt
from discord.ext import commands
from matplotlib.collections import PolyCollection
from matplotlib.dates import AutoDateFormatter, HourLocator, date2num

#matplotlib.use('SVG')
matplotlib.rc("font", family="Noto Sans", size=4)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="dpyvc!", intents=intents)


class StalkType(enum.Enum):
    join = "join"
    leave = "leave"

joined_re = re.compile(r"<@!?(?P<uid>\d+)> joined \*\*(?P<chan>\w+)\*\*.")
left_re = re.compile(r"<@!?(?P<uid>\d+)> left \*\*(?P<chan>\w+)\*\*.")
moved_re = re.compile(r"<@!?(?P<uid>\d+)> moved from \*\*(?P<from_chan>\w+)\*\* to \*\*(?P<to_chan>\w+)\*\*.")


@dataclass(frozen=True)
class StalkEvent:
    who: int
    when: datetime
    action: StalkType

    @classmethod
    async def from_embed(cls, emb: discord.Embed, target_channel: str) -> Optional[StalkEvent]:
        d = emb.description

        if isinstance(d, discord.Embed.Empty.__class__):
            return None

        if (m := joined_re.match(d)) is not None:
            uid, chan = m.groups()
            type = StalkType.join
        elif (m := left_re.match(d)) is not None:
            uid, chan = m.groups()
            type = StalkType.leave
        elif (m := moved_re.match(d)) is not None:
            uid, from_chan, dest_chan = m.groups()
            if dest_chan == target_channel:
                type = StalkType.join
                chan = dest_chan
            elif from_chan == target_channel:
                type = StalkType.leave
                chan = dest_chan
            else:
                return None
        else:
            return None

        if chan != target_channel:
            return None

        if isinstance(emb.timestamp, discord.Embed.Empty.__class__):
            return None

        return cls(who=int(uid), action=type, when=emb.timestamp)


@dataclass(frozen=True)
class ConnectedDuration:
    who: int
    joined: datetime
    left: datetime

def process_events(evts: Iterable[StalkEvent], start_time: datetime, end_time: datetime) -> List[ConnectedDuration]:
    looking_at: Dict[int, datetime] = {}
    durations = []

    seen = set()

    for evt in evts:
        if evt.action == StalkType.join:
            looking_at[evt.who] = evt.when
        elif evt.who in looking_at:
            join = looking_at.pop(evt.who)
            durations.append(ConnectedDuration(evt.who, join, evt.when))
        elif evt.who not in seen:
            durations.append(ConnectedDuration(evt.who, start_time, evt.when))

        seen.add(evt.who)

    for user, join in looking_at.items():
        if (end_time - join).total_seconds() > (60 * 60 * 12):
            # imagine
            continue
        durations.append(ConnectedDuration(user, join, end_time))

    return durations


def counter():
    i = 0

    def increment():
        nonlocal i
        i += 1
        return i

    return increment


@bot.command()
async def stalk_csv(ctx: commands.Context, stalking_channel: discord.TextChannel, channel_name: str, delta_hours: int):
    """Generate a plot of when people were in a vc given a stalking channel to read from."""
    now = datetime.utcnow()
    start_time = now - timedelta(hours=delta_hours)

    events = []

    async for message in stalking_channel.history(limit=None, after=start_time):
        if message.author.id != 641596449355726858:
            continue

        if not message.embeds:
            continue

        stalk_embed = message.embeds[0]

        event = await StalkEvent.from_embed(stalk_embed, channel_name)
        if event is None:
            continue

        events.append(event)

    durations = process_events(events, start_time, now)
    durations = [d for d in durations if (d.left - d.joined).total_seconds() > 600]

    b = StringIO()

    writer = csv.DictWriter(b, fieldnames=("user_id", "joined", "left"))
    writer.writeheader()

    for duration in durations:
        writer.writerow({"user_id": str(duration.who),
                         "joined": duration.joined.isoformat(),
                         "left": duration.left.isoformat()})

    b.seek(0)

    await ctx.send(file=discord.File(b, filename="plot.csv"))


@bot.command()
async def stalk_plot(ctx: commands.Context, stalking_channel: discord.TextChannel, channel_name: str, delta_hours: int):
    """Generate a plot of when people were in a vc given a stalking channel to read from."""

    now = datetime.utcnow()
    start_time = now - timedelta(hours=delta_hours)

    events = []

    async for message in stalking_channel.history(limit=None, after=start_time):
        if message.author.id != 641596449355726858:
            continue

        if not message.embeds:
            continue

        stalk_embed = message.embeds[0]

        event = await StalkEvent.from_embed(stalk_embed, channel_name)
        if event is None:
            continue

        events.append(event)

    durations = process_events(events, start_time, now)
    durations = [d for d in durations if (d.left - d.joined).total_seconds() > 600]

    categories = defaultdict(counter())
    seen_names = set()
    name_order = []
    vertices = []
    colours = []

    print("a")

    for duration in durations:
        if duration.who not in seen_names:
            user = bot.get_user(duration.who)
            if user is None:
                continue
            seen_names.add(duration.who)
            name_order.append(str(user))
        category = categories[duration.who]
        colour = f"C{category}"

        v = [
            (date2num(duration.joined), category - 0.4),
            (date2num(duration.joined), category + 0.4),
            (date2num(duration.left),   category + 0.4),
            (date2num(duration.left),   category - 0.4),
            (date2num(duration.joined), category - 0.4),
        ]

        vertices.append(v)
        colours.append(colour)

    print("b")

    bars = PolyCollection(vertices, facecolors=colours)

    fig, ax = plt.subplots()
    ax.add_collection(bars)
    ax.autoscale()

    loc = HourLocator(byhour=range(0, 24, 6))
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(AutoDateFormatter(loc))

    ax.set_yticks(list(categories.values()))
    ax.set_yticklabels(name_order)

    b = BytesIO()
    plt.savefig(b, dpi=800)
    b.seek(0)

    await ctx.send(file=discord.File(b, filename="plot.png"))

bot.run(os.getenv("TOKEN"))
