import json
from configparser import ConfigParser
from src.client import CurseforgeClient
from src.loaders import FabricInstaller, NeoForgeInstaller, ForgeInstaller


configs = ConfigParser()
configs.read("cfg.ini")

cli = CurseforgeClient(
    configs.get("DEFAULT", "API_KEY"),
    configs.get("DEFAULT", "BASE_URL"),
    configs.get("DEFAULT", "GAME_ID"),
)

loader = NeoForgeInstaller()
# results = cli.get_minecraft_loaders("1.12.2", include_all=True)

# for result in results:
#     print(result.name)

success = loader.install("1.20.2", "20.2.3-beta", ".minecraft")
print(success)
