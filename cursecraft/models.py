import io, os, re, requests, tempfile, subprocess, json, zipfile
from tqdm import tqdm
from pathlib import Path
from typing import Optional, Any, Dict, List

from .utils import (
    get_main_class,
    resolve_maven_coord,
    get_minecraft_dir_path,
    single_download,
    hash_verify,
)


class BaseClientModel:
    def __init__(self, headers: Dict[str, Any], base_url: str, max_workers: int = 5):
        self._base_url = base_url
        self._headers = headers
        self._max_workers = max_workers

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        try:
            response = requests.request(
                method=method,
                url=endpoint,
                headers=self._headers,
                params=params,
                json=json,
            )
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            print(f"HTTP 请求错误: {e}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"请求发生错误: {e}")
            return None

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self._base_url + endpoint
        return self._request("GET", url, params=params)

    def post(self, endpoint: str, json: Optional[Dict[str, Any]] = None) -> Any:
        url = self._base_url + endpoint
        return self._request("POST", url, json=json)


class BaseInstaller(BaseClientModel):
    def __init__(
        self,
        api_base_url: str,
        max_workers: int = 5,
    ):
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3 Safari/605.1.15"
        }
        super(BaseInstaller, self).__init__(self._headers, api_base_url, max_workers)

        self.installer = {}
        self._static_data = {}
        self._pattern = re.compile(r"(\{[A-Z_]+\})|(\[[^\]]+\])")

    def _get_installer(self, endpoint: str) -> None:
        response = self.get(endpoint)
        try:
            with zipfile.ZipFile(io.BytesIO(response.content), "r") as zip_ref:
                for file_name in zip_ref.namelist():
                    if file_name.endswith("/"):
                        continue
                    self.installer[file_name] = zip_ref.read(file_name)
        except Exception as e:
            raise RuntimeError(f"解析 installer 失败: {e}")

    def _replace_arg_variable(self, arg: str) -> str:
        def replace_match(match):
            var_match = match.group(1)
            maven_match = match.group(2)

            if var_match:
                key = var_match[1:-1]
                if key in self._static_data:
                    return self._static_data[key]
                else:
                    return var_match

            elif maven_match:
                coord = maven_match[1:-1]
                try:
                    resolved_path = resolve_maven_coord(coord)
                    resolved_path = str(
                        Path(self._static_data["ROOT"], "libraries", resolved_path)
                    )
                    return resolved_path
                except Exception as e:
                    print(f"Error resolving maven coord {coord}: {e}")
                    return maven_match

            return match.group(0)

        return self._pattern.sub(replace_match, arg)

    def _parse_install_profile(self, install_profile: Dict[str, Any]):
        for key, values in install_profile["data"].items():
            self._static_data[key] = self._replace_arg_variable(values["client"])

        processors = []
        for processor in install_profile["processors"]:
            if self._static_data["SIDE"] not in processor.get("sides", ["client"]):
                continue

            jar_path = str(
                Path(
                    self._static_data["ROOT"],
                    "libraries",
                    resolve_maven_coord(processor["jar"]),
                )
            )

            main_class = get_main_class(jar_path)

            class_paths = []
            for class_coord in processor.get("classpath"):
                class_path = str(
                    Path(
                        self._static_data["ROOT"],
                        "libraries",
                        resolve_maven_coord(class_coord),
                    )
                )
                class_paths.append(class_path)
            if jar_path not in class_paths:
                class_paths.insert(0, jar_path)
            args = []
            for arg in processor["args"]:
                arg = self._replace_arg_variable(arg)
                args.append(arg)

            outputs = {}
            if processor.get("outputs") is not None:
                for key, value in processor.get("outputs").items():
                    outputs[self._replace_arg_variable(key)] = (
                        self._replace_arg_variable(value)
                    )

            processors.append(
                {
                    "jar": jar_path,
                    "main_class": main_class,
                    "class_paths": class_paths,
                    "args": args,
                    "outputs": outputs,
                }
            )
        return processors

    def _run_processors(
        self, processors: Dict[str, Any], java_executable: str = "java"
    ) -> List[bool]:
        def single_process(
            processor: Dict[str, Any],
            java_executable: str = "java",
        ) -> bool:
            main_class = processor.get("main_class")
            class_paths = processor.get("class_paths")
            args = processor["args"]
            outputs = processor.get("outputs")

            temp_files = []
            for i, arg in enumerate(args):
                if arg.startswith("/data/"):
                    file_key = arg[1:]
                    file_bytes = self.installer[file_key]

                    temp_file = tempfile.NamedTemporaryFile(
                        delete=False, suffix=os.path.basename(file_key)
                    )
                    temp_file.write(file_bytes)
                    temp_file.close()

                    temp_files.append(temp_file.name)
                    args[i] = temp_file.name

            classpath_str = os.pathsep.join(class_paths)
            command = [java_executable, "-cp", classpath_str, main_class] + args
            try:
                result = subprocess.run(
                    command,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                print(result.stdout)
                if not outputs:
                    for file_path, hash_value in outputs.keys():
                        hash_verify(file_path, hash_value, "sha1")
                return True
            except subprocess.CalledProcessError as e:
                print(f"Fail to patch, Return Code: {e.returncode}")
                print(f"Stdout: {e.stdout}")
                print(f"Stderr: {e.stderr}")
                return False
            except Exception as e:
                print(f"Fail to patch: {e}")
                return False
            finally:
                for temp_file_path in temp_files:
                    try:
                        os.remove(temp_file_path)
                    except OSError:
                        pass

        results = []
        for processor in processors:
            results.append(single_process(processor, java_executable))
        return results

    def _install_initialize(
        self,
        install_side: str,
        minecraft_version: str,
        loader_name: str,
        loader_version: str,
        install_path: Optional[str] = None,
    ) -> None:
        self._static_data["SIDE"] = install_side
        self._static_data["ROOT"] = (
            install_path if install_path else get_minecraft_dir_path()
        )
        self._static_data["MINECRAFT_VERSION"] = minecraft_version
        self._static_data["LOADER_NAME"] = loader_name
        self._static_data["LOADER_VERSION"] = loader_version
        self._static_data["MINECRAFT_JAR"] = str(
            Path(
                self._static_data["ROOT"],
                "versions",
                minecraft_version,
                f"{minecraft_version}.jar",
            )
        )

    def _write_version_file(self, data: Dict[str, Any]) -> bool:
        file_path = Path(
            self._static_data["ROOT"],
            "versions",
            f"{self._static_data["LOADER_NAME"]}-{self._static_data["LOADER_VERSION"]}",
        )
        file_name = str(
            Path(
                file_path,
                f"{self._static_data["LOADER_NAME"]}-{self._static_data["LOADER_VERSION"]}.json",
            )
        )
        data["id"] = (
            f"{self._static_data["LOADER_NAME"]}-{self._static_data["LOADER_VERSION"]}"
        )
        try:
            os.makedirs(file_path, exist_ok=True)
            with open(file_name, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"写入配置文件失败: {e}")
            return False
        return True

    def check_and_download_minecraft_jar(self, block_size: int = 8192) -> bool:
        if os.path.exists(self._static_data["MINECRAFT_JAR"]) is False:
            manifest = requests.request(
                "GET", "https://launchermeta.mojang.com/mc/game/version_manifest.json"
            ).json()
            target_version = None
            for version_info in manifest["versions"]:
                if version_info["id"] == self._static_data["MINECRAFT_VERSION"]:
                    target_version = version_info
                    break
            version_data = requests.request("GET", target_version["url"]).json()
            return single_download(
                version_data["downloads"]["client"]["url"],
                f"{self._static_data["MINECRAFT_VERSION"]}.jar",
                Path(
                    self._static_data["ROOT"],
                    "versions",
                    self._static_data["MINECRAFT_VERSION"],
                ),
                block_size,
                version_data["downloads"]["client"]["sha1"],
                "sha1",
            )
        return True

    def install(
        self,
        minecraft_version: str,
        loader_version: str,
        minecraft_dir_path: Optional[str] = None,
        download_block_size: int = 8192,
        install_side: str = "client",
    ):
        raise NotImplementedError("Not implemented yet.")
