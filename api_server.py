# api_server.py
import os
import json
import uuid
import threading
import datetime
import time
from flask import Flask, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

# Optional: websocket logic left as-is (Pushbullet websocket)
import websocket

app = Flask(__name__)
app.secret_key = str(uuid.uuid4())

# DATABASE_URL expected as environment variable:
# e.g. postgres://user:pass@host:port/dbname
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경변수가 설정되어 있지 않습니다.")

# 연결 함수 (connection pool 없이 간단 구현)
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def ensure_tables():
    """테이블 있으면 패스, 없으면 생성."""
    sqls = [
        """
        CREATE TABLE IF NOT EXISTS ios_transactions (
            id SERIAL PRIMARY KEY,
            shop TEXT NOT NULL,
            userid TEXT NOT NULL,
            displayname TEXT NOT NULL,
            count INTEGER NOT NULL,
            success BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            shop TEXT NOT NULL,
            userid TEXT NOT NULL,
            displayname TEXT NOT NULL,
            count INTEGER NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
    ]
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                for s in sqls:
                    cur.execute(s)
    finally:
        conn.close()

ensure_tables()

def delete_transaction_after_delay(shop, user_id, name, amount, delay_seconds=300):
    """주어진 시간 후에 성공=false 상태의 요청을 삭제합니다."""
    time.sleep(delay_seconds)
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ios_transactions WHERE shop=%s AND userid=%s AND displayname=%s AND count=%s AND success=%s;",
                    (shop, user_id, name, amount, False)
                )
    finally:
        conn.close()

# 기존 bank endpoint (Pushbullet websocket 방식) - 그대로 두지만 DB는 Postgres 사용
@app.route("/bank", methods=['POST'])
def bank():
    try:
        request_time = datetime.datetime.datetime.now() if hasattr(datetime, 'datetime') else datetime.datetime.now()
        req = request.get_json(force=True)
        bankpin = req.get("bankpin")
        key = req.get("api_key")
        shop = req.get("shop")
        userid = req.get("userid")
        username = req.get("userinfo")

        # on_message 내부에서 DB에 바로 INSERT
        def on_message(ws, message):
            nonlocal request_time, req, shop, userid, username
            current_time = datetime.datetime.datetime.now() if hasattr(datetime, 'datetime') else datetime.datetime.now()
            minutes_difference = (current_time - request_time).total_seconds() / 60.0
            if minutes_difference >= 3:
                ws.close()
                return

            try:
                obj = json.loads(message)
                if obj.get("type") == "push":
                    push = obj.get("push", {})
                    body = push.get("body", "").replace("\n", " ")
                    NotificationApplicationName = str(push.get("package_name", ""))
                    message_parts = body.replace("원", "").replace(",", "").split(' ')
                    displayname = ""
                    count = 0

                    if NotificationApplicationName == "com.kbankwith.smartbank":  # 케이뱅크
                        sp = body.split(" ")
                        displayname = sp[2]
                        count = int(sp[1].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.IBK.SmartPush.app":  # 기업은행
                        sp = body.split(" ")
                        displayname = sp[2]
                        count = int(sp[1].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.shinhan.sbanking":  # 신한은행
                        sp = body.split(" ")
                        displayname = sp[1]
                        count = int(sp[0].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.nh.mobilenoti":  # 농협
                        displayname = message_parts[5]
                        count = int(message_parts[1].replace("입금", "").replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.nonghyup.nhallonebank":  # 농협 올원
                        displayname = message_parts[5]
                        count = int(message_parts[1].replace("입금", "").replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.wooribank.smart.npib":  # 우리
                        sp = body.split(" ")
                        displayname = sp[1]
                        count = int(sp[5].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.kakaobank.channel":  # 카카오뱅크
                        name_parts = body.split(" ")
                        displayname = name_parts[0]
                        title = push.get("title", "")
                        sp = title.split(" ")
                        count = int(sp[1].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "viva.republica.toss":  # 토스
                        displayname = push.get("body", "").replace(" → 내 토스뱅크 통장", '')
                        count = int(push.get("title", "").replace("원 입금", "").replace(",", ""))
                    else:
                        return

                    # 비교
                    if displayname == username and count == int(req.get("amount")):
                        conn = get_conn()
                        try:
                            with conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        "INSERT INTO transactions (shop, userid, displayname, count) VALUES (%s, %s, %s, %s);",
                                        (shop, userid, displayname, count)
                                    )
                        finally:
                            conn.close()
                        ws.close()
                        return
            except Exception:
                return

        def on_error(ws, error):
            print("websocket error:", error)

        def on_close(ws, close_status_code, close_msg):
            print("### websocket closed ###")

        def on_open(ws):
            print("websocket opened")

        websocket.enableTrace(False)
        ws = websocket.WebSocketApp("wss://stream.pushbullet.com/websocket/" + bankpin,
                                    on_message=on_message,
                                    on_error=on_error,
                                    on_close=on_close)
        ws.on_open = on_open
        # blocking - 이 endpoint will wait until websocket closes or timeout on client side.
        ws.run_forever()

        # after websocket closed, check DB
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT displayname, count, userid FROM transactions WHERE shop=%s AND userid=%s ORDER BY created_at DESC LIMIT 1;", (shop, userid))
                res = cur.fetchone()
        finally:
            conn.close()

        if res:
            return jsonify({"result": True, "username": res["displayname"], "count": res["count"], "id": res["userid"]})
        else:
            return jsonify({"result": False, "reason": "Transaction not found."})
    except Exception as e:
        print("bank handler error:", e)
        return jsonify({"result": False, "reason": "An unexpected error occurred."})

# 모바일(단축어)에서 생성하는 엔드포인트
@app.route("/ios", methods=["POST"])
def ios():
    try:
        obj = request.get_json(force=True)
        user_id = obj.get("user_id")
        shop = obj.get("shop")
        amount = int(obj.get("amount"))
        name = obj.get("name")

        conn = get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    # 중복(동일한 displayname, count, success=FALSE) 검사
                    cur.execute(
                        "SELECT id FROM ios_transactions WHERE displayname=%s AND count=%s AND success=%s;",
                        (name, amount, False)
                    )
                    existinfo = cur.fetchone()
                    if existinfo:
                        return jsonify({'result': False, 'message': '이미 동일한 입금요청이 존재합니다.'})

                    cur.execute(
                        "INSERT INTO ios_transactions (shop, userid, displayname, count, success) VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                        (shop, user_id, name, amount, False)
                    )
                    inserted = cur.fetchone()
                    inserted_id = inserted["id"] if inserted else None
        finally:
            conn.close()

        # 일정 시간 후 삭제 스레드 시작
        threading.Thread(target=delete_transaction_after_delay, args=(shop, user_id, name, amount), daemon=True).start()

        return jsonify({'result': True, 'message': '성공적으로 입금데이터가 생성되었습니다. 5분 후 자동으로 삭제됩니다.', 'id': inserted_id})
    except Exception as e:
        print("ios error:", e)
        return jsonify({'result': False, 'message': '서버 오류가 발생했습니다.'}), 500

# 알림 형태(리치 텍스트)를 분석해서 /ios/check로 전달 (POST)
@app.route("/ios/check", methods=["POST"])
def ios_check():
    try:
        obj = request.get_json(force=True)
        shop = obj.get("shop")
        messageText = obj.get("messageText", "")

        # 예시: 카카오뱅크(라인 인덱스 조정), 케이뱅크 등 기존 로직 재사용
        if '카카오뱅크' in messageText:
            message_list = messageText.split('\n')
            # 기존 코드대로 인덱스 적용
            amount = message_list[4].split('입금 ')[1]
            amount = amount.split('원')[0]
            amount = amount.replace(',', '')
            deposit_name = message_list[5]
        elif '케이뱅크' in messageText:
            message_list = messageText.split('\n')
            amount = message_list[3].split('입금 ')[1]
            amount = amount.split('원')[0]
            amount = amount.replace(',', '')
            deposit_name = message_list[5]
        else:
            return jsonify({'result': False, 'message': '아직 지원하지 않는 은행입니다.'})

        amount = int(amount)
        print("parsed:", amount, deposit_name)

        conn = get_conn()
        found = False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM ios_transactions WHERE shop=%s AND count=%s AND displayname=%s AND success=%s LIMIT 1;",
                        (shop, amount, deposit_name, False)
                    )
                    res = cur.fetchone()
                    if res:
                        tx_id = res["id"]
                        cur.execute(
                            "UPDATE ios_transactions SET success=%s WHERE id=%s;",
                            (True, tx_id)
                        )
                        found = True
        finally:
            conn.close()

        if found:
            return jsonify({'result': True, 'message': '충전을 성공적으로 확인하였습니다.'})
        else:
            return jsonify({'result': False, 'message': '충전을 확인하지 못했습니다.'})
    except Exception as e:
        print("ios_check error:", e)
        return jsonify({'result': False, 'message': '서버 오류가 발생했습니다.'}), 500

# 최종 확인(성공 기록 삭제) 엔드포인트
@app.route("/ios/check/success", methods=["POST"])
def ios_check_success():
    try:
        obj = request.get_json(force=True)
        user_id = obj.get("user_id")
        shop = obj.get("shop")
        amount = int(obj.get("amount"))
        name = obj.get("name")

        conn = get_conn()
        deleted = False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM ios_transactions WHERE shop=%s AND userid=%s AND count=%s AND displayname=%s AND success=%s LIMIT 1;",
                        (shop, user_id, amount, name, True)
                    )
                    res = cur.fetchone()
                    if res:
                        cur.execute(
                            "DELETE FROM ios_transactions WHERE id=%s;",
                            (res["id"],)
                        )
                        deleted = True
        finally:
            conn.close()

        if deleted:
            return jsonify({'result': True, 'message': '충전을 성공적으로 확인하였습니다.'})
        else:
            return jsonify({'result': False, 'message': '충전을 확인하지 못했습니다.'})
    except Exception as e:
        print("ios_check_success error:", e)
        return jsonify({'result': False, 'message': '서버 오류가 발생했습니다.'}), 500

if __name__ == "__main__":
    # 로컬 테스트용 (Render에서는 gunicorn 사용)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
