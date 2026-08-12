import urllib.request
import json

url = 'http://localhost:8000/api/custom-signal'

payload = {
    "symbol": "XAU/USD",
    "type": "SELL LIMIT",
    "entry": 4385.58,
    "sl": 4390.19,
    "tp": 4371.76,
    "reasons": ["Sinyal 4385 di-reactivate manual"]
}

data = json.dumps(payload).encode('utf-8')

req = urllib.request.Request(url, data=data, method='POST')
req.add_header('Content-Type', 'application/json')
req.add_header('X-Custom-Signal-Secret', 'novaire_secret_123')

try:
    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        print("Sinyal berhasil dibuat ulang!")
        print(result)
except Exception as e:
    print(f"Gagal memanggil API: {e}")
    print("Pastikan EA-nya (main.py) udah running dulu di terminal lain!")
