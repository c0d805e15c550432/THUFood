from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64
import requests
import json
from datetime import date, datetime

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
    response = requests.post(url, params=params, cookies=cookie)
    
    encrypted_string = json.loads(response.text)["data"]
    decrypted_string = decrypt_aes_ecb(encrypted_string)
    data = json.loads(decrypted_string)
        # dump data as json file
    data_file_name = f"./eat_records/eat_record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(data_file_name, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return data
