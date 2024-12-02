#!/usr/bin/env python3

import base64
import glob
import json
import os
import random
import requests
import sys
import time
import threading
import google.auth
from google.auth.transport.requests import AuthorizedSession
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

credentials, project_id = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
if not project_id:
    raise Exception("Please set GOOGLE_APPLICATION_CREDENTIALS to a GCP service account key file (json).")

POOL_GENAI_ITEMS = int(os.getenv("POOL_GENAI_ITEMS") or "10")
PROMPTS_FILE = os.getenv("PROMPTS_FILE") or os.path.join(os.path.dirname(__file__), "prompts.json")
LISTEN_PORT = int(os.getenv("LISTEN_PORT") or "10001")
LISTEN_ADDR = os.getenv("LISTEN_ADDR") or "0.0.0.0"
GENERATE_VIDEO = True if os.getenv("GENERATE_VIDEO") else False
DMS_OUTPUT_DIR = os.getenv("DMS_OUTPUT_DIR") or "/var/lib/hakidgenai"
DMS_URL_BASE = os.environ["DMS_URL_BASE"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]


def eprint(*args, **kwargs):
    print(*args, **kwargs, file=sys.stderr)


def parse_prompts():
    with open(PROMPTS_FILE, "r") as f:
        prompts = json.load(f)

    # consolidating them:
    for s in prompts["static"]:
        if not s.get("ttsTemplate"):
            s["ttsTemplate"] = "default"
    for s in prompts["genai"]:
        if not s.get("ttsTemplate"):
            s["ttsTemplate"] = "default"
        if not s.get("genAiTemplate"):
            s["genAiTemplate"] = "default"
    return prompts


def send_tts_synthesize_request(session, data):
    eprint("POST", "https://texttospeech.googleapis.com/v1/text:synthesize", data, {"Content-Type": "application/json", "x-goog-user-project": project_id})
    response = session.request("POST", "https://texttospeech.googleapis.com/v1/text:synthesize", json=data, headers={"Content-Type": "application/json", "x-goog-user-project": project_id})
    re_data = response.json()
    if response.status_code != 200:
        raise Exception("Error calling synthesize: "+json.dumps(re_data))
    return base64.b64decode(re_data["audioContent"])


def get_dest_file_path(subdir, idx):
    ext = ".mp4" if GENERATE_VIDEO else ".mp3"
    filename = os.path.join(subdir, idx + ext)
    return os.path.join(DMS_OUTPUT_DIR, filename), filename


def save_data(dest_file, binary):
    with open(dest_file, "wb") as f:
        f.write(binary)


def save_dest_file(dest_file, binary):
    if not GENERATE_VIDEO:
        save_data(dest_file, binary)
        return

    tmp_dest_file = dest_file + ".mp3"
    save_data(tmp_dest_file, binary)
    # TODO: need to convert to video: https://unix.stackexchange.com/questions/657519/how-to-convert-output-mp3-to-mp4-with-ffmpeg
    os.unlink(tmp_dest_file)


def generate_audio_from_text(setup, session, template_name, text):
    eprint("Synthetising text", text)
    data = dict(setup["ttsTemplates"][template_name])
    data["input"] = {}
    data["input"]["text"] = text
    return send_tts_synthesize_request(session, data)


def process_static(setup, shared_data):
    to_be_generated = {}
    for s in setup["static"]:
        dest_file, dest_name = get_dest_file_path("static", s["id"])
        if os.path.exists(dest_file):
            continue
        s["dest_name"] = dest_name
        to_be_generated[dest_file] = s
    if not to_be_generated:
        return
    authed_session = AuthorizedSession(credentials)
    for dest_file, s in to_be_generated.items():
        audio_binary_data = generate_audio_from_text(setup, authed_session, s["ttsTemplate"], s["text"])
        save_dest_file(dest_file, audio_binary_data)
        shared_data["available"][dest_file] = DMS_URL_BASE + s["dest_name"]


def maintain_genai_items(setup, shared_data):
    cnt = 0
    for dest_path in shared_data["available"]:
        if "/genai/" in dest_path:
            cnt += 1
    if cnt >= POOL_GENAI_ITEMS:
        return
    authed_session = AuthorizedSession(credentials)
    while cnt < POOL_GENAI_ITEMS:
        cnt += 1
        eprint(f"Generating one more GenAi item {cnt}/{POOL_GENAI_ITEMS}")
        s = setup["genai"][random.randrange(0, len(setup["genai"]))]
        data = dict(setup["genAiTemplates"][s["genAiTemplate"]])
        data["contents"] = [{"parts": [{"text": s["text"]}]}]
        response = requests.request("POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key="+GOOGLE_API_KEY, json=data, headers={"Content-Type": "application/json"})
        re_data = response.json()
        if response.status_code != 200:
            raise Exception("Error calling generateContent: "+json.dumps(re_data))
        text = re_data["candidates"][0]["content"]["parts"][0]["text"]

        audio_binary_data = generate_audio_from_text(setup, authed_session, s["ttsTemplate"], text)
        dest_file, dest_name = get_dest_file_path("genai", f"{s['id']}-{time.time()}")
        save_dest_file(dest_file, audio_binary_data)
        shared_data["available"][dest_file] = DMS_URL_BASE + dest_name


def serve_http(shared_data):
    class WebRequestHandler(BaseHTTPRequestHandler):

        def pick_one(self):
            i = random.randrange(0, len(shared_data["available"]))
            dest_file = list(shared_data["available"].keys())[i]
            dest_url = shared_data["available"][dest_file]
            if "/genai/" in dest_file:
                del shared_data["available"][dest_file]
                shared_data["to_be_deleted"][dest_file] = time.time()
            
            return {"url": dest_url}
            
        def send_content(self):
            p = os.path.join(DMS_OUTPUT_DIR, self.path.lstrip("/"))
            if ".." in self.path or not os.path.exists(p):
                self.send_response(404)
                self.end_headers()
                return
            if ".mp3" in self.path:
                content_type = "audio/mp3"
            else:
                content_type = "video/mp4"
            size = os.path.getsize(p)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(p, "rb") as fp:
                data = fp.read()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/pick":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(self.pick_one()).encode("utf-8"))
            else:
                self.send_content()

    eprint("Listening on", LISTEN_ADDR, LISTEN_PORT, "project is", project_id)
    server = HTTPServer((LISTEN_ADDR, LISTEN_PORT), WebRequestHandler)
    server.serve_forever()


def delete_old_stuff(to_be_deleted):
    now = time.time()
    tmp = []
    for dest_path, access_timestamp in to_be_deleted.items():
        if now - access_timestamp >= 5*60:
            eprint("Removing genai file already used", dest_path)
            tmp.append(dest_path)
            os.unlink(dest_path)
    for dest_path in tmp:
        del to_be_deleted[dest_path]


def scan_existing_items():
    items = {}
    for f in glob.glob("**/*.mp*", root_dir=DMS_OUTPUT_DIR):
        eprint("Already available", f)
        items[os.path.join(DMS_OUTPUT_DIR, f)] = DMS_URL_BASE + f
    return items



def spawn_background_process(shared_data):
    def background_process():
        os.makedirs(os.path.join(DMS_OUTPUT_DIR, "static"), exist_ok=True)
        os.makedirs(os.path.join(DMS_OUTPUT_DIR, "genai"), exist_ok=True)
        shared_data["available"] = scan_existing_items()
        firstTime = True
        while True:
            try:
                if not firstTime:
                    time.sleep(60)
                firstTime = False
                delete_old_stuff(shared_data["to_be_deleted"])
                setup = parse_prompts()
                process_static(setup, shared_data)
                maintain_genai_items(setup, shared_data)
            except KeyboardInterrupt:
                return
            except Exception as e:
                eprint(e)
            
    threading.Thread(target=background_process).start()


def do_the_job():
    shared_data = {"available":{}, "to_be_deleted":{}}
    spawn_background_process(shared_data)
    serve_http(shared_data)


if __name__ == "__main__":
    do_the_job(*sys.argv[1:])
