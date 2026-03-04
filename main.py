from configparser import ConfigParser
from cursecraft import CurseCraft


configs = ConfigParser()
configs.read("cfg.ini")

craft = CurseCraft(configs)

craft.install_modpack(389615, "G:\\Minecraft")