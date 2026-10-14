"""The app catalogue (roadmap #3): common apps the parent can schedule, grouped into categories.

Kept on the server so it can grow without reinstalling anything on the PC: it travels to the PC in the
state snapshot. Each app has matchers; an app matches a running program if ANY matcher matches, and a
matcher matches if ALL its fields do:

  exe      program file name, or the original file name stored inside the program, so a renamed
           copy (Minecraft.exe -> homework.exe) still matches
  product  text in the program's product name
  company  text in the publisher name
  path     text in the full path (e.g. every Steam game lives under \\steamapps\\common\\)
  cmdline  text in the command line (Minecraft Java runs on a generic javaw.exe)

All comparisons ignore case.
"""

from dataclasses import dataclass, field
from typing import Any

CATEGORIES: dict[str, str] = {
    "games": "Games",
    "browsers": "Browsers",
    "chat": "Chat and social",
    "video": "Video and music",
}


@dataclass(frozen=True)
class CatalogApp:
    id: str
    name: str
    category: str
    matchers: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def to_agent(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "category": self.category, "matchers": list(self.matchers)}


def _app(id: str, name: str, category: str, *matchers: dict[str, str]) -> CatalogApp:
    return CatalogApp(id, name, category, tuple(matchers))


CATALOG: tuple[CatalogApp, ...] = (
    # Games
    _app("minecraft_java", "Minecraft (Java)", "games",
         {"exe": "javaw.exe", "cmdline": "net.minecraft"}, {"exe": "java.exe", "cmdline": "net.minecraft"},
         {"exe": "MinecraftLauncher.exe"}, {"product": "Minecraft Launcher"}),
    _app("minecraft_bedrock", "Minecraft (Bedrock)", "games", {"exe": "Minecraft.Windows.exe"}),
    _app("roblox", "Roblox", "games", {"exe": "RobloxPlayerBeta.exe"}, {"product": "Roblox Player"},
         {"exe": "Windows10Universal.exe", "path": "roblox"}),
    _app("fortnite", "Fortnite", "games", {"exe": "FortniteClient-Win64-Shipping.exe"}, {"exe": "FortniteLauncher.exe"}),
    _app("epic", "Epic Games Launcher", "games", {"exe": "EpicGamesLauncher.exe"}),
    _app("steam_games", "Steam games", "games", {"path": "\\steamapps\\common\\"}),
    _app("steam", "Steam", "games", {"exe": "steam.exe", "company": "Valve"}, {"exe": "steamwebhelper.exe"}),
    _app("xbox", "Xbox app", "games", {"exe": "XboxPcApp.exe"}),
    _app("xbox_games", "Xbox and Game Pass games", "games", {"path": "\\XboxGames\\"}),
    _app("battlenet", "Battle.net", "games", {"exe": "Battle.net.exe"}),
    _app("ea", "EA app", "games", {"exe": "EADesktop.exe"}),
    _app("riot", "Riot Client (Valorant, League of Legends)", "games", {"exe": "RiotClientServices.exe"},
         {"exe": "VALORANT-Win64-Shipping.exe"}, {"exe": "League of Legends.exe"}),
    # Browsers
    _app("chrome", "Google Chrome", "browsers", {"exe": "chrome.exe"}),
    _app("edge", "Microsoft Edge", "browsers", {"exe": "msedge.exe"}),
    _app("firefox", "Firefox", "browsers", {"exe": "firefox.exe"}),
    _app("brave", "Brave", "browsers", {"exe": "brave.exe"}),
    _app("opera", "Opera", "browsers", {"exe": "opera.exe"}, {"product": "Opera"}),
    # Chat and social
    _app("discord", "Discord", "chat", {"exe": "Discord.exe"}),
    _app("whatsapp", "WhatsApp", "chat", {"exe": "WhatsApp.exe"}, {"exe": "WhatsApp.Root.exe"}),
    _app("tiktok", "TikTok", "chat", {"exe": "TikTok.exe"}, {"product": "TikTok"}),
    _app("telegram", "Telegram", "chat", {"exe": "Telegram.exe"}),
    _app("instagram", "Instagram", "chat", {"exe": "Instagram.exe"}),
    # Video and music
    _app("netflix", "Netflix", "video", {"exe": "Netflix.exe"}, {"path": "\\4DF9E0F8.Netflix_"}),
    _app("spotify", "Spotify", "video", {"exe": "Spotify.exe"}),
    _app("disney_plus", "Disney+", "video", {"path": "Disney.37853FC22B2CE_"}),
    _app("twitch", "Twitch", "video", {"exe": "Twitch.exe"}),
)

CATALOG_BY_ID = {a.id: a for a in CATALOG}

MATCHER_FIELDS = ("exe", "product", "company", "path", "cmdline")
