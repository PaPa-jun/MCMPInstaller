import os, json
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from .models import BaseClientModel
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed


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

    def _maven_name_to_path(self, name: str) -> str:
        parts = name.split(":")
        if len(parts) != 3:
            raise ValueError(f"Invalid Maven coordinate format: {name}")
        group_id, artifact_id, version = parts
        group_path = group_id.replace(".", "/")
        return f"{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.jar"

    def _get_lib_hash(self, data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        if data.get("md5") is not None:
            return data.get("md5"), "md5"
        elif data.get("sha1") is not None:
            return data.get("sha1"), "sha1"
        elif data.get("sha256") is not None:
            return data.get("sha256"), "sha256"
        elif data.get("sha512") is not None:
            return data.get("sha512"), "sha512"
        else:
            return None

    def _download_dependencies(
        self,
        minecraft_version: str,
        loader_version: str,
        dest_path: Optional[str] = None,
        block_size: int = 8192,
    ) -> List[bool]:
        if dest_path is None:
            home_path = Path.home()
            dest_path = os.path.join(home_path, "Downloads")
        response = self.get(
            f"/v2/versions/loader/{minecraft_version}/{loader_version}/profile/json"
        ).json()
        libraries = response["libraries"]
        grouped_tasks: Dict[str, List[str]] = {}
        for lib in libraries:
            hash_pack = self._get_lib_hash(lib)
            maven_path = self._maven_name_to_path(lib.get("name"))
            url = lib.get("url") + maven_path
            folder_prefix = str(Path(maven_path).parent)
            file_name = Path(maven_path).name
            if hash_pack is not None:
                task = (file_name, url, hash_pack[0], hash_pack[1])
            else:
                task = (file_name, url)
            grouped_tasks.setdefault(folder_prefix, []).append(task)

        results = []
        for prefix, tasks in grouped_tasks.items():
            path = os.path.join(dest_path, prefix)
            results.extend(self.batch_download(tasks, path, block_size))
        return results

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
        if minecraft_dir_path is None:
            minecraft_dir_path = self._get_minecraft_dir_path()
        libraries_path = os.path.join(minecraft_dir_path, "libraries")
        versions_path = os.path.join(minecraft_dir_path, "versions")
        profile_path = os.path.join(versions_path, version_name)
        profile_json_file = os.path.join(profile_path, f"{version_name}.json")
        deps_res = self._download_dependencies(
            minecraft_version, loader_version, libraries_path, download_block_size
        )
        if all(deps_res) is not True:
            return False
        os.makedirs(profile_path, exist_ok=True)
        response = self.get(
            f"/v2/versions/loader/{minecraft_version}/{loader_version}/profile/json"
        ).json()
        response["id"] = version_name
        try:
            with open(profile_json_file, "w", encoding="utf-8") as file:
                json.dump(response, file, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            return False
        return True


class NeoForgeInstaller(ModLoaderInstaller):
    def __init__(
        self, api_base_url: str = "https://maven.neoforged.net", max_workers=5
    ) -> None:
        super(NeoForgeInstaller, self).__init__(api_base_url, max_workers)

    def _parse_pom(self, pom_content: str) -> Tuple[List[Dict[str, str]], str]:
        root = ET.fromstring(pom_content)
        namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
        libraries = []
        dependencies = root.findall(".//m:dependency", namespace)

        for dep in dependencies:
            group_id_elem = dep.find("m:groupId", namespace)
            artifact_id_elem = dep.find("m:artifactId", namespace)
            version_elem = dep.find("m:version", namespace)

            if (
                group_id_elem is None
                or artifact_id_elem is None
                or version_elem is None
            ):
                continue

            group_path = group_id_elem.text.replace(".", "/")
            file_name = (
                f"{artifact_id_elem.text}-{version_elem.text}.jar"
                if artifact_id_elem.text != "neoform"
                else f"{artifact_id_elem.text}-{version_elem.text}.zip"
            )
            maven_path = (
                f"{group_path}/{artifact_id_elem.text}/{version_elem.text}/{file_name}"
            )
            if artifact_id_elem.text == "neoform":
                neoform_version = version_elem.text
            url = f"{self._base_url}/releases/{maven_path}"
            lib_entry = {
                "name": ":".join(
                    [group_id_elem.text, artifact_id_elem.text, version_elem.text]
                ),
                "downloads": {
                    "artifact": {
                        "path": maven_path,
                        "url": url,
                    }
                },
            }
            libraries.append(lib_entry)

        with ThreadPoolExecutor(self._max_workers) as executor:
            future_to_lib = {}
            for lib in libraries:
                path = lib["downloads"]["artifact"]["path"]
                future = executor.submit(self._fetch_file_sha1, path)
                future_to_lib[future] = lib

            for future in as_completed(future_to_lib):
                lib = future_to_lib[future]
                try:
                    sha1 = future.result()
                    if sha1:
                        lib["downloads"]["artifact"]["sha1"] = sha1
                except Exception:
                    pass

        return libraries, neoform_version

    def _fetch_file_sha1(self, maven_path: str) -> Tuple[Optional[str]]:
        response = self.get(endpoint=(f"/{maven_path}.sha1"))
        value = response.text.strip()
        return value if value else None

    def _generate_version_file(
        self,
        minecraft_version: str,
        loader_version: str,
        version_name: str,
    ) -> Dict[str, Any]:
        response = self.get(
            f"/releases/net/neoforged/neoforge/{loader_version}/neoforge-{loader_version}.pom"
        )
        libraries, neoform_version = self._parse_pom(response.text)
        arguments = {
            "game": [
                "--fml.neoForgeVersion",
                loader_version,
                "--fml.mcVersion",
                minecraft_version,
                "--fml.neoFormVersion",
                neoform_version,
            ],
            "jvm": [
                "-Djava.net.preferIPv6Addresses=system",
                "-DlibraryDirectory=${library_directory}",
                "--add-opens",
                "java.base/java.lang.invoke=ALL-UNNAMED",
                "--add-exports",
                "jdk.naming.dns/com.sun.jndi.dns=java.naming",
            ],
        }
        return {
            "id": version_name,
            "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "releaseTime": "2077-01-01T00:00:00.012345Z",
            "type": "release",
            "mainClass": "net.neoforged.fml.startup.Client",
            "inheritsFrom": minecraft_version,
            "arguments": arguments,
            "libraries": libraries,
        }

    def install(
        self,
        minecraft_version,
        loader_version,
        minecraft_dir_path=None,
        version_name=None,
        download_block_size=8192,
    ):
        if version_name is None:
            version_name = f"neoforge-{minecraft_version}-{loader_version}"
        if minecraft_dir_path is None:
            minecraft_dir_path = self._get_minecraft_dir_path()
        libraries_path = os.path.join(minecraft_dir_path, "libraries")
        versions_path = os.path.join(minecraft_dir_path, "versions")
        profile_path = os.path.join(versions_path, version_name)
        profile_json_file = os.path.join(profile_path, f"{version_name}.json")

        profile_data = self._generate_version_file(
            minecraft_version, loader_version, version_name
        )
        libraries = profile_data["libraries"]
        grouped_tasks: Dict[str, List[str]] = {}
        downloaded_files_map = []

        for lib in libraries:
            sha1_value = lib["downloads"]["artifact"]["sha1"]
            folder_prefix = str(Path(lib["downloads"]["artifact"]["path"]).parent)
            file_name = Path(lib["downloads"]["artifact"]["path"]).name
            task = (file_name, lib["downloads"]["artifact"]["url"], sha1_value, "sha1")
            grouped_tasks.setdefault(folder_prefix, []).append(task)
            local_file_path = os.path.join(libraries_path, folder_prefix, file_name)
            downloaded_files_map.append({"local_path": local_file_path, "lib_ref": lib})

        deps_res = []
        for prefix, tasks in grouped_tasks.items():
            path = os.path.join(libraries_path, prefix)
            deps_res.extend(self.batch_download(tasks, path, download_block_size))
        if not all(deps_res):
            return False

        for item in downloaded_files_map:
            local_path = item["local_path"]
            lib_ref = item["lib_ref"]
            file_size = os.path.getsize(local_path)
            lib_ref["downloads"]["artifact"]["size"] = file_size

        try:
            os.makedirs(profile_path, exist_ok=True)
            with open(profile_json_file, "w", encoding="utf-8") as file:
                json.dump(profile_data, file, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            return False
        return True
