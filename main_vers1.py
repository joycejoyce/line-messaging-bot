import os
import io
import uuid
from flask import Flask, request, abort
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, VideoMessage

# 載入專案根目錄下的 .env 檔
load_dotenv()

# 從環境變數讀取 LINE 的密鑰資訊
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise Exception("請確認 .env 中已設定 LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN")

# 初始化 LINE Bot API 與 WebhookHandler
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 定義儲存檔案的目錄
OUTPUT_DIR = "./output"
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def save_to_local(file_stream, filename):
    """
    將 BytesIO 物件存檔到 OUTPUT_DIR 目錄下
    """
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(file_stream.getvalue())
    return filepath

# 建立 Flask Webhook 伺服器
app = Flask(__name__)

@app.route("/callback", methods=["POST"])
def callback():
    # 取得 HTTP Header 中的 X-Line-Signature
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        # 驗證並處理 LINE Webhook 事件
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK", 200

# 處理文字訊息：存成 .txt 檔案
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text
    user_id = event.source.user_id
    timestamp = event.timestamp
    filename = f"text_{user_id}_{timestamp}_{uuid.uuid4().hex}.txt"
    file_stream = io.BytesIO(text.encode("utf-8"))
    saved_path = save_to_local(file_stream, filename)
    print(f"已儲存文字訊息： {saved_path}")

# 處理圖片訊息：存成 .jpg 檔案
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    timestamp = event.timestamp
    filename = f"image_{user_id}_{timestamp}_{uuid.uuid4().hex}.jpg"
    file_stream = io.BytesIO(message_content.content)
    saved_path = save_to_local(file_stream, filename)
    print(f"已儲存圖片訊息： {saved_path}")

# 處理影片訊息：存成 .mp4 檔案
@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    timestamp = event.timestamp
    filename = f"video_{user_id}_{timestamp}_{uuid.uuid4().hex}.mp4"
    file_stream = io.BytesIO(message_content.content)
    saved_path = save_to_local(file_stream, filename)
    print(f"已儲存影片訊息： {saved_path}")

if __name__ == "__main__":
    # 測試時建議使用 ngrok 將本地端口暴露給外網，並將 ngrok 提供的 HTTPS URL 設定到 LINE 官方帳號管理後台的 Webhook URL
    app.run(port=5000)
