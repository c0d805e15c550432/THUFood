from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64
import requests
import json
from datetime import date, datetime
from pathlib import Path

def decrypt_aes_ecb(encrypted_data: str) -> str:
    
    key = encrypted_data[:16].encode('utf-8')
    encrypted_data = encrypted_data[16:]
    encrypted_data_bytes = base64.b64decode(encrypted_data)
    
    cipher = AES.new(key, AES.MODE_ECB)
    
    decrypted_data = unpad(cipher.decrypt(encrypted_data_bytes), AES.block_size)

    return decrypted_data.decode('utf-8')

def _format_query_date(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y-%m-%d")

def get_record(servicehall, idserial, starttime, endtime):
    starttime = _format_query_date(starttime)
    endtime = _format_query_date(endtime)
    if starttime > endtime:
        raise ValueError("starttime must not be later than endtime")

    # 获取数据
    url = "https://card.tsinghua.edu.cn/business/querySelfTradeList"
    params = {
        "pageNumber": 0,
        "pageSize": 5000,
        "starttime": starttime,
        "endtime": endtime,
        "idserial": idserial,
        "tradetype": -1,
    }
    cookie = {"servicehall": servicehall}
    response = requests.post(url, params=params, cookies=cookie, timeout=30)
    response.raise_for_status()

    payload = response.json()
    encrypted_string = payload.get("data")
    if not encrypted_string:
        message = payload.get("message") or payload.get("msg") or "The server returned no data"
        raise ValueError(message)

    decrypted_string = decrypt_aes_ecb(encrypted_string)
    data = json.loads(decrypted_string)

    # Saving a local copy is optional. In a PyInstaller executable the working
    # directory may not contain eat_records (or may not be writable), so a
    # persistence failure must not turn a successful query into a login error.
    try:
        records_dir = Path.cwd() / "eat_records"
        records_dir.mkdir(parents=True, exist_ok=True)
        data_file = records_dir / f"eat_record_{datetime.now():%Y%m%d_%H%M%S}.json"
        with data_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except OSError:
        pass

    return data
