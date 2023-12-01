#!/usr/bin/env python3

import hashlib
import json
import mimetypes
import os
import re
import subprocess
from typing import Union
import urllib

import requests
from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

load_dotenv()

app = FastAPI(
    title="patch managger",
    version="0.1.1")
templates = Jinja2Templates(directory="templates")


access_enable_exts = ["bmp", "jpg", "png", "wav", "mp3", "txt", "xml"]

encode_euckr_files = [
    "data\\idnum2itemresnametable.txt",
    "data\\num2itemresnametable.txt"
]

@app.get("/")
def index():
    return RedirectResponse("/patch-manager/patch2")

@app.get("/patch2", response_class=HTMLResponse)
async def view_patch2_list(request: Request):
    patch_dict: dict[str] = {}
    response = requests.get("https://patch2.gungho.jp/patch30/patchbbs/patch2.txt", timeout=5)

    pattern = re.compile(r"^(//)?(\d+)\s+(.*)$")

    for patch in response.text.splitlines():
        matches = pattern.search(patch)
        if matches is None or matches.group(0) == "//":
            continue

        patch_dict[int(matches.group(2))] = str(matches.group(3))

    # reverse sort
    patch_dict = sorted(patch_dict.items(), reverse=True)
    patch_dict = dict((key, value) for key, value in patch_dict)

    return templates.TemplateResponse("patch2.html", {"request": request, "patch_dict": patch_dict})

@app.get("/patch_file/{patch_file_name}", response_class=HTMLResponse)
async def view_patch_file(request: Request, patch_file_name: str = None):
    if patch_file_name is not None and re.match(r"^[\w\d\.\-]+$", patch_file_name):
        if os.path.isfile(f"./patch_files/{patch_file_name:s}") == False:
            try:
                response = requests.get(f"https://patch2.gungho.jp/pub/dl-gunghoftp/roftp/{patch_file_name:s}", timeout=2)
                if response.status_code == 200:
                    # save
                    with open(f"./patch_files/{patch_file_name:s}", mode="wb") as fp:
                        fp.write(response.content)

            except ConnectionError as ex:
                print("Connection Error:", ex)
            except requests.HTTPError as ex:
                print("HTTP Error:", ex)
            except requests.Timeout as ex:
                print("Timeout Error:", ex)
            except requests.RequestException as ex:
                print("Error:", ex)

        if os.path.isfile(f"./patch_files/{patch_file_name:s}") == False:
            return JSONResponse(content={"status":"error", "message":"File not found."})

        patch_file_size = round(os.path.getsize(f"./patch_files/{patch_file_name:s}") / 1024, 2)

        patch_file_digests: dict[str] = {}
        patch_file_digests["SHA256"]= get_file_hexdigest(f"./patch_files/{patch_file_name:s}", "SHA256")
        patch_file_digests["SHA3-256"]= get_file_hexdigest(f"./patch_files/{patch_file_name:s}", "SHA3-256")
        patch_file_digests["SHA1"]= get_file_hexdigest(f"./patch_files/{patch_file_name:s}", "SHA1")
        patch_include_list: list = []
        patch_include_files: dict[str] = {}

        if re.match(r"^.+\.gpf$", patch_file_name):
            iconv_param: list[str] = ["-c", "-f", "EUC-KR", "-t", "UTF-8"]

            grftool: list[str] = [os.getenv("GRFTOOL"), os.path.abspath(f"./patch_files/{patch_file_name:s}")]

            iconv: list[str] = [os.getenv("ICONV")]
            iconv.extend(iconv_param)

            tr: list[str] = [os.getenv("TR")]
            tr.extend(["-d", "\\r"])

            subp1 = subprocess.run(grftool, capture_output=True)
            subp2 = subprocess.run(iconv, capture_output=True, input=subp1.stdout)
            subp3 = subprocess.run(tr, capture_output=True, input=subp2.stdout)
            patch_include_list = subp3.stdout.decode().split("\n")
            patch_include_list.remove("")

        elif re.match(r"^.+\.rgz$", patch_file_name):
            rgztool = os.getenv("RGZTOOL")
            subp1 = subprocess.run([rgztool, os.path.abspath(f"./patch_files/{patch_file_name:s}"), "--json-output"], capture_output=True)
            patch_include_list = json.loads(subp1.stdout.decode())

        for value in patch_include_list:
            patch_include_files[value]: dict = {}
            matches = re.match(r".*\.(.+)$", value)
            if matches:
                patch_include_files[value]["ext"] = matches.group(1)
                if matches.group(1) in access_enable_exts:
                    patch_include_files[value]["link"] = f"/patch-manager/extract_patch_file/{patch_file_name:s}?f=" + urllib.parse.quote_plus(value)
                else:
                    patch_include_files[value]["link"] = None
            else:
                patch_include_files[value]["link"] = None

        return templates.TemplateResponse("patch_file.html",
                                          {"request": request,
                                           "patch_file_name": patch_file_name,
                                           "patch_file_digests": patch_file_digests,
                                           "patch_file_size": patch_file_size,
                                           "patch_include_files": patch_include_files
                                           })

    return JSONResponse(content={"status":"error", "memssage":"Invalid paramater."})

@app.get("/extract_patch_file/{patch_file_name}")
async def extract_patch_file(request: Request, patch_file_name: str = None, filepath: Union[str, None] = Query(default=None, alias="f", max_length=256)):

    if patch_file_name is None:
        raise HTTPException(status_code=404, detail="File not found")

    if filepath is None or filepath == "":
        raise HTTPException(status_code=400, detail="File requelst is bad")

    if not re.match(r"^[\w\d\.\-]+$", patch_file_name):
        raise HTTPException(status_code=400, detail="Bad request")

    if not re.match(r"^.+\.(gpf|rgz)$", patch_file_name):
        raise HTTPException(status_code=406, detail="Not Acceptable")

    if os.path.isfile(f"./patch_files/{patch_file_name:s}") == False:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        extract_dirpath = os.path.abspath(f"./patch_files/extract/{patch_file_name:s}")
        filepath = urllib.parse.unquote_plus(filepath)
        extract_filepath = os.path.abspath(extract_dirpath + "/" + filepath.replace("\\","/"))

        if extract_filepath.startswith(extract_dirpath) == False:
            # Detect : directory traversal
            raise HTTPException(status_code=400, detail="Bad request (Are you a hacker?)")

        dirpath: str = os.path.dirname(extract_filepath)
        if os.path.isdir(dirpath) == False:
            os.makedirs(dirpath)

        if os.path.isfile(extract_filepath) == False:
            patch_file = os.path.abspath(f"./patch_files/{patch_file_name:s}")
            ext = os.path.splitext(patch_file)[1]
            if ext == ".gpf":
                grftool: list[str] = [os.getenv("GRFTOOL"), patch_file, filepath.encode("euc-kr")]

                subp = subprocess.run(grftool, capture_output=True)

                data: Union[bytes, str, None] = None
                extract_file_ext = os.path.splitext(extract_filepath)[1][1:]
                if extract_file_ext in ["txt", "xml"]:
                    iconv_param: list[str] = ["-c", "-f", "CP932", "-t", "UTF-8"]
                    if filepath in encode_euckr_files:
                        iconv_param = ["-c", "-f", "EUC-KR", "-t", "UTF-8"]

                    iconv: list[str] = [os.getenv("ICONV")]
                    iconv.extend(iconv_param)
                    subp_iconv = subprocess.run(iconv, capture_output=True, input=subp.stdout)

                    data = subp_iconv.stdout
                else:
                    data = subp.stdout

                with open(extract_filepath, mode="wb") as fp:
                    fp.write(data)

            elif ext == ".rgz":
                rgztool = os.getenv("RGZTOOL")
                print([rgztool, "-e", extract_filepath, "-f", patch_file_name, patch_file])
                subprocess.run([rgztool, "-e", extract_filepath, "-f", filepath, patch_file])

        if not mimetypes.inited:
            mimetypes.init()

        media_type = mimetypes.types_map[os.path.splitext(extract_filepath)[1]]

        return FileResponse(extract_filepath, media_type=media_type, filename=os.path.basename(extract_filepath), content_disposition_type="inline")

    except HTTPException as ex:
        raise ex

    except Exception as ex:
        raise HTTPException(status_code=500, detail="Internal Server Error")

def get_file_hexdigest(filepath: str, algo: str):
    hash = hashlib.new(algo)

    length = hashlib.new(algo).block_size *0xf000

    with open(filepath, mode="rb") as fp:
        bin_datas = fp.read(length)

        # データがなくなるまでループします
        while bin_datas:

            # ハッシュオブジェクトに追加して計算します。
            hash.update(bin_datas)

            # データの続きを読み込む
            bin_datas = fp.read(length)

    return hash.hexdigest()

if __name__ == '__main__':
    uvicorn.run(app=app)
