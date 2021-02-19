import asyncio
import contextlib
import datetime
from datetime import timezone
from random import randint # Embed Colours 
from collections import namedtuple
from copy import copy
from typing import Union, Optional, Literal

import discord

from redbot.cogs.warnings.helpers import (
    warning_points_add_check,
    get_command_for_exceeded_points,
    get_command_for_dropping_points,
    warning_points_remove_check,
)
from discord.ext.commands import max_concurrency, BucketType
from redbot.core import Config, checks, commands, modlog
from redbot.core.bot import Red
from redbot.core.commands import UserInputOptional
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import warning, pagify
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS


_ = Translator("Warnings", __file__)


@cog_i18n(_)
class Warnings(commands.Cog):
    """Warn misbehaving users and take automated actions."""

    default_guild = {
        "actions": [],
        "reasons": {},
        "allow_custom_reasons": False,
        "toggle_dm": True,
        "show_mod": False,
        "warn_channel": None,
        "toggle_channel": False,
    }

    default_member = {"total_points": 0, "status": "", "warnings": {}}

    def __init__(self, bot: Red):
        super().__init__()
        self.config = Config.get_conf(self, identifier=1426112320)
        self.config.register_guild(**self.default_guild)
        self.config.register_member(**self.default_member)
        self.bot = bot
        self.registration_task = self.bot.loop.create_task(self.register_warningtype())

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        if requester != "discord_deleted_user":
            return

        all_members = await self.config.all_members()

        c = 0

        for guild_id, guild_data in all_members.items():
            c += 1
            if not c % 100:
                await asyncio.sleep(0)

            if user_id in guild_data:
                await self.config.member_from_ids(guild_id, user_id).clear()

            for remaining_user, user_warns in guild_data.items():
                c += 1
                if not c % 100:
                    await asyncio.sleep(0)

                for warn_id, warning in user_warns.get("warnings", {}).items():
                    c += 1
                    if not c % 100:
                        await asyncio.sleep(0)

                    if warning.get("mod", 0) == user_id:
                        grp = self.config.member_from_ids(guild_id, remaining_user)
                        await grp.set_raw("warnings", warn_id, "mod", value=0xDE1)

    # We're not utilising modlog yet - no need to register a casetype
    @staticmethod
    async def register_warningtype():
        casetypes_to_register = [
            {
                "name": "warning",
                "default_setting": True,
                "image": "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}",
                "case_str": "Warning",
            },
            {
                "name": "unwarned",
                "default_setting": True,
                "image": "\N{WARNING SIGN}\N{VARIATION SELECTOR-16}",
                "case_str": "Unwarned",
            },
        ]
        try:
            await modlog.register_casetypes(casetypes_to_register)
        except RuntimeError:
            pass

    @commands.group()
    @commands.guild_only()
    @checks.guildowner_or_permissions(administrator=True)
    async def warningset74(self, ctx: commands.Context):
        """Manage settings for Warnings."""
        pass

    @warningset.command()
    @commands.guild_only()
    async def allowcustomreasons74(self, ctx: commands.Context, allowed: bool):
        """Enable or disable custom reasons for a warning."""
        guild = ctx.guild
        await self.config.guild(guild).allow_custom_reasons.set(allowed)
        if allowed:
            await ctx.send(_("Custom reasons have been enabled."))
        else:
            await ctx.send(_("Custom reasons have been disabled."))

    @warningset.command()
    @commands.guild_only()
    async def senddm74(self, ctx: commands.Context, true_or_false: bool):
        """Set whether warnings should be sent to users in DMs."""
        await self.config.guild(ctx.guild).toggle_dm.set(true_or_false)
        if true_or_false:
            await ctx.send(_("I will now try to send warnings to users DMs."))
        else:
            await ctx.send(_("Warnings will no longer be sent to users DMs."))

    @warningset.command()
    @commands.guild_only()
    async def showmoderator74(self, ctx, true_or_false: bool):
        """Decide whether the name of the moderator warning a user should be included in the DM to that user."""
        await self.config.guild(ctx.guild).show_mod.set(true_or_false)
        if true_or_false:
            await ctx.send(
                _(
                    "I will include the name of the moderator who issued the warning when sending a DM to a user."
                )
            )
        else:
            await ctx.send(
                _(
                    "I will not include the name of the moderator who issued the warning when sending a DM to a user."
                )
            )

    @warningset.command()
    @commands.guild_only()
    async def warnchannel74(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set the channel where warnings should be sent to.

        Leave empty to use the channel `[p]warn` command was called in.
        """
        guild = ctx.guild
        if channel:
            await self.config.guild(guild).warn_channel.set(channel.id)
            await ctx.send(
                _("The warn channel has been set to {channel}.").format(channel=channel.mention)
            )
        else:
            await self.config.guild(guild).warn_channel.set(channel)
            await ctx.send(_("Warnings will now be sent in the channel command was used in."))

    @warningset.command()
    @commands.guild_only()
    async def usewarnchannel74(self, ctx: commands.Context, true_or_false: bool):
        """
        Set if warnings should be sent to a channel set with `[p]warningset warnchannel`.
        """
        await self.config.guild(ctx.guild).toggle_channel.set(true_or_false)
        channel = self.bot.get_channel(await self.config.guild(ctx.guild).warn_channel())
        if true_or_false:
            if channel:
                await ctx.send(
                    _("Warnings will now be sent to {channel}.").format(channel=channel.mention)
                )
            else:
                await ctx.send(_("Warnings will now be sent in the channel command was used in."))
        else:
            await ctx.send(_("Toggle channel has been disabled."))

    @commands.group()
    @commands.guild_only()
    @checks.guildowner_or_permissions(administrator=True)
    async def warnaction74(self, ctx: commands.Context):
        """Manage automated actions for Warnings.

        Actions are essentially command macros. Any command can be run
        when the action is initially triggered, and/or when the action
        is lifted.

        Actions must be given a name and a points threshold. When a
        user is warned enough so that their points go over this
        threshold, the action will be executed.
        """
        pass

    @warnaction.command(name="add")
    @commands.guild_only()
    async def action_add(self, ctx: commands.Context, name: str, points: int):
        """Create an automated action.

        Duplicate action names are not allowed.
        """
        guild = ctx.guild

        exceed_command = await get_command_for_exceeded_points(ctx)
        drop_command = await get_command_for_dropping_points(ctx)

        to_add = {
            "action_name": name,
            "points": points,
            "exceed_command": exceed_command,
            "drop_command": drop_command,
        }

        # Have all details for the action, now save the action
        guild_settings = self.config.guild(guild)
        async with guild_settings.actions() as registered_actions:
            for act in registered_actions:
                if act["action_name"] == to_add["action_name"]:
                    await ctx.send(_("Duplicate action name found!"))
                    break
            else:
                registered_actions.append(to_add)
                # Sort in descending order by point count for ease in
                # finding the highest possible action to take
                registered_actions.sort(key=lambda a: a["points"], reverse=True)
                await ctx.send(_("Action {name} has been added.").format(name=name))

    @warnaction.command(name="delete", aliases=["del", "remove"])
    @commands.guild_only()
    async def action_del(self, ctx: commands.Context, action_name: str):
        """Delete the action with the specified name."""
        guild = ctx.guild
        guild_settings = self.config.guild(guild)
        async with guild_settings.actions() as registered_actions:
            to_remove = None
            for act in registered_actions:
                if act["action_name"] == action_name:
                    to_remove = act
                    break
            if to_remove:
                registered_actions.remove(to_remove)
                await ctx.tick()
            else:
                await ctx.send(_("No action named {name} exists!").format(name=action_name))

    @commands.group()
    @commands.guild_only()
    @checks.guildowner_or_permissions(administrator=True)
    async def warnreason74(self, ctx: commands.Context):
        """Manage warning reasons.

        Reasons must be given a name, description and points value. The
        name of the reason must be given when a user is warned.
        """
        pass

    @warnreason.command(name="create", aliases=["add"])
    @commands.guild_only()
    async def reason_create(
        self, ctx: commands.Context, name: str, points: int, *, description: str
    ):
        """Create a warning reason."""
        guild = ctx.guild

        if name.lower() == "custom":
            await ctx.send(_("*Custom* cannot be used as a reason name!"))
            return
        to_add = {"points": points, "description": description}
        completed = {name.lower(): to_add}

        guild_settings = self.config.guild(guild)

        async with guild_settings.reasons() as registered_reasons:
            registered_reasons.update(completed)

        await ctx.send(_("The new reason has been registered."))

    @warnreason.command(name="delete", aliases=["remove", "del"])
    @commands.guild_only()
    async def reason_del(self, ctx: commands.Context, reason_name: str):
        """Delete a warning reason."""
        guild = ctx.guild
        guild_settings = self.config.guild(guild)
        async with guild_settings.reasons() as registered_reasons:
            if registered_reasons.pop(reason_name.lower(), None):
                await ctx.tick()
            else:
                await ctx.send(_("That is not a registered reason name."))

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(ban_members=True)
    async def reasonlist74(self, ctx: commands.Context):
        """List all configured reasons for Warnings."""
        guild = ctx.guild
        guild_settings = self.config.guild(guild)
        msg_list = []
        async with guild_settings.reasons() as registered_reasons:
            for r, v in registered_reasons.items():
                if await ctx.embed_requested():
                    em = discord.Embed(
                        title=_("Reason: {name}").format(name=r),
                        description=v["description"],
                    )
                    em.add_field(name=_("Points"), value=str(v["points"]))
                    msg_list.append(em)
                else:
                    msg_list.append(
                        _(
                            "Name: {reason_name}\nPoints: {points}\nDescription: {description}"
                        ).format(reason_name=r, **v)
                    )
        if msg_list:
            await menu(ctx, msg_list, DEFAULT_CONTROLS)
        else:
            await ctx.send(_("There are no reasons configured!"))

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(ban_members=True)
    async def actionlist74(self, ctx: commands.Context):
        """List all configured automated actions for Warnings."""
        guild = ctx.guild
        guild_settings = self.config.guild(guild)
        msg_list = []
        async with guild_settings.actions() as registered_actions:
            for r in registered_actions:
                if await ctx.embed_requested():
                    em = discord.Embed(title=_("Action: {name}").format(name=r["action_name"]))
                    em.add_field(name=_("Points"), value="{}".format(r["points"]), inline=False)
                    em.add_field(
                        name=_("Exceed command"),
                        value=r["exceed_command"],
                        inline=False,
                    )
                    em.add_field(name=_("Drop command"), value=r["drop_command"], inline=False)
                    msg_list.append(em)
                else:
                    msg_list.append(
                        _(
                            "Name: {action_name}\nPoints: {points}\n"
                            "Exceed command: {exceed_command}\nDrop command: {drop_command}"
                        ).format(**r)
                    )
        if msg_list:
            await menu(ctx, msg_list, DEFAULT_CONTROLS)
        else:
            await ctx.send(_("There are no actions configured!"))

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(ban_members=True)
    async def warn74(
        self,
        ctx: commands.Context,
        user: discord.Member,
        points: UserInputOptional[int] = 1,
        *,
        reason: str,
    ):
        """Warn the user for the specified reason.

        `<points>` number of points the warning should be for. If no number is supplied
        1 point will be given. Pre-set warnings disregard this.
        `<reason>` can be a registered reason if it exists or a custom one
        is created by default.
        """
        guild = ctx.guild
        if user == ctx.author:
            return await ctx.send(_("You cannot warn yourself."))
        if user.bot:
            return await ctx.send(_("You cannot warn other bots."))
        if user == ctx.guild.owner:
            return await ctx.send(_("You cannot warn the server owner."))
        if user.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send(
                _(
                    "The person you're trying to warn is equal or higher than you in the discord hierarchy, you cannot warn them."
                )
            )
        guild_settings = await self.config.guild(ctx.guild).all()
        custom_allowed = guild_settings["allow_custom_reasons"]

        reason_type = None
        async with self.config.guild(ctx.guild).reasons() as registered_reasons:
            if (reason_type := registered_reasons.get(reason.lower())) is None:
                msg = _("That is not a registered reason!")
                if custom_allowed:
                    reason_type = {"description": reason, "points": points}
                else:
                    # logic taken from `[p]permissions canrun`
                    fake_message = copy(ctx.message)
                    fake_message.content = f"{ctx.prefix}warningset allowcustomreasons"
                    fake_context = await ctx.bot.get_context(fake_message)
                    try:
                        can = await self.allowcustomreasons.can_run(
                            fake_context, check_all_parents=True, change_permission_state=False
                        )
                    except commands.CommandError:
                        can = False
                    if can:
                        msg += " " + _(
                            "Do `{prefix}warningset allowcustomreasons true` to enable custom "
                            "reasons."
                        ).format(prefix=ctx.clean_prefix)
                    return await ctx.send(msg)
        if reason_type is None:
            return
        member_settings = self.config.member(user)
        current_point_count = await member_settings.total_points()
        currentTime = str(datetime.date.today())
        warning_to_add = {
            str(ctx.message.id): {
                "points": reason_type["points"],
                "description": reason_type["description"],
                "mod": ctx.author.id,
                "submitTime": currentTime,
            }
        }
        async with member_settings.warnings() as user_warnings:
            user_warnings.update(warning_to_add)
        current_point_count += reason_type["points"]
        await member_settings.total_points.set(current_point_count)

        await warning_points_add_check(self.config, ctx, user, current_point_count)
        dm = guild_settings["toggle_dm"]
        showmod = guild_settings["show_mod"]
        dm_failed = False
        if dm:
            if showmod:
                title = _("Warning from {user}").format(user=ctx.author)
            else:
                title = _("Warning")
            em = discord.Embed(
                title=title,
                description=reason_type["description"],
            )
            em.add_field(name=_("Points"), value=str(reason_type["points"]))
            try:
                await user.send(
                    _("You have received a warning in {guild_name}.").format(
                        guild_name=ctx.guild.name
                    ),
                    embed=em,
                )
            except discord.HTTPException:
                dm_failed = True

        if dm_failed:
            await ctx.send(
                _(
                    "A warning for {user} has been issued,"
                    " but I wasn't able to send them a warn message."
                ).format(user=user.mention)
            )

        toggle_channel = guild_settings["toggle_channel"]
        if toggle_channel:
            if showmod:
                title = _("Warning from {user}").format(user=ctx.author)
            else:
                title = _("Warning")
            em = discord.Embed(
                title=title,
                description=reason_type["description"],
            )
            em.add_field(name=_("Points"), value=str(reason_type["points"]))
            warn_channel = self.bot.get_channel(guild_settings["warn_channel"])
            if warn_channel:
                if warn_channel.permissions_for(guild.me).send_messages:
                    with contextlib.suppress(discord.HTTPException):
                        await warn_channel.send(
                            _("{user} has been warned.").format(user=user.mention),
                            embed=em,
                        )

            if not dm_failed:
                if warn_channel:
                    await ctx.tick()
                else:
                    await ctx.send(
                        _("{user} has been warned.").format(user=user.mention), embed=em
                    )
        else:
            if not dm_failed:
                await ctx.tick()
        reason_msg = _(
            "{reason}\n\nUse `{prefix}unwarn {user} {message}` to remove this warning."
        ).format(
            reason=_("{description}\nPoints: {points}").format(
                description=reason_type["description"], points=reason_type["points"]
            ),
            prefix=ctx.clean_prefix,
            user=user.id,
            message=ctx.message.id,
        )
        await modlog.create_case(
            self.bot,
            ctx.guild,
            ctx.message.created_at.replace(tzinfo=timezone.utc),
            "warning",
            user,
            ctx.message.author,
            reason_msg,
            until=None,
            channel=None,
        )

    @commands.command()
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages = True)
    @max_concurrency(1, per=BucketType.guild, wait=False)
    async def warnings74(self, ctx: commands.Context, user: Union[discord.Member, int] = None):
        """List the warnings for the specified user."""
        if user is None:
            user = ctx.message.author
        try:
            userid: int = user.id
        except AttributeError:
            userid: int = user
            user = ctx.guild.get_member(userid)
            user = user or namedtuple("Member", "id guild")(userid, ctx.guild)

        try:
            avatar = user.avatar_url_as(static_format="png")
        except:
            avatar = "https://i.imgur.com/azpo3Dc.png"

        embeds = []
        count = 1

        member_settings = self.config.member(user)
        async with member_settings.warnings() as user_warnings:
            if not user_warnings.keys():  # no warnings for the user
                await ctx.send(_("That user has no warnings!"))
            else:
                for key in user_warnings.keys():
                    EmColour = randint(0, 0xffffff)
                    em_ToSend = discord.Embed(title=f"Warnings for {user}", color=EmColour)
                    em_ToSend.set_thumbnail(url=avatar)
                    warningTxt = _(
                        "{num_points} point warning {reason_name} issued for "
                        "{description}\n"
                    ).format(
                        num_points=user_warnings[key]["points"],
                        reason_name=key,
                        description=user_warnings[key]["description"],
                    )
                    em_ToSend.add_field(name=f"Warning #{count}", value=warningTxt, inline=False)
                    unwarnTxt = _(
                        "`{prefix}unwarn {userid} {reason_name}`"
                    ).format(
                            prefix = ctx.prefix,
                            reason_name=key,
                            userid = user.id,
                        )
                    em_ToSend.add_field(name=f"Unwarn command", value=unwarnTxt, inline=False)

                    try:
                        warnTime = user_warnings[key]["submitTime"]
                        warnTimeDate = datetime.date.fromisoformat(warnTime)
                        if warnTimeDate == datetime.date.today():
                            elapsed = "(Today)"
                        else:
                            diff = datetime.date.today() - warnTimeDate
                            if diff.days < 14:
                                elapsed = f"({str(diff.days)} days ago)"
                            if diff.days >= 14:
                                diffy = diff.days / 7
                                diffy = int(diffy)
                                elapsed = f"({str(diffy)} weeks ago)"
                        em_ToSend.add_field(name=f"Warning Date", value=f"{warnTimeDate} {elapsed}", inline=False)

                    except:
                        warnTimeDate = "No Warn Time Supplied."
                        em_ToSend.add_field(name=f"Warning Date", value=f"{warnTimeDate}", inline=False)
                    embeds.append(em_ToSend)
                    count += 1
                await menu(ctx, embeds, DEFAULT_CONTROLS, timeout=6)




    @commands.command()
    @commands.guild_only()
    async def mywarnings74(self, ctx: commands.Context):
        """List warnings for yourself."""

        user = ctx.author

        msg = ""
        member_settings = self.config.member(user)
        async with member_settings.warnings() as user_warnings:
            if not user_warnings.keys():  # no warnings for the user
                await ctx.send(_("You have no warnings!"))
            else:
                for key in user_warnings.keys():
                    mod_id = user_warnings[key]["mod"]
                    if mod_id == 0xDE1:
                        mod = _("Deleted Moderator")
                    else:
                        bot = ctx.bot
                        mod = bot.get_user(mod_id) or _("Unknown Moderator ({})").format(mod_id)
                    msg += _(
                        "{num_points} point warning {reason_name} issued for "
                        "{description}\n"
                    ).format(
                        num_points=user_warnings[key]["points"],
                        reason_name=key,
                        description=user_warnings[key]["description"],
                    )
                await ctx.send_interactive(
                    pagify(msg, shorten_by=58),
                    box_lang=_("Warnings for {user}").format(user=user),
                )

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(ban_members=True)
    async def unwarn74(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, int],
        warn_id: str,
        *,
        reason: str = None,
    ):
        """Remove a warning from a user."""

        guild = ctx.guild

        try:
            user_id = user.id
            member = user
        except AttributeError:
            user_id = user
            member = guild.get_member(user_id)
            member = member or namedtuple("Member", "guild id")(guild, user_id)

        if user_id == ctx.author.id:
            return await ctx.send(_("You cannot remove warnings from yourself."))

        member_settings = self.config.member(member)
        current_point_count = await member_settings.total_points()
        await warning_points_remove_check(self.config, ctx, member, current_point_count)
        async with member_settings.warnings() as user_warnings:
            if warn_id not in user_warnings.keys():
                return await ctx.send(_("That warning doesn't exist!"))
            else:
                current_point_count -= user_warnings[warn_id]["points"]
                await member_settings.total_points.set(current_point_count)
                user_warnings.pop(warn_id)
        await modlog.create_case(
            self.bot,
            ctx.guild,
            ctx.message.created_at.replace(tzinfo=timezone.utc),
            "unwarned",
            member,
            ctx.message.author,
            reason,
            until=None,
            channel=None,
        )

        await ctx.tick()
