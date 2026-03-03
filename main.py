import json
from configparser import ConfigParser
from src.client import CurseforgeClient
from src.loaders import FabricInstaller, NeoForgeInstaller


configs = ConfigParser()
configs.read("cfg.ini")

cli = CurseforgeClient(
    configs.get("DEFAULT", "API_KEY"),
    configs.get("DEFAULT", "BASE_URL"),
    configs.get("DEFAULT", "GAME_ID"),
)

# res = cli.get_minecraft_loaders("1.21.10", include_all=True)
# for loader in res:
#     print(loader.name, loader.game_version)

loader = NeoForgeInstaller()
loader.install("1.21.11", "21.11.38-beta", ".minecraft/")
# /mnt/c/Users/Pengy/AppData/Roaming/.minecraft/
# content = loader._generate_version_file("1.21.11", "21.11.38-beta", "neoforge")
# with open("version.json" ,"w") as file:
#     file.write(json.dumps(content, indent=2))

# mod = cli.get_mod(925200)
# cli.download_modpacks(925200, mode="complete")
