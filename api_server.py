import requests
from flask import Flask, request, jsonify
import uuid
import sqlite3
import websocket
import json
import datetime
from datetime import timedelta
import time
import threading

app = Flask(__name__)
app.secret_key = str(uuid.uuid4())

def get_now():
    return datetime.datetime.now()

@app.route("/bank", methods=['POST'])
def bank():
    try:
        request_time = datetime.datetime.now()
        req = request.get_json()
        bankpin = req.get("bankpin")
        key = req.get("api_key")
        shop = req.get("shop")
        userid = req.get("userid")
        username = req.get("userinfo")
        print(req)

        def on_message(ws, message):
            current_time = datetime.datetime.now()
            time_difference = current_time - request_time
            minutes_difference = time_difference.total_seconds() / 60

            if minutes_difference >= 3:
                ws.close()
                return {"result": False, "reason": "Could not confirm the deposit."}

            try:
                obj = json.loads(message)
                if obj["type"] == "push":
                    push = obj["push"]
                    body = push["body"].replace("\n", " ")
                    NotificationApplicationName = str(push["package_name"])
                    message = body.replace("원", "").replace(",", "").split(' ')
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
                        displayname = message[5]
                        count = message[1].replace("입금", "").replace("원", "").replace(",", "")
                        count = int(count)
                    elif NotificationApplicationName == "com.nonghyup.nhallonebank":  # 농협 올원
                        displayname = message[5]
                        count = message[1].replace("입금", "").replace("원", "").replace(",", "")
                        count = int(count)
                    elif NotificationApplicationName == "com.wooribank.smart.npib":  # 우리
                        sp = body.split(" ")
                        displayname = sp[1]
                        count = int(sp[5].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "com.kakaobank.channel":  # 카뱅
                        name = body.split(" ")
                        displayname = name[0]
                        title = push["title"]
                        sp = title.split(" ")
                        count = int(sp[1].replace("원", "").replace(",", ""))
                    elif NotificationApplicationName == "viva.republica.toss":  # 토뱅
                        displayname = push["body"].replace(" → 내 토스뱅크 통장", '')
                        count = int(push["title"].replace("원 입금", "").replace(",", ""))
                    else:
                        return {"result": False, "reason": "Notification application not recognized."}

                    if displayname == username:
                        if count == int(req.get("amount")):
                            con = sqlite3.connect("API.db")
                            cur = con.cursor()
                            cur.execute("INSERT INTO Transactions (shop, userid, displayname, count) VALUES (?, ?, ?, ?);",
                                        (shop, userid, displayname, count))
                            con.commit()
                            con.close()
                            ws.close()
                            return {"result": True, "count": count}
                        else:
                            return {"result": False, "reason": "Amount does not match."}
                    else:
                        return {"result": False, "reason": "Username does not match."}

            except Exception as e:
                return {"result": False, "reason": "An error occurred during message processing."}

        def on_error(ws, error):
            print("error:", error)

        def on_close(arg1, arg2, arg3):
            print("### closed ###")

        def on_open(ws):
            print("Opened")

        websocket.enableTrace(True)
        ws = websocket.WebSocketApp("wss://stream.pushbullet.com/websocket/" + bankpin,
                                    on_message=on_message,
                                    on_error=on_error,
                                    on_close=on_close)
        ws.on_open = on_open
        ws.run_forever()

        con = sqlite3.connect("API.db")
        cur = con.cursor()
        cur.execute("SELECT displayname, count, userid FROM Transactions WHERE shop = ? AND userid = ?;", (shop, userid))
        res = cur.fetchone()
        con.close()

        if res:
            return {"result": True, 'username': res[0], 'count': res[1], 'id': res[2]}
        else:
            return {"result": False, "reason": "Transaction not found."}

    except Exception as e:
        return {"result": False, "reason": "An unexpected error occurred."}
    

def delete_transaction_after_delay(shop, user_id, name, amount):
    time.sleep(300)
    con = sqlite3.connect("API.db")
    cur = con.cursor()
    cur.execute("DELETE FROM IosTransactions WHERE shop = ? AND userid = ? AND displayname = ? AND count = ? AND success = ?;",
                (shop, user_id, name, amount, 0))
    con.commit()
    con.close()

@app.route("/ios", methods=["POST"])
def ios():
    obj = request.get_json()
    user_id = obj.get("user_id")
    shop = obj.get("shop")
    amount = obj.get("amount")
    name = obj.get("name")
    
    con = sqlite3.connect("API.db")
    cur = con.cursor()
    cur.execute("SELECT * FROM IosTransactions WHERE displayname = ? AND count = ? AND success = ?;", (name, amount, 0))
    existinfo = cur.fetchone()
    con.close()

    if existinfo:
        return jsonify({'result': False, 'message': '이미 동일한 입금요청이 존재합니다.'})
    
    con = sqlite3.connect("API.db")
    cur = con.cursor()
    cur.execute("INSERT INTO IosTransactions (shop, userid, displayname, count, success) VALUES (?, ?, ?, ?, ?);",
                (shop, user_id, name, amount, 0))
    con.commit()
    con.close()

    threading.Thread(target=delete_transaction_after_delay, args=(shop, user_id, name, amount)).start()

    return jsonify({'result': True, 'message': '성공적으로 입금데이터가 생성되었습니다. 5분 후 자동으로 삭제됩니다.'})

@app.route("/ios/check", methods=["POST"])
def ios_check():
    obj = request.get_json()
    shop=obj.get("shop")
    messageText=obj.get("messageText")

    print(messageText)
    if '카카오뱅크' in messageText:
        message_list = messageText.split('\n')
        amount = message_list[4].split('입금 ')[1]
        amount = amount.split('원')[0]
        amount=amount.replace(',','')
        deposit_name = message_list[5]
    elif '케이뱅크' in messageText:
        message_list = messageText.split('\n')
        amount = message_list[3].split('입금 ')[1]
        amount = amount.split('원')[0]
        amount=amount.replace(',','')
        deposit_name = message_list[5]
    else:


        return jsonify({'result': False, 'message': '아직 지원하지 않는 은행입니다.'})
    print(amount,deposit_name)

    con = sqlite3.connect("API.db")
    cur = con.cursor()      
    cur.execute("SELECT * FROM IosTransactions WHERE shop = ? AND count = ? AND displayname = ? AND success = ?;", (shop, amount,deposit_name,0))
    res = cur.fetchone()
    if res:
        
        cur.execute("UPDATE IosTransactions SET success = ? WHERE shop = ? AND count = ? AND displayname = ?;", (1, shop, amount,deposit_name))
        con.commit()
    con.close()

    if res:

        return jsonify({'result': True, 'message': '충전을 성공적으로 확인하였습니다.'})
    else:
        
        return jsonify({'result': False, 'message': '충전을 확인하지 못했습니다.'})

@app.route("/ios/check/success", methods=["POST"])
def ios_check_success():
    
    obj = request.get_json()
    user_id = obj.get("user_id")
    shop = obj.get("shop")
    amount=obj.get("amount")
    name=obj.get("name")


    con = sqlite3.connect("API.db")
    cur = con.cursor()  
    cur.execute("SELECT * FROM IosTransactions WHERE shop = ? AND userid = ? AND count = ? AND displayname = ? AND success = ?;", (shop,user_id, amount,name,1))
    res = cur.fetchone()
    
    if res:
        
        cur.execute("DELETE FROM IosTransactions WHERE shop = ? AND userid = ? AND count = ? AND displayname = ? AND success = ?;",
                    (shop, user_id, amount, name,1))
        con.commit()
    con.close()
    if res:
        return jsonify({'result': True, 'message': '충전을 성공적으로 확인하였습니다.'})
    
    else:
        
        return jsonify({'result': False, 'message': '충전을 확인하지 못했습니다.'})

app.run(debug=False, host='0.0.0.0', port=80)
