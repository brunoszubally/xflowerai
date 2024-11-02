from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import openai
import time
import zlib
import requests
import logging
import os
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import cairosvg
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from threading import Lock
from dotenv import load_dotenv

# Környezeti változók betöltése
load_dotenv()

app = Flask(__name__)
CORS(app)

# Környezeti változók (Most már a .env fájlból töltődnek be)
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))  # Alapértelmezett érték megadása
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

# OpenAI beállítások
openai.api_key = OPENAI_API_KEY

# Logolás beállítása
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# A4 méret 300 DPI-vel (mm to pixels at 300 DPI)
A4_WIDTH = int(297 * 11.811)  # 297mm * (300/25.4)
A4_HEIGHT = int(210 * 11.811)  # 210mm * (300/25.4)

# Thread tárolás
user_threads = {}
thread_lock = Lock()

@app.before_request
def before_request():
    if request.method == "OPTIONS":
        response = make_response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "*")
        response.headers.add("Access-Control-Allow-Methods", "*")
        return response

def generate_plantuml_with_assistant(user_input, user_id):
    logger.debug(f"PlantUML generálás indítása: {user_input}, user_id: {user_id}")

    with thread_lock:
        thread_id = user_threads.get(user_id)
        if not thread_id:
            thread = openai.beta.threads.create()
            thread_id = thread.id
            user_threads[user_id] = thread_id
            logger.debug(f"Új thread ID létrehozva: {thread_id}")

    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=f"Create PlantUML Activity diagram code for this business process, ensuring the code strictly follows PlantUML syntax. Only return the PlantUML code, which should include extra notes for steps. The output should be in the language of the users input language, and return nothing else but the PlantUML code.(with the notes of course, note left and note right). Don't use swimlanes!  ALWAYS GIVE THE SAME LANGUAGE AS ASKED! Always remember and modify based on previous processes in one conversation! User input:{user_input}"
    )

    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
    )

    status = check_status(run.id, thread_id)
    while status != "completed":
        logger.debug(f"Várakozás a futás befejezésére, jelenlegi státusz: {status}")
        status = check_status(run.id, thread_id)
        time.sleep(2)

    response = openai.beta.threads.messages.list(thread_id=thread_id)
    
    if not response.data:
        logger.error("Nem sikerült asszisztens válaszát lekérni.")
        return None, None

    assistant_response = response.data[0].content[0].text.value
    cleaned_response = assistant_response.replace("```plantuml", "").rstrip("`").strip()
    cleaned_response = cleaned_response.replace(
        '@startuml',
        '@startuml\nskinparam ConditionEndStyle hline\nskinparam defaultFontName Montserrat'
    )

    return thread_id, cleaned_response

def check_status(run_id, thread_id):
    run = openai.beta.threads.runs.retrieve(
        thread_id=thread_id,
        run_id=run_id,
    )
    return run.status

def compress_and_encode_plantuml(plantuml_code):
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(plantuml_code.encode('utf-8'))
    compressed += compressor.flush()
    return encode64_for_ascii(compressed)

def encode64_for_ascii(bytes_data):
    base64_chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_'
    result = ''
    i = 0
    while i < len(bytes_data):
        b1 = bytes_data[i] if i < len(bytes_data) else 0
        b2 = bytes_data[i + 1] if i + 1 < len(bytes_data) else 0
        b3 = bytes_data[i + 2] if i + 2 < len(bytes_data) else 0
        
        c1 = b1 >> 2
        c2 = ((b1 & 0x3) << 4) | (b2 >> 4)
        c3 = ((b2 & 0xF) << 2) | (b3 >> 6)
        c4 = b3 & 0x3F
        
        result += (base64_chars[c1] + base64_chars[c2] +
                  (base64_chars[c3] if i + 1 < len(bytes_data) else '') +
                  (base64_chars[c4] if i + 2 < len(bytes_data) else ''))
        i += 3
    
    return result

def create_a4_image(image_data, recipient_name):
    # Base64 kép feldolgozása
    image_bytes = base64.b64decode(image_data.split('base64,')[1])
    image = Image.open(BytesIO(image_bytes))
    
    # Új A4 méretű kép létrehozása fehér háttérrel
    a4_image = Image.new('RGB', (A4_WIDTH, A4_HEIGHT), 'white')
    draw = ImageDraw.Draw(a4_image)
    margin = int(A4_WIDTH * 0.02)

    # Cím hozzáadása
    try:
        font = ImageFont.truetype('Montserrat-Bold.ttf', size=int(A4_WIDTH * 0.02))
    except Exception as e:
        logger.warning(f"Nem sikerült a Montserrat betöltése: {e}")
        font = ImageFont.load_default()

    title = "A Te xFLOWer folyamatod"
    title_bbox = draw.textbbox((0, 0), title, font=font)
    title_width = title_bbox[2] - title_bbox[0]
    title_height = title_bbox[3] - title_bbox[1]
    title_x = (A4_WIDTH - title_width) // 2
    title_y = margin

    draw.text((title_x, title_y), title, font=font, fill='black')

    # Logo betöltése és méretezése
    logo = Image.open('logo2.png')
    if logo.mode != 'RGBA':
        logo = logo.convert('RGBA')
    
    logo_width = int(A4_WIDTH * 0.15)
    logo_height = int(logo.height * (logo_width / logo.width))
    logo = logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)

    # Diagram méretezése és pozicionálása
    diagram_y = title_y + title_height + margin
    max_diagram_width = A4_WIDTH - (margin * 2)
    max_diagram_height = A4_HEIGHT - diagram_y - logo_height - (margin * 2)

    diagram_ratio = min(
        max_diagram_width / image.width,
        max_diagram_height / image.height
    )
    new_width = int(image.width * diagram_ratio)
    new_height = int(image.height * diagram_ratio)
    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    diagram_x = (A4_WIDTH - new_width) // 2
    diagram_y = diagram_y + ((max_diagram_height - new_height) // 2)
    a4_image.paste(image, (diagram_x, diagram_y))

    # Logo és weboldal cím hozzáadása
    logo_position = (margin, A4_HEIGHT - logo_height - margin)
    if logo.mode == 'RGBA':
        alpha = logo.split()[3]
        a4_image.paste(logo, logo_position, mask=alpha)

    # Weboldal cím
    website_text = "xflower.hu"
    website_font_size = int(logo_height * 0.5)
    try:
        website_font = ImageFont.truetype('Montserrat-Bold.ttf', size=website_font_size)
    except:
        website_font = ImageFont.load_default()

    website_bbox = draw.textbbox((0, 0), website_text, font=website_font)
    website_width = website_bbox[2] - website_bbox[0]
    website_x = A4_WIDTH - website_width - margin
    website_y = A4_HEIGHT - website_bbox[3] - margin

    draw.text((website_x, website_y), website_text, font=website_font, fill='black')

    return a4_image

@app.route('/chat', methods=['POST'])
def generate_diagram():
    try:
        data = request.get_json()
        user_message = data['message']
        user_id = request.remote_addr

        thread_id, plantuml_code = generate_plantuml_with_assistant(user_message, user_id)
        if not plantuml_code:
            return jsonify({'error': 'Nem sikerült PlantUML kódot generálni.'}), 500

        encoded_uml = compress_and_encode_plantuml(plantuml_code)
        plantuml_url = f"http://www.plantuml.com/plantuml/svg/~1{encoded_uml}"
        
        response = requests.get(plantuml_url)
        if response.status_code != 200:
            return jsonify({'error': 'Nem sikerült az SVG lekérése.'}), 500

        # SVG konvertálása nagy felbontású PNG-vé
        png_data = BytesIO()
        cairosvg.svg2png(
            bytestring=response.text.encode('utf-8'),
            write_to=png_data,
            dpi=300,
            scale=2,
            background_color='white'
        )
        png_data.seek(0)
        
        # Base64 kódolás
        jpg_base64 = base64.b64encode(png_data.getvalue()).decode('utf-8')
        
        return jsonify({
            'image': f'data:image/jpeg;base64,{jpg_base64}',
            'thread_id': thread_id
        })

    except Exception as e:
        logger.error(f"Hiba történt: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/send-email', methods=['POST'])
def send_email():
    try:
        data = request.get_json()
        recipient_name = data.get('name')
        recipient_email = data.get('email')
        image_data = data.get('image')
        
        if not all([recipient_name, recipient_email, image_data]):
            return jsonify({'error': 'Hiányzó adatok'}), 400

        # A4-es kép létrehozása
        a4_image = create_a4_image(image_data, recipient_name)
        
        # Kép mentése BytesIO objektumba
        output = BytesIO()
        a4_image.save(output, format='JPEG', quality=95, dpi=(300, 300))
        output.seek(0)

        # E-mail összeállítása és küldése
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = recipient_email
        msg['Subject'] = "Folyamatábra az xFLOWer.ai-tól"
        
        # **BCC** címzett hozzáadása
        msg['Bcc'] = 'brunoszubally@gmail.com'

        # Plain Text formátumú törzs
        text_body = f"""
Kedves {recipient_name}!

Köszönjük, hogy az xFLOWer.ai-t használtad a folyamatábra elkészítéséhez.

Üdvözlettel,
Az xFLOWer.ai csapata
pip
----------------------------------------
© 2024 xFLOWer.ai. Minden jog fenntartva.
https://xflower.hu
"""

        # MIMEText objektum létrehozása csak plain text részt
        part1 = MIMEText(text_body, 'plain', 'utf-8')

        # Csak a plain text részt csatoljuk
        msg.attach(part1)

        # Kép csatolása
        image_attachment = MIMEImage(output.getvalue())
        image_attachment.add_header('Content-Disposition', 'attachment', filename='xflower_folyamatabra.jpg')
        msg.attach(image_attachment)

        # E-mail küldése
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        return jsonify({'success': True, 'message': 'E-mail sikeresen elküldve'})

    except Exception as e:
        logger.error(f"Hiba az e-mail küldése során: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
