import requests
import json
from datetime import datetime

# URL backend Anda (Ganti dengan URL Railway Anda jika sudah di-deploy)
# Contoh lokal: "http://127.0.0.1:8000/api/custom-signal"
API_URL = "http://127.0.0.1:8000/api/custom-signal"

# Secret Key yang sama dengan yang ada di file .env backend
SECRET_KEY = "novaire_secret_123"

def send_custom_signal():
    print("=== PENGIRIM SINYAL KUSTOM ===")
    symbol = input("Masukkan Pair (Contoh: XAU/USD): ") or "XAU/USD"
    signal_type = input("Tipe Sinyal (BUY LIMIT / SELL LIMIT / BUY / SELL): ") or "BUY"
    
    try:
        entry = float(input("Harga Entry: "))
        sl = float(input("Harga Stop Loss (SL): "))
        tp = float(input("Harga Take Profit (TP): "))
    except ValueError:
        print("Error: Harap masukkan angka yang valid!")
        return

    reason_input = input("Alasan Sinyal (opsional): ")
    reasons = [reason_input] if reason_input else ["Manual Custom Signal", "Dianalisa oleh Trader"]

    payload = {
        "symbol": symbol.upper(),
        "type": signal_type.upper(),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "reasons": reasons
    }

    headers = {
        "Content-Type": "application/json",
        "X-Custom-Signal-Secret": SECRET_KEY
    }

    print(f"\nMengirim sinyal ke {API_URL}...")
    
    try:
        response = requests.post(API_URL, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 200:
            print("\n✅ Sinyal Berhasil Dikirim!")
            print("Respons Server:", json.dumps(response.json(), indent=2))
        elif response.status_code == 401:
            print("\n❌ Gagal: Secret Key salah atau tidak diotorisasi (Error 401)")
        else:
            print(f"\n❌ Gagal dengan kode {response.status_code}")
            print("Detail:", response.text)
            
    except requests.exceptions.ConnectionError:
        print(f"\n❌ Error: Tidak dapat terhubung ke server. Pastikan backend di {API_URL} sedang berjalan.")

if __name__ == "__main__":
    send_custom_signal()
