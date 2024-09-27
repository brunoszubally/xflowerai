import openai
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import zlib
import requests
import logging
import os

# Flask app létrehozása
app = Flask(__name__)
CORS(app)

# Logolás beállítása
logging.basicConfig(level=logging.DEBUG)  # Alapértelmezett logolási szint
logger = logging.getLogger(__name__)

# OpenAI API kulcs és asszisztens azonosító
openai.api_key = os.getenv("API_KEY")
assistant_id = os.getenv("ASSISTANT_KEY")

# Tároljuk a thread ID-ket egy globális dictionary-ben
user_threads = {}

# OpenAI asszisztens használata a PlantUML generálásához
def generate_plantuml_with_assistant(user_input, user_id):
    logger.debug(f"PlantUML generálás indítása: {user_input}, user_id: {user_id}")

    # Ha a felhasználó már rendelkezik thread ID-val, akkor használjuk azt
    if user_id in user_threads:
        thread_id = user_threads[user_id]
        logger.debug(f"Korábbi thread ID megtalálva: {thread_id}")
    else:
        # Ha nincs még thread, létrehozunk egy újat
        thread = openai.beta.threads.create()
        thread_id = thread.id
        user_threads[user_id] = thread_id
        logger.debug(f"Új thread ID létrehozva: {thread_id}")

    # Küldjük a felhasználói üzenetet a meglévő thread-be
    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=f"Create PlantUML Activity diagram code for this business process, ensuring the code strictly follows PlantUML syntax.   Only return the PlantUML code, which should include extra notes for steps. The output should be in Hungarian, and return nothing else but the PlantUML code. (with the notes of course, note left and note right).  Don't use swimlanes! Always remember and modify based on previous processes in one conversation!{user_input}"
    )

    # Indítsuk el az asszisztenst a thread-ben
    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=assistant_id,
    )
    logger.debug(f"Futás indítva, run_id: {run.id}")

    # Ellenőrizzük a futás státuszát
    run_id = run.id
    status = check_status(run_id, thread_id)
    logger.debug(f"Futás státusz ellenőrzése: {status}")

    while status != "completed":
        logger.debug(f"Várakozás a futás befejezésére, jelenlegi státusz: {status}")
        status = check_status(run_id, thread_id)
        time.sleep(2)

    # Kérjük le az asszisztens válaszát
    response = openai.beta.threads.messages.list(
        thread_id=thread_id
    )
    logger.debug(f"Válaszok száma: {len(response.data)}")

    if response.data:
        assistant_response = response.data[0].content[0].text.value
        cleaned_response = assistant_response.replace("```plantuml", "").rstrip("`").strip()

        # Hozzáadjuk a "skinparam" sorokat
        cleaned_response = cleaned_response.replace(
            '@startuml',
            '@startuml\nskinparam ConditionEndStyle hline\nskinparam defaultFontName Montserrat'
        )
        logger.debug(f"PlantUML kód tisztítva: {cleaned_response}")

        return cleaned_response

    logger.error(f"Nem sikerült asszisztens válaszát lekérni.")
    return None

# Funkció a futás státuszának ellenőrzésére
def check_status(run_id, thread_id):
    run = openai.beta.threads.runs.retrieve(
        thread_id=thread_id,
        run_id=run_id,
    )
    logger.debug(f"Futás státusz lekérve: {run.status}")
    return run.status

# PlantUML kód tömörítése és kódolása ASCII formátumba
def encode64_for_ascii(bytes_data):
    base64_chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_'
    result = ''
    i = 0

    while i < len(bytes_data):
        # Első karakter
        result += base64_chars[bytes_data[i] >> 2]

        # Második karakter
        if i + 1 < len(bytes_data):
            result += base64_chars[((bytes_data[i] & 0x03) << 4) | (bytes_data[i + 1] >> 4)]
        else:
            result += base64_chars[(bytes_data[i] & 0x03) << 4]
            break

        # Harmadik karakter
        if i + 2 < len(bytes_data):
            result += base64_chars[((bytes_data[i + 1] & 0x0f) << 2) | (bytes_data[i + 2] >> 6)]
            result += base64_chars[bytes_data[i + 2] & 0x3f]
        else:
            result += base64_chars[(bytes_data[i + 1] & 0x0f) << 2]
            break

        i += 3

    return result

# PlantUML kód tömörítése zlib-bel, majd ASCII formátumba konvertálása
def compress_and_encode_plantuml(plantuml_code):
    plantuml_bytes = plantuml_code.encode('utf-8')
    compressed = zlib.compress(plantuml_bytes, 9)
    logger.debug(f"PlantUML kód tömörítve és kódolva.")
    return encode64_for_ascii(compressed)

@app.route('/generate', methods=['POST'])
def generate_diagram():
    data = request.get_json()

    if 'message' not in data:
        logger.error("Nincs üzenet a kérésben!")
        return jsonify({'error': 'Nincs üzenet!'}), 400

    user_message = data['message']
    logger.debug(f"Üzenet fogadva: {user_message}")

    # Felhasználói azonosító lekérése (itt pl. az IP-címet használjuk az egyszerűség kedvéért)
    user_id = request.remote_addr
    logger.debug(f"Felhasználói azonosító: {user_id}")

    # PlantUML kód generálása az OpenAI asszisztenssel
    plantuml_code = generate_plantuml_with_assistant(user_message, user_id)

    if plantuml_code is None:
        logger.error("Nem sikerült PlantUML kódot generálni.")
        return jsonify({'error': 'Nem sikerült PlantUML kódot generálni.'}), 500

    # PlantUML kód tömörítése és kódolása
    encoded_uml = compress_and_encode_plantuml(plantuml_code)

    # PlantUML szerver URL létrehozása
    plantuml_url = f"http://www.plantuml.com/plantuml/svg/~1{encoded_uml}"
    logger.debug(f"PlantUML URL: {plantuml_url}")

    # SVG tartalom lekérése a PlantUML szerverről
    response = requests.get(plantuml_url)
    if response.status_code == 200:
        logger.debug("SVG sikeresen lekérve a PlantUML szerverről.")
        svg_content = response.text
        return jsonify({'svg': svg_content})
    else:
        logger.error(f"Nem sikerült az SVG lekérése a PlantUML szerverről, státusz: {response.status_code}")
        return jsonify({'error': 'Nem sikerült az SVG lekérése a PlantUML szerverről.'}), 500

if __name__ == '__main__':
    app.run(debug=True)
