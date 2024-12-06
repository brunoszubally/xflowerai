from flask import Flask, request, jsonify, make_response, session, redirect
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
from email.mime.application import MIMEApplication
from threading import Lock, Timer
from dotenv import load_dotenv
from datetime import datetime, timedelta
import secrets
from fpdf import FPDF

# Környezeti változók betöltéses
load_dotenv()

app = Flask(__name__)
app.secret_key = "supersecretkey123"

# CORS beállítások egyszerűsítése
CORS(app, 
     origins=["https://xflower.ai"],
     methods=["GET", "POST", "OPTIONS"],
     allow_headers=["Content-Type", "X-Session-ID"],
     supports_credentials=True)

# Session konfiguráció
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_NAME='xflower_session'
)

# Környezeti változók (Most már a .env fájlból töltődnek be)
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))  # Alapértelmezett érték megadása
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
BCC_EMAIL = os.getenv("BCC_EMAIL")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
INACTIVITY_TIMEOUT = 600  # 10 perc másodpercekben

# OpenAI beállítások
openai.api_key = OPENAI_API_KEY

# Logolás beállítása
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# A4 méret 300 DPI-vel (mm to pixels at 300 DPI)
A4_WIDTH = int(297 * 11.811)  # 297mm * (300/25.4)
A4_HEIGHT = int(210 * 11.811)  # 210mm * (300/25.4)

# Thread tárolás módosítása - hozzáadjunk időbélyeget és cleanup funkciót
user_threads = {
    # 'session_id': {'thread_id': 'xxx', 'last_used': datetime}
}
thread_lock = Lock()

# Konstans a thread élettartamához
THREAD_LIFETIME_HOURS = 24

# Új globális változók
conversation_timers = {}
conversation_history = {}

def cleanup_old_threads():
    """Régi thread-ek törlése"""
    current_time = datetime.now()
    with thread_lock:
        for session_id in list(user_threads.keys()):
            if current_time - user_threads[session_id]['last_used'] > timedelta(hours=THREAD_LIFETIME_HOURS):
                try:
                    # OpenAI thread törlése
                    openai.beta.threads.delete(user_threads[session_id]['thread_id'])
                    logger.info(f"Thread törölve: {user_threads[session_id]['thread_id']} (session: {session_id})")
                except Exception as e:
                    logger.error(f"Hiba a thread törlésekor: {str(e)}")
                del user_threads[session_id]

def get_or_create_thread(session_id):
    """Thread kezelése egy session-höz"""
    with thread_lock:
        if session_id in user_threads:
            thread_data = user_threads[session_id]
            # Frissítjük az utolsó hasznlat időpontját
            thread_data['last_used'] = datetime.now()
            logger.debug(f"Meglévő thread használata: {thread_data['thread_id']} (session: {session_id})")
            return thread_data['thread_id']
        else:
            try:
                thread = openai.beta.threads.create()
                user_threads[session_id] = {
                    'thread_id': thread.id,
                    'last_used': datetime.now()
                }
                logger.debug(f"Új thread létrehozva: {thread.id} (session: {session_id})")
                return thread.id
            except Exception as e:
                logger.error(f"Hiba új thread létrehozásakor: {str(e)}")
                return None

def clean_plantuml_notes(plantuml_code):
    """Eltávolítja a zárójeleket a note sorokból"""
    cleaned_lines = []
    for line in plantuml_code.split('\n'):
        if line.strip().lower().startswith('note'):
            # Zárójelek eltávolítása a sorból
            cleaned_line = line.replace('(', '').replace(')', '')
            cleaned_lines.append(cleaned_line)
        else:
            cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

def generate_plantuml_with_assistant(user_message, session_id):
    logger.debug(f"PlantUML generálás indítása: {user_message}, session: {session_id}")

    # Thread kezelés session alapján
    thread_id = get_or_create_thread(session_id)
    if not thread_id:
        return None, None

    max_attempts = 3
    attempt = 0
    
    while attempt < max_attempts:
        try:
            # Üzenet küldése
            message = openai.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=f""" ALWAYS GIVE THE SAME LANGUAGE AS THE USERS INPUT!  Create PlantUML Activity diagram code for this business process, ensuring the code strictly follows PlantUML syntax.   Only return the PlantUML code, which should include extra PLANTUML notes for steps.  The output should be in in the input language, and return nothing else but the PlantUML code. Don't use swimlanes! Always remember and modify based on previous processes in one conversation!
User input: {user_message}"""
            )
            logger.debug(f"OpenAI üzenet elküldve: {message}")

            # Futtatás indítása
            run = openai.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=ASSISTANT_ID,
            )
            logger.debug(f"OpenAI futtatás indítva: {run}")

            # Státusz ellenőrzése
            status = check_status(run.id, thread_id)
            while status != "completed":
                logger.debug(f"Várakozás a futás befejezésére, jelenlegi státusz: {status}")
                status = check_status(run.id, thread_id)
                time.sleep(2)

            # Válasz lekérése
            response = openai.beta.threads.messages.list(thread_id=thread_id)
            logger.debug(f"OpenAI válasz: {response}")
            
            if not response.data:
                logger.error("Nem sikerült asszisztens válaszát lekérni.")
                attempt += 1
                continue

            assistant_response = response.data[0].content[0].text.value
            logger.debug(f"Asszisztens válasza: {assistant_response}")
            
            # PlantUML kód validáció
            if not "@startuml" in assistant_response or not "@enduml" in assistant_response:
                logger.warning("Hiányzó @startuml vagy @enduml a válaszból, újrapróbálkozás...")
                attempt += 1
                continue

            # További PlantUML szintaxis ellenőrzések
            if not "start" in assistant_response.lower() or not "stop" in assistant_response.lower():
                logger.warning("Hiányzó start/stop elemek a PlantUML kódból, újrapróbálkozás...")
                attempt += 1
                continue

            cleaned_response = assistant_response.replace("```plantuml", "").rstrip("`").strip()
            cleaned_response = cleaned_response.replace(
                '@startuml',
                '@startuml\nskinparam ConditionEndStyle hline\nskinparam defaultFontName Montserrat'
            )
            # Note-ok tisztítása
            cleaned_response = clean_plantuml_notes(cleaned_response)
            logger.debug(f"Tisztított válasz: {cleaned_response}")

            return thread_id, cleaned_response

        except Exception as e:
            logger.error(f"Hiba történt a PlantUML generálás során: {str(e)}")
            attempt += 1
            time.sleep(2)

    return None, None

def check_status(run_id, thread_id):
    run = openai.beta.threads.runs.retrieve(
        thread_id=thread_id,
        run_id=run_id,
    )
    return run.status

def compress_and_encode_plantuml(plantuml_code):
    compressed = zlib.compress(plantuml_code.encode('utf-8'))
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

def create_pdf_report(session_id):
    """PDF jelentés készítése a beszélgetés történetéből"""
    pdf = FPDF()
    
    if session_id in conversation_history:
        history = conversation_history[session_id]
        
        for idx, entry in enumerate(history, 1):
            pdf.add_page()
            try:
                pdf.add_font('Montserrat', '', 'Montserrat-Regular.ttf', uni=True)
                pdf.set_font('Montserrat', size=12)
            except:
                pdf.set_font('Arial', size=12)  # Fallback font
            
            # Fejléc
            pdf.cell(0, 10, f'Folyamat {idx} - {datetime.now().strftime("%Y-%m-%d %H:%M")}', ln=True)
            pdf.ln(5)
            
            # Prompt
            pdf.cell(0, 10, 'Felhasználói kérés:', ln=True)
            pdf.multi_cell(0, 10, str(entry['prompt']))
            pdf.ln(5)
            
            # Diagram kép
            if 'image' in entry:
                try:
                    # Base64 kép dekódolása
                    image_data = entry['image'].split('base64,')[1]
                    image_bytes = base64.b64decode(image_data)
                    
                    # Ideiglenes fájl létrehozása a képnek
                    temp_image_path = f'temp_diagram_{idx}.png'
                    with open(temp_image_path, 'wb') as f:
                        f.write(image_bytes)
                    
                    # Kép hozzáadása a PDF-hez
                    page_width = pdf.w - 20  # Margók
                    pdf.image(temp_image_path, x=10, y=pdf.get_y(), w=page_width)
                    
                    # Ideiglenes fájl törlése
                    os.remove(temp_image_path)
                except Exception as e:
                    logger.error(f"Hiba a kép PDF-be illesztésekor: {str(e)}")
                    pdf.multi_cell(0, 10, "Hiba történt a diagram betöltésekor")
            
            pdf.ln(10)
    
    return pdf.output(dest='S').encode('latin-1')

def send_inactivity_email(session_id):
    """E-mail küldése inaktivitás esetén"""
    try:
        pdf_content = create_pdf_report(session_id)
        
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = ADMIN_EMAIL
        msg['Subject'] = f"Inaktív beszélgetés jelentés - {session_id}"
        
        body = f"""
        Egy beszélgetés 10 perce inaktív.
        Session ID: {session_id}
        Utolsó aktivitás: {datetime.now() - timedelta(seconds=INACTIVITY_TIMEOUT)}
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # PDF csatolása
        pdf_attachment = MIMEApplication(pdf_content, _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'conversation_{session_id}.pdf')
        msg.attach(pdf_attachment)
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
            
        logger.info(f"Inaktivitási e-mail elküldve: {session_id}")
        
    except Exception as e:
        logger.error(f"Hiba az inaktivitási e-mail küldésekor: {str(e)}")

def reset_inactivity_timer(session_id):
    """Időzítő újraindítása vagy létrehozása"""
    # Előző időzítő törlése, ha létezik
    if session_id in conversation_timers:
        conversation_timers[session_id].cancel()
    
    # Új időzítő létrehozása
    timer = Timer(INACTIVITY_TIMEOUT, send_inactivity_email, args=[session_id])
    timer.start()
    conversation_timers[session_id] = timer

@app.route('/init-session', methods=['POST', 'OPTIONS'])
def init_session():
    if request.method == "OPTIONS":
        return make_response()
        
    try:
        session_id = secrets.token_urlsafe(32)
        session['session_id'] = session_id
        response = jsonify({'session_id': session_id})
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chat', methods=['POST', 'OPTIONS'])
def chat():
    if request.method == "OPTIONS":
        return make_response()
    
    # Session ID ellenőrzése headerből
    session_id = request.headers.get('X-Session-ID')
    if not session_id:
        return jsonify({'error': 'Hiányzó session ID'}), 401
    
    try:
        data = request.get_json()
        user_message = data['message']
        
        # Beszélgetés történet inicializálása, ha még nem létezik
        if session_id not in conversation_history:
            conversation_history[session_id] = []
        
        max_attempts = 3
        attempt = 0

        while attempt < max_attempts:
            thread_id, plantuml_code = generate_plantuml_with_assistant(user_message, session_id)
            if not plantuml_code:
                attempt += 1
                continue
            
            encoded_uml = compress_and_encode_plantuml(plantuml_code)
            plantuml_url = f"http://www.plantuml.com/plantuml/svg/~1{encoded_uml}"
            
            response = requests.get(plantuml_url)
            if response.status_code != 200:
                attempt += 1
                continue

            try:
                # SVG konvertálása PNG-vé
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
                image_data = f'data:image/jpeg;base64,{jpg_base64}'
                
                # Beszélgetés történet frissítése a k��ppel együtt
                conversation_history[session_id].append({
                    'prompt': user_message,
                    'plantuml': plantuml_code,
                    'image': image_data  # Kép mentése base64 formátumban
                })
                
                # Időzítő újraindítása
                reset_inactivity_timer(session_id)
                
                return jsonify({
                    'image': image_data,
                    'thread_id': thread_id
                })

            except Exception as e:
                logger.error(f"Hiba az SVG feldolgozása során: {str(e)}")
                attempt += 1
                continue

        return jsonify({'error': 'Nem sikerült érvényes diagramot generálni többszöri próbálkozás után sem.'}), 500

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

        # A4-es kép l��trehozása
        a4_image = create_a4_image(image_data, recipient_name)
        
        # Kép mentése BytesIO objektumba
        output = BytesIO()
        a4_image.save(output, format='JPEG', quality=95, dpi=(300, 300))
        output.seek(0)

        msg = MIMEMultipart('alternative')
        msg['From'] = SMTP_USER
        msg['To'] = recipient_email
        msg['Subject'] = "Folyamatábra az xFLOWer.ai-tól"
        msg['Bcc'] = BCC_EMAIL

        # HTML verzió
        html = f"""
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f4f4f4; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; background-color: #ffffff; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
        h1 {{ color: #b42325; }}
        .cta {{ background-color: #b42325; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 3px; display: inline-block; }}
        .footer {{ margin-top: 20px; font-size: 12px; color: #777; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Köszönjük, hogy az xFLOWer.ai-t használtad!</h1>
        
        <p>Kedves {recipient_name}!</p>
        
        <p>Örömmel értesítünk, hogy a folyamatábrád elkészült, melyet ezen e-mail csatolmányaként küldünk el Neked.</p>
        
        <p>Az <strong>xFLOWer workflow platformmal</strong> villámgyorsan tudunk Neked működő, testreszabott folyamatokat létrehozni. Legyen szó bármilyen zleti folyamatról, mi segítünk azt hatékonyan digitalizálni és automatizálni.</p>
        
        <p>Ha szeretnéd megtapasztalni, hogyan teheted még gördülékenyebbé vállalkozásod működését, vedd fel velünk a kapcsolatot:</p>
        
        <p>
            Telefon: <strong>+36 1 469 0001</strong><br>
            E-mail: <a href="mailto:sales@xflower.hu">sales@xflower.hu</a>
        </p>
        
        <p><a href="https://xflower.hu" class="cta">Látogass el weboldalunkra</a></p>
        
        <p>Várjuk megkeresésed!</p>
        
        <p>Üdvözlettel,<br>Az xFLOWer csapata</p>
        
        <div class="footer">
            © 2024 xFLOWer.ai. Minden jog fenntartva.<br>
            <a href="https://xflower.hu">https://xflower.hu</a>
        </div>
    </div>
</body>
</html>
"""

        # Plain text verzió
        text = f"""
Kedves {recipient_name}!

Köszönjük, hogy az xFLOWer.ai-t használtad a folyamatábra elkészítéséhez, melyet ezen e-mail csatolmányaként küldtünk el most Neked.

Az xFLOWer workflow platformmal villámgyorsan tudunk Neked működő, testreszabott folyamatokat létrehozni. Legyen szó bármilyen üzleti folyamatról, mi segítünk azt hatékonyan digitalizálni és automatizálni.

Ha szeretnéd megtapasztalni, hogyan teheted még gördülékenyebb vállalkozásod működését, vedd fel velünk a kapcsolatot:

Telefon: +36 1 469 0001
E-mail: sales@xflower.hu

Várjuk megkeresésed!

Üdvözlettel,
Az xFLOWer csapata

© 2024 xFLOWer.ai. Minden jog fenntartva.
https://xflower.hu
"""

        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')

        msg.attach(part1)
        msg.attach(part2)

        # Folyamatábra csatolása
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
    
@app.route('/network-test')
def network_test():
    import socket
    import requests
    results = {}

    # DNS feloldás tesztelése
    try:
        ip = socket.gethostbyname('api.openai.com')
        results['dns_resolution'] = f"Az api.openai.com IP címe: {ip}"
    except socket.gaierror as e:
        results['dns_resolution'] = f"DNS feloldási hiba: {e}"

    # HTTPS kérés tesztelése
    try:
        response = requests.get('https://api.openai.com/v1')
        results['http_request'] = f"HTTP válasz kód: {response.status_code}"
    except requests.exceptions.RequestException as e:
        results['http_request'] = f"HTTP kérés hiba: {e}"

    return jsonify(results)

@app.route('/end-session', methods=['POST', 'OPTIONS'])
def end_session():
    if request.method == "OPTIONS":
        return make_response()
    
    try:
        session_id = request.headers.get('X-Session-ID')
        if not session_id:
            return jsonify({'error': 'Hiányzó session ID'}), 401
            
        # Időzítő leállítása és törlése
        if session_id in conversation_timers:
            conversation_timers[session_id].cancel()
            del conversation_timers[session_id]
            
        # Beszélgetés történet törlése
        if session_id in conversation_history:
            del conversation_history[session_id]
            
        # Thread törlése
        if session_id in user_threads:
            try:
                openai.beta.threads.delete(user_threads[session_id]['thread_id'])
            except Exception as e:
                logger.error(f"Hiba a thread törlésekor: {str(e)}")
            del user_threads[session_id]
            
        # Session törlése
        session.clear()
        
        return jsonify({'success': True, 'message': 'Session sikeresen lezárva'})
        
    except Exception as e:
        logger.error(f"Hiba a session lezárásakor: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 