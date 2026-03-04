import os, json, zipfile, io, requests, subprocess
from .models import BaseClientModel
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List, Union


class ModLoaderInstaller(BaseClientModel):
    def __init__(
        self,
        api_base_url: str,
        max_workers: int = 5,
    ):
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3 Safari/605.1.15"
        }
        super(ModLoaderInstaller, self).__init__(
            self._headers, api_base_url, max_workers
        )
        self._minecraft_manifest = requests.request(
            "GET", "https://launchermeta.mojang.com/mc/game/version_manifest.json"
        ).json()

    def _set_paths(
        self, version_name: str, minecraft_dir_path: Optional[str] = None
    ) -> None:
        if minecraft_dir_path is None:
            self.minecraft_dir_path = self._get_minecraft_dir_path()
        self.libraries_path = os.path.join(minecraft_dir_path, "libraries")
        self.versions_path = os.path.join(minecraft_dir_path, "versions")
        self.profile_path = os.path.join(self.versions_path, version_name)
        self.profile_json_file = os.path.join(self.profile_path, f"{version_name}.json")

    def _write_version_file(self, data: Dict[str, Any]) -> bool:
        try:
            os.makedirs(self.profile_path, exist_ok=True)
            with open(self.profile_json_file, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            return False
        return True

    def _get_minecraft_dir_path() -> str:
        home_path = Path.home()
        if os.name == "nt":
            appdata = os.getenv("APPDATA")
            minecraft_dir_path = (
                os.path.join(appdata, ".minecraft")
                if appdata
                else os.path.join(str(home_path), ".minecraft")
            )
        elif os.name == "posix":
            if os.path.exists(
                os.path.join(
                    str(home_path), "Library", "Application Support", "minecraft"
                )
            ):
                minecraft_dir_path = os.path.join(
                    str(home_path), "Library", "Application Support", "minecraft"
                )
            else:
                minecraft_dir_path = os.path.join(str(home_path), ".minecraft")
        else:
            minecraft_dir_path = os.path.join(str(home_path), ".minecraft")
        return minecraft_dir_path

    def _resolve_maven_coord(self, coord: str) -> str:
        parts = coord.replace("[", "").replace("]", "").split(":")
        if len(parts) < 3:
            raise ValueError(f"Invalid maven coord: {coord}")
        group = parts[0]
        artifact = parts[1]
        version = parts[2]
        classifier = parts[3] if len(parts) > 3 else None
        extension = "jar"

        if classifier and "@" in classifier:
            classifier, extension = classifier.split("@")

        group_path = group.replace(".", "/")
        filename = f"{artifact}-{version}"
        if classifier:
            filename += f"-{classifier}"
        filename += f".{extension}"

        return f"{group_path}/{artifact}/{version}/{filename}"

    def get_installer(self, url: str) -> Union[bytes, str]:
        pass

    def install(
        self,
        minecraft_version: str,
        loader_version: str,
        minecraft_dir_path: Optional[str] = None,
        version_name: Optional[str] = None,
        download_block_size: int = 8192,
    ):
        raise NotImplementedError("Not implemented yet.")


class FabricInstaller(ModLoaderInstaller):
    def __init__(
        self,
        api_base_url: str = "https://meta.fabricmc.net",
        max_workers: int = 5,
    ) -> None:
        super(FabricInstaller, self).__init__(api_base_url, max_workers)

    def _get_lib_hash(self, data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        if data.get("sha1") is not None:
            return data.get("sha1"), "sha1"
        elif data.get("md5") is not None:
            return data.get("md5"), "md5"
        elif data.get("sha256") is not None:
            return data.get("sha256"), "sha256"
        elif data.get("sha512") is not None:
            return data.get("sha512"), "sha512"
        else:
            return None

    def install(
        self,
        minecraft_version: str,
        loader_version: str,
        minecraft_dir_path: Optional[str] = None,
        version_name: Optional[str] = None,
        download_block_size: int = 8192,
    ):
        if version_name is None:
            version_name = f"fabric-{minecraft_version}-{loader_version}"
        self._set_paths(version_name, minecraft_dir_path)

        version_profile = self.get(
            f"/v2/versions/loader/{minecraft_version}/{loader_version}/profile/json"
        ).json()
        libraries = version_profile["libraries"]

        grouped_tasks: Dict[str, List[str]] = {}
        for lib in libraries:
            hash_pack = self._get_lib_hash(lib)
            maven_path = self._resolve_maven_coord(lib.get("name"))
            url = lib.get("url") + maven_path
            folder_prefix = str(Path(maven_path).parent)
            file_name = Path(maven_path).name
            if hash_pack is not None:
                task = (file_name, url, hash_pack[0], hash_pack[1])
            else:
                task = (file_name, url)
            grouped_tasks.setdefault(folder_prefix, []).append(task)

        deps_res = []
        for prefix, tasks in grouped_tasks.items():
            path = os.path.join(self.libraries_path, prefix)
            deps_res.extend(self.batch_download(tasks, path, download_block_size))
        if all(deps_res) is not True:
            return False

        version_profile["id"] = version_name
        return self._write_version_file(version_profile)


class NeoForgeInstaller(ModLoaderInstaller):
    def __init__(
        self, maven_base_url: str = "https://maven.neoforged.net", max_workers=5
    ) -> None:
        super(NeoForgeInstaller, self).__init__(maven_base_url, max_workers)

    def _generate_version_file(
        self,
        minecraft_version: str,
        loader_version: str,
        version_name: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        response = self.get(
            f"/releases/net/neoforged/neoforge/{loader_version}/neoforge-{loader_version}-installer.jar"
        )
        try:
            with zipfile.ZipFile(io.BytesIO(response.content), "r") as zip_ref:
                version_filename = "version.json"
                profile_filename = "install_profile.json"

                with zip_ref.open(profile_filename) as profile_file:
                    profile_data = json.load(profile_file)
                with zip_ref.open(version_filename) as version_file:
                    version_data = json.load(version_file)
                bin_path_filename = "/".join(
                    profile_data["data"]["BINPATCH"]["client"].split("/")[1:]
                )
                with zip_ref.open(bin_path_filename) as bin_path_file:
                    bin_path = bin_path_file.read()
        except Exception as e:
            raise RuntimeError(f"解析 NeoForge installer 失败: {e}")
        version_data["id"] = version_name
        version_data["inheritsFrom"] = minecraft_version
        return (version_data, profile_data["libraries"], profile_data["data"], bin_path)

    def _process(
        self,
        installer_tool_path: str,
        minecraft_jar_path: str,
        mojang_maping_path: str,
        patched_path: str,
        libraries_path: str,
        neoform_mapping_path: str,
        bin_path: bytes,
    ) -> bool:
        if os.path.exists(patched_path) is not False:
            os.remove(patched_path)
        with open(".temp.lzma", "wb") as file:
            file.write(bin_path)
        command = [
            "java",
            "-jar",
            installer_tool_path,
            "--task",
            "PROCESS_MINECRAFT_JAR",
            "--input",
            minecraft_jar_path,
            "--input-mappings",
            mojang_maping_path,
            "--output",
            patched_path,
            "--extract-libraries-to",
            libraries_path,
            "--neoform-data",
            neoform_mapping_path,
            "--apply-patches",
            ".temp.lzma",
        ]
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"Fail to patch {minecraft_jar_path}: {e}")
            print(f"Stdout: {e.stdout}")
            print(f"Stderr: {e.stderr}")
            return False
        except Exception as e:
            print(f"Fail to patch {minecraft_jar_path}: {e}")
            return False
        finally:
            if os.path.exists(".temp.lzma") is not False:
                os.remove(".temp.lzma")
        return True

    def install(
        self,
        minecraft_version,
        loader_version,
        minecraft_dir_path=None,
        version_name=None,
        download_block_size=8192,
    ) -> bool:
        if version_name is None:
            version_name = f"neoforge-{loader_version}"
        self._set_paths(version_name, minecraft_dir_path)

        version_profile, libraries, satic_data, bin_patch = self._generate_version_file(
            minecraft_version, loader_version, version_name
        )

        grouped_tasks: Dict[str, List[str]] = {}
        for lib in libraries:
            sha1_value = lib["downloads"]["artifact"]["sha1"]
            folder_prefix = str(Path(lib["downloads"]["artifact"]["path"]).parent)
            file_name = str(Path(lib["downloads"]["artifact"]["path"]).name)
            task = (file_name, lib["downloads"]["artifact"]["url"], sha1_value, "sha1")
            grouped_tasks.setdefault(folder_prefix, []).append(task)

        maven_path = self._resolve_maven_coord(satic_data["MOJMAPS"]["client"])
        folder_prefix = str(Path(maven_path).parent)
        file_name = str(Path(maven_path).name)
        target_version = None
        for version_info in self._minecraft_manifest["versions"]:
            if version_info["id"] == minecraft_version:
                target_version = version_info
                break
        version_data = requests.request("GET", target_version["url"]).json()
        task = (
            file_name,
            version_data["downloads"]["client_mappings"]["url"],
            version_data["downloads"]["client_mappings"]["sha1"],
            "sha1",
        )
        grouped_tasks.setdefault(folder_prefix, []).append(task)

        deps_res = []
        for prefix, tasks in grouped_tasks.items():
            path = os.path.join(self.libraries_path, prefix)
            deps_res.extend(self.batch_download(tasks, path, download_block_size))
        if not all(deps_res):
            return False

        if not os.path.exists(
            Path(self.versions_path, minecraft_version, f"{minecraft_version}.jar")
        ):
            self.single_download(
                version_data["downloads"]["client"]["url"],
                f"{minecraft_version}.jar",
                Path(self.versions_path, minecraft_version),
                download_block_size,
                version_data["downloads"]["client"]["sha1"],
                "sha1",
            )

        install_tool_coord = None
        for lib in libraries:
            if "net.neoforged.installertools:installertools" in lib.get("name"):
                install_tool_coord = lib.get("name")
                break

        pro_res = self._process(
            Path(
                self.libraries_path,
                self._resolve_maven_coord(install_tool_coord),
            ),
            Path(self.versions_path, minecraft_version, f"{minecraft_version}.jar"),
            Path(self.libraries_path, maven_path),
            Path(
                self.libraries_path,
                self._resolve_maven_coord(satic_data["PATCHED"]["client"]),
            ),
            self.libraries_path,
            Path(
                self.libraries_path,
                self._resolve_maven_coord(
                    f"net.neoforged:neoform:{satic_data["MCP_VERSION"]["client"]}:mappings@tsrg.lzma"
                ).replace("'", ""),
            ),
            bin_patch,
        )
        if pro_res is not True:
            return False

        return self._write_version_file(version_profile)
