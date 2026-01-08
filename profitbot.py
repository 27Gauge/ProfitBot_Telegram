import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
from PIL import Image, ImageDraw, ImageFont, ImageOps
import os
import io
import re
import google.generativeai as genai
from datetime import datetime
import urllib.parse 
from openpyxl import Workbook, load_workbook
from dotenv import load_dotenv # [NUOVO] Importa per leggere .env
import traceback # [NUOVO] Importa per gestire gli errori

# Carica le variabili d'ambiente all'avvio
load_dotenv() 

# --- CONFIGURAZIONE (Legge tutto da .env) ---
# ⚠️ 1. TOKEN TELEGRAM - ORA LETTO DA .env
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') 

# ⚠️ 2. CHIAVE GOOGLE - ORA LETTA DA .env
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') 

# ⚠️ 3. CHAT ID per NOTIFICHE CRITICHE - ORA LETTO DA .env
FABRIZIO_CHAT_ID = os.getenv('FABRIZIO_CHAT_ID') # Useremo questo per il logging

# 4. ALTRE CONFIGURAZIONI (Invariate)
CHANNEL_ID = '@citazioneradar' 
AMAZON_TAG = 'radartest-21' 
DB_FILE = "Registro_Vendite.xlsx"
FONT_NAME = "Montserrat-Bold.ttf"
LINK_INFO_POST = "https://t.me/citazioneradar/178" 

# --- CONFIGURAZIONE IA ---
try:
    if GOOGLE_API_KEY:
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        print("⚠️ Chiave GOOGLE_API_KEY mancante. Le emoji IA non funzioneranno.")
        model = None
except Exception as e:
    print(f"Errore configurazione Gemini: {e}")
    model = None

# Verifica TOKEN
if not API_TOKEN:
    raise ValueError("❌ ERRORE CRITICO: TELEGRAM_BOT_TOKEN non trovato nel file .env.")

bot = telebot.TeleBot(API_TOKEN)
user_data = {} # Dizionario per memorizzare i dati temporanei dell'utente

print("✅ ProfitBot Definitivo AVVIATO!")

# --- NUOVA FUNZIONE: GESTIONE ERRORI CRITICI ---
def handle_critical_error(e):
    """Cattura, formatta e invia il traceback dell'errore al chat ID di Fabrizio."""
    error_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    error_traceback = traceback.format_exc()
    
    # Messaggio di errore formattato per Telegram
    message = (
        f"🚨 **ERRORE CRITICO in ProfitBot!** 🚨\n"
        f"⏰ Data/Ora: `{error_time}`\n"
        f"🐞 Tipo Errore: `{type(e).__name__}`\n"
        f"💬 Messaggio: `{str(e)}`\n\n"
        f"--- TRACEBACK ---\n"
        f"```\n"
        f"{error_traceback[:1000]}..." # Limita la lunghezza
        f"```"
    )
    
    # Tentativo di invio della notifica (solo se FABRIZIO_CHAT_ID è impostato)
    if FABRIZIO_CHAT_ID:
        try:
            bot.send_message(FABRIZIO_CHAT_ID, message, parse_mode='Markdown')
        except Exception as notify_e:
            print(f"Impossibile inviare la notifica a {FABRIZIO_CHAT_ID}: {notify_e}")
    
    print(message) # Stampa anche nella console per debug
# --- FINE FUNZIONE ERRORE ---


# --- DATABASE (Invariato) ---
def inizializza_db():
    if not os.path.exists(DB_FILE):
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Vendite"
            ws.append(["DATA", "ORA", "ASIN", "TITOLO", "PREZZO VECCHIO", "PREZZO NUOVO", "LINK", "FILE_ID"]) 
            ws.column_dimensions['D'].width = 50
            wb.save(DB_FILE)
        except: 
            pass

def is_gia_pubblicato(asin):
    if not os.path.exists(DB_FILE): return False
    try:
        wb = load_workbook(DB_FILE)
        ws = wb.active
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[2] and asin in str(row[2]) and asin != "NO_ASIN":
                return True
    except:
        return False
    return False

def salva_in_excel(dati):
    adesso = datetime.now()
    try:
        if os.path.exists(DB_FILE):
            wb = load_workbook(DB_FILE)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
        
        ws.append([
            adesso.strftime("%d/%m/%Y"), 
            adesso.strftime("%H:%M:%S"), 
            dati.get('asin', 'N/A'), 
            dati.get('titolo', 'Nessun Titolo'), 
            dati.get('old_fmt_save', '0,00'),   
            dati.get('new_fmt_save', '0,00'),   
            dati.get('link', ''),
            dati.get('file_id', 'N/A') 
        ])
        wb.save(DB_FILE)
        return True
    except Exception as e:
        print(f"Errore salvataggio Excel: {e}")
        return False
#inizializza_db() # Lo spostiamo nel main loop

# --- CLEANER E UTILITY (Invariato) ---
def clean_input_price(text):
    text = text.replace('€', '').replace('EUR', '').strip()
    return text

def clean_price_calc(price_str):
    if not isinstance(price_str, str):
        return 0.0
    price_str = price_str.replace('€', '').replace('$', '').strip()
    if price_str.count(',') == 1 and price_str.count('.') <= 1 and price_str.rfind(',') > price_str.rfind('.'):
        price_str = price_str.replace('.', '')
        price_str = price_str.replace(',', '.')
    else:
        price_str = price_str.replace(',', '') 
        
    try:
        return float(price_str)
    except ValueError:
        return 0.0 
        
def format_price_euro(val_float):
    return "{:,.2f}".format(val_float).replace(",", "X").replace(".", ",").replace("X", ".")

def format_price_for_excel(val_float):
    return "{:.2f}".format(val_float).replace('.', ',')

def sanitize_description(text):
    """Rimuove le ripetizioni immediate di valori numerici o sigle comuni."""
    if not text:
        return text
    
    # Pattern: Cerca un valore (es. 256GB, 5G, 6,3") seguito immediatamente dalla sua ripetizione.
    pattern = r'(\d+[\.,]?\d*["\w\s]*)(\1)'
    
    # Sostituisci il pattern completo ($1$2) con solo la prima occorrenza ($1)
    cleaned_text = re.sub(pattern, r'\1', text, flags=re.IGNORECASE)
    
    # Pulisce spazi doppi e trimma
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    return cleaned_text

# 🟢 FUNZIONE: PER RISOLVERE 'can't parse entities'
def escape_markdown(text):
    """
    Sfuga i caratteri speciali per la modalità di parse 'Markdown' (non V2),
    garantendo che i caratteri presenti nel titolo non interrompano il parsing.
    """
    # Caratteri speciali da sfuggire: \, *, _, [, ], (, )
    text = text.replace('\\', r'\\')
    text = text.replace('*', r'\*')
    text = text.replace('_', r'\_')
    text = text.replace('[', r'\[')
    text = text.replace(']', r'\]')
    text = text.replace('(', r'\(')
    text = text.replace(')', r'\)')
    return text


# --- IA E EMOJI (Invariato nella logica, usa le chiavi da os.getenv) ---

# 🛑 MODIFICATA: Rimosso l'uso dell'IA e il troncamento forzato. Restituisce il titolo quasi integralmente.
def rewrite_with_ai(testo_grezzo):
    """Restituisce il testo inserito pulito da formattazioni indesiderate, senza chiamare l'IA."""
    if not testo_grezzo:
        return ""
    # Pulisce da caratteri di formattazione comuni usati dall'IA in passato e trimma
    testo = testo_grezzo.strip().replace('"', '').replace('**', '')
    # Rimuovi esplicitamente i puntini di sospensione che l'IA usava
    if testo.endswith('...'):
        testo = testo[:-3].strip()
    return testo
        
def get_emoji_from_ia(titolo):
    if not titolo: return "📦"
    
    titolo_lower = titolo.lower().replace('.', '').replace(',', '')
    
    # MAPPATURA STATICA (PRIORITARIA)
    emoji_map = {
        "friggitrice": "🍳", "forno": "🍗", "frullatore": "🍹", "macchina da caffè": "☕",
        "pentola": "🍲", "padella": "🍳", "aspirapolvere": "🧹", "robot aspirapolvere": "🤖",
        "detersivo": "🧺", "ammorbidente": "🧺", "finish": "🍽️", 
        "smartphone": "📱", "tablet": "📱", "notebook": "💻", 
        "cuffie": "🎧", "auricolari": "🎧", "solo": "🎧",
        "custodia per": "📱", "t-shirt": "👕", "jeans": "👖", 
        "scarpe": "👟", "spazzola ad aria": "💆‍♀️", "prosecco": "🍾", 
        "vino": "🍷", "cioccolato": "🍫", "giacca": "🧥", 
        "piumino": "🧥", "tuta": "👕", "chiavetta": "💾", 
        "spazzolino": "🦷", 
        "elettrico": "⚡",   
        "bambino": "🧸",    
        "bebes": "🧸",      
        "chiccò": "🧸",     
        "i-master": "🍹",   
    }
    
    for keyword, emoji in emoji_map.items():
        if keyword in titolo_lower:
            return emoji 

    # 2. FALLBACK ALL'IA 
    if not model: return "📦"
    
    prompt = (
        f"Analizza il titolo: '{titolo}'. Restituisci SOLO UNA singola emoji molto specifica "
        f"che rappresenti il prodotto. Non usare emoji generiche come 📦, 🎁. Se non trovi una corrispondenza, restituisci solo 📦."
    )
    
    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
        emoji_match = re.search(r'([\U0001F000-\U001FFFFF])', raw_text)
        
        if emoji_match:
            return emoji_match.group(1) 
        
        return "📦"
        
    except Exception as e:
        print(f"Errore IA nel recupero emoji: {e}")
        return "📦" 

# --- FUNZIONI DI RIASSUNTO E COLLAGE (Invariato) ---
def get_riassunto_offerte():
    """Genera una stringa di riepilogo delle offerte pubblicate oggi, leggendo dall'Excel e calcolando lo sconto."""
    if not os.path.exists(DB_FILE):
        return "⚠️ Nessun record di offerte trovate nel database."

    adesso = datetime.now()
    oggi_str = adesso.strftime("%d/%m/%Y")
    riepilogo = f"👀 *Ecco il riassunto delle migliori offerte di oggi!* \n\n" 
    
    record_oggi = {}
    
    try:
        wb = load_workbook(DB_FILE)
        ws = wb.active
        
        for row in ws.iter_rows(min_row=2, values_only=True):
            data_db = row[0]
            prezzo_vecchio_str = row[4] 
            prezzo_nuovo_str = row[5]

            old_val = clean_price_calc(prezzo_vecchio_str)
            new_val = clean_price_calc(prezzo_nuovo_str)

            if str(data_db) == oggi_str and row[2] != 'N/A' and old_val > new_val and old_val > 0:
                perc = int(100 - (new_val / old_val * 100))
                old_fmt = format_price_euro(old_val) + "€"
                new_fmt = format_price_euro(new_val) + "€"
                
                record_oggi[row[2]] = {
                    'titolo': row[3],
                    'prezzo_vecchio_fmt': old_fmt,
                    'prezzo_nuovo_fmt': new_fmt,
                    'link': row[6],
                    'sconto_perc': perc 
                }

        num_offerte = len(record_oggi)
        if num_offerte == 0:
            return "⚠️ Nessuna offerta di ribasso trovata oggi nel database."
            
        offerte_ordinate = sorted(record_oggi.items(), key=lambda item: item[1]['sconto_perc'], reverse=True)
            
        count = 0 
        for asin, dati in offerte_ordinate:
            if count >= 5: 
                break
                
            titolo_pulito = dati['titolo'].split('(')[0].strip()
            
            # 🟢 MODIFICA CRITICA: Sfuggire i caratteri Markdown dal titolo
            titolo_pulito_sicuro = escape_markdown(titolo_pulito)
            
            riepilogo += f"🚨 **{titolo_pulito_sicuro}**\n" 
            riepilogo += f"💰 **{dati['prezzo_nuovo_fmt']}** _invece di {dati['prezzo_vecchio_fmt']}_\n"
            
            short_link_base = dati['link'].split('?')[0] 
            
            asin_match = re.search(r'(B0[A-Z0-9]{8})', short_link_base)
            if asin_match:
                short_link = f"https://www.amazon.it/dp/{asin_match.group(1)}?tag={AMAZON_TAG}"
            else:
                short_link = dati['link'] 

            riepilogo += f"🔍 {short_link}\n\n" 
            
            count += 1
            
        riepilogo += "🔗 *Tutti i link sono affiliati.*\n"
        return riepilogo
        
    except Exception as e:
        return f"❌ Errore nella lettura DB: {e}"

def get_latest_image_ids():
    """Recupera gli ultimi 5 file_id unici dal DB per il collage (per layout 2-3)."""
    if not os.path.exists(DB_FILE):
        return []

    file_ids = []
    adesso = datetime.now()
    oggi_str = adesso.strftime("%d/%m/%Y")
    
    ws = None
    
    try:
        wb = load_workbook(DB_FILE)
        ws = wb.active
        
        if ws.max_row < 2:
            return []
            
        for row in reversed(list(ws.iter_rows(min_row=2, values_only=True))):
            data_db = row[0]
            file_id = row[7] 
            prezzo_vecchio_str = row[4]
            prezzo_nuovo_str = row[5]

            old_val = clean_price_calc(prezzo_vecchio_str)
            new_val = clean_price_calc(prezzo_nuovo_str)
            
            if (str(data_db) == oggi_str and 
                file_id != 'N/A' and 
                file_id not in file_ids and 
                old_val > new_val):

                file_ids.append(file_id)
                if len(file_ids) >= 5: 
                    break
        
        return file_ids
    except Exception as e:
        print(f"Errore lettura file IDs: {e}") 
        return []

def crea_collage_riassunto():
    """Crea un collage con layout ottimizzato (2x3 con 5 foto) minimizzando lo spazio bianco."""
    file_ids = get_latest_image_ids()
    
    if not file_ids:
        img = Image.new('RGB', (600, 600), color = 'gray')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(FONT_NAME, 40)
        except:
            font = ImageFont.load_default()
            
        draw.text((50, 280), "⚠️ NESSUNA FOTO DI RIBASSO TROVATA OGGI ⚠️", fill=(255, 255, 255), font=font)
        bio = io.BytesIO()
        img.save(bio, 'JPEG', quality=90)
        bio.seek(0)
        return bio
        
    immagini_collage = []
    
    for file_id in file_ids:
        try:
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            img = Image.open(io.BytesIO(downloaded_file)).convert("RGB")
            immagini_collage.append(img)
        except Exception as e:
            print(f"Errore download foto {file_id}: {e}")
            continue

    if not immagini_collage:
        img = Image.new('RGB', (600, 600), color = 'gray')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(FONT_NAME, 40)
        except:
            font = ImageFont.load_default()
            
        draw.text((50, 280), "⚠️ FALLBACK: ERRORE SCARICAMENTO FOTO ⚠️", fill=(255, 255, 255), font=font)
        bio = io.BytesIO()
        img.save(bio, 'JPEG', quality=90)
        bio.seek(0)
        return bio

    num_immagini = len(immagini_collage)
    
    COLLAGE_BASE_DIM = 800
    PADDING = 10 
    
    if num_immagini <= 2:
        cols = num_immagini
        rows_effettive = 1
    elif num_immagini == 3:
        cols = 3
        rows_effettive = 1
    elif num_immagini <= 5: 
        cols = 3 
        rows_effettive = 2 
    else: 
        cols = 3
        rows_effettive = 3 
    
    dim_cella_effettiva = COLLAGE_BASE_DIM // cols
    larghezza_finale = cols * dim_cella_effettiva
    altezza_finale = rows_effettive * dim_cella_effettiva
    
    collage = Image.new('RGB', (larghezza_finale, altezza_finale), color='white')
    
    for i, img in enumerate(immagini_collage):
        
        row = i // cols
        col = i % cols
        
        img_resized = ImageOps.fit(img, (dim_cella_effettiva - PADDING, dim_cella_effettiva - PADDING), Image.Resampling.LANCZOS)
        x_offset = col * dim_cella_effettiva + PADDING // 2
        y_offset = row * dim_cella_effettiva + PADDING // 2
        
        if num_immagini == 4 or num_immagini == 5:
            
            if i < 2:
                # Centra il blocco di 2 foto nella riga da 3 colonne
                x_offset = col * dim_cella_effettiva + dim_cella_effettiva // 2 + PADDING // 2 
                y_offset = PADDING // 2 
                
            else:
                 col_riga2 = i - 2 
                 x_offset = col_riga2 * dim_cella_effettiva + PADDING // 2
                 y_offset = dim_cella_effettiva + PADDING // 2 
        
        collage.paste(img_resized, (x_offset, y_offset))

    bio = io.BytesIO()
    collage.save(bio, 'JPEG', quality=95)
    bio.seek(0)
    return bio

# --- MENU e GESTORE CALLBACK (Invariato) ---

def main_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Inizia Nuovo Post", callback_data="url_libero"))
    markup.add(InlineKeyboardButton("🗒️ Riassunto Offerte Oggi", callback_data="show_riassunto"))
    markup.add(InlineKeyboardButton("☀️ Inizio Pubblicazioni per Oggi", callback_data="open_posts"), 
               InlineKeyboardButton("🌙 Chiudi Pubblicazioni di Oggi", callback_data="close_posts"))
    markup.add(InlineKeyboardButton("🏠 Menu Principale (Start)", callback_data="reset_all")) 
    return markup

def get_extra_markup(chat_id):
    # INCLUSIONE 'rapida': False
    extras = user_data.get(chat_id, {}).get('extras', {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False})
    
    txt_prime = "✅ Prime" if extras['prime'] else "🚚 Prime?"
    txt_lampo = "✅ Lampo" if extras['lampo'] else "⚡️ Offerta a tempo?"
    txt_choice = "✅ Scelta Amazon" if extras['choice'] else "⭐ Scelta Amazon?"
    coupon_val = extras.get('coupon')
    txt_coupon = f"✅ {coupon_val}" if coupon_val else "🎫 Coupon?"
    
    # NUOVO PULSANTE
    txt_rapida = "✅ Vendita Rapida" if extras['rapida'] else "🔥 Offerta a vendita rapida?" 
    
    markup = InlineKeyboardMarkup(row_width=2)
    # Prima riga
    markup.add(InlineKeyboardButton(txt_prime, callback_data="toggle_prime"),
               InlineKeyboardButton(txt_lampo, callback_data="toggle_lampo"))
    # Seconda riga
    markup.add(InlineKeyboardButton(txt_choice, callback_data="toggle_choice"),
               InlineKeyboardButton(txt_coupon, callback_data="set_coupon"))
    # NUOVA terza riga
    markup.add(InlineKeyboardButton(txt_rapida, callback_data="toggle_rapida"))
    # Ultima riga
    markup.add(InlineKeyboardButton("📸 CONTINUA", callback_data="finish_extras"))
    return markup

@bot.message_handler(commands=['start'])
def welcome(message):
    bot.clear_step_handler_by_chat_id(message.chat.id)
    # INCLUSIONE 'rapida': False
    user_data[message.chat.id] = {'extras': {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False}} 
    bot.send_message(message.chat.id, "🚨 *ProfitBot di Radar Offerte*\nFlusso manuale per i tuoi post.", reply_markup=main_menu(), parse_mode='Markdown')

# --- GESTORE CALLBACK (LOGICA FLUSSO CRITICA - Invariato) ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except: pass

    # VARIABILE DI RESET AGGIORNATA
    RESET_EXTRAS = {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False}

    # RESET
    if call.data == "reset_all":
        bot.clear_step_handler_by_chat_id(chat_id) 
        user_data[chat_id] = {'extras': RESET_EXTRAS} 
        bot.edit_message_text("⭐️ *ProfitBot Stabile*\nFlusso manuale per i tuoi post.", chat_id, call.message.message_id, reply_markup=main_menu(), parse_mode='Markdown')
        return
        
    if call.data == "force_unlock" or call.data == "url_libero":
        bot.clear_step_handler_by_chat_id(chat_id) 
        user_data[chat_id] = {'extras': RESET_EXTRAS} 
        ask_link(chat_id)
        return

    # GESTIONE RIASSUNTO (Invariata)
    if call.data == "show_riassunto":
        show_riassunto(chat_id, call)
        return
    
    elif call.data == "pubblica_riassunto":
        pubblica_riassunto_handler(chat_id, call)
        return
        
    elif call.data == "confirma_pubblica_riassunto":
        confirma_pubblica_riassunto(chat_id, call) 
        return

    # GESTIONE CHIUSURA/APERTURA 
    elif call.data == "close_posts": 
        close_posts_handler(chat_id, call)
        return
    
    elif call.data == "open_posts": 
        open_posts_handler(chat_id, call)
        return

    # NAVIGAZIONE
    elif call.data == "back_to_link": ask_link(chat_id)
    elif call.data == "back_to_title":
        if 'link' in user_data.get(chat_id, {}): ask_title(chat_id)
        else: ask_link(chat_id)
    elif call.data == "back_to_old": ask_old_price(chat_id)
    elif call.data == "back_to_new": ask_new_price(chat_id)
    elif call.data == "back_to_reviews": ask_reviews(chat_id)
    
    # GESTIONE PER TORNARE INDIETRO 
    elif call.data == "back_to_description": 
        # Non c'è più un 'temp_descrizione_ia' da eliminare
        ask_description(chat_id)
        return
        
    # GESTIONE PER ANDARE AVANTI (Da step_description a step_ask_extras)
    elif call.data == "finish_description_step":
        step_ask_extras(chat_id)
        return

    # EXTRA (Tasti On/Off)
    if 'extras' not in user_data.get(chat_id, {}): 
        user_data[chat_id]['extras'] = RESET_EXTRAS

    elif call.data == "toggle_prime":
        user_data[chat_id]['extras']['prime'] = not user_data[chat_id]['extras']['prime']
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=get_extra_markup(chat_id))
    elif call.data == "toggle_lampo":
        user_data[chat_id]['extras']['lampo'] = not user_data[chat_id]['extras']['lampo']
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=get_extra_markup(chat_id))
    elif call.data == "toggle_choice": 
        user_data[chat_id]['extras']['choice'] = not user_data[chat_id]['extras']['choice']
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=get_extra_markup(chat_id))
        
    # NUOVO HANDLER PER 'Offerta a vendita rapida'
    elif call.data == "toggle_rapida": 
        user_data[chat_id]['extras']['rapida'] = not user_data[chat_id]['extras']['rapida']
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=get_extra_markup(chat_id))
        
    elif call.data == "set_coupon":
        msg = bot.send_message(chat_id, "🎫 **Valore Coupon:**")
        bot.register_next_step_handler(msg, step_coupon_input)
    elif call.data == "finish_extras":
        ask_photo(chat_id)

    # PUBBLICAZIONE SINGOLA (Invariata)
    elif call.data == "pubblica_si":
        if chat_id in user_data and 'img_bytes' in user_data[chat_id]:
            dati = user_data[chat_id]
            try:
                photo_file = io.BytesIO(dati['img_bytes'])
                bot.send_photo(CHANNEL_ID, photo_file, caption=dati['caption'], reply_markup=dati['markup'], parse_mode='Markdown')
                salva_in_excel(dati)
                
                msg = "✅ **PUBBLICATO!**"
                bot.edit_message_text(msg, chat_id, call.message.message_id)

                bot.send_message(chat_id, "Procediamo?", reply_markup=main_menu()) 
                # RESET DELLE VARIABILI INCLUDENDO 'rapida'
                user_data[chat_id] = {'extras': RESET_EXTRAS} 
            except Exception as e:
                # Se il bot crasha qui, non lo possiamo notificare con handle_critical_error, 
                # ma almeno l'utente vede l'errore a schermo.
                bot.send_message(chat_id, f"❌ ERRORE TECNICO: {e}")
        else:
            bot.send_message(chat_id, "⚠️ Sessione scaduta.")

    elif call.data == "pubblica_no":
        bot.edit_message_text("❌ Annullato.", chat_id, call.message.message_id)
        bot.send_message(chat_id, "Ok, riproviamo.", reply_markup=main_menu())

# --- FUNZIONI STEP (Invariato) ---
def get_nav_markup(step_back=None):
    markup = InlineKeyboardMarkup()
    if step_back:
        if step_back == "back_to_link":
            markup.add(InlineKeyboardButton("🔙 Correggi Link", callback_data=step_back))
        elif step_back == "back_to_title":
            markup.add(InlineKeyboardButton("🔙 Correggi Titolo", callback_data=step_back))
        elif step_back == "back_to_old":
            markup.add(InlineKeyboardButton("🔙 Correggi Vecchio Prezzo", callback_data=step_back))
        elif step_back == "back_to_new":
            markup.add(InlineKeyboardButton("🔙 Correggi Nuovo Prezzo", callback_data=step_back))
        elif step_back == "back_to_reviews":
            markup.add(InlineKeyboardButton("🔙 Correggi Voto/Recensioni", callback_data=step_back))
        elif step_back == "back_to_description":
            markup.add(InlineKeyboardButton("🔙 Correggi Descrizione", callback_data=step_back)) 

    markup.add(InlineKeyboardButton("❌ ANNULLA", callback_data="reset_all"))
    return markup

def ask_link(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "1️⃣ **Incolla il Link Amazon:**", reply_markup=get_nav_markup(None))
    bot.register_next_step_handler(msg, step_link)

def ask_title(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "2️⃣ **Inserire il Titolo del Prodotto:**", reply_markup=get_nav_markup("back_to_link"))
    bot.register_next_step_handler(msg, step_titolo_ai)

def ask_old_price(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "3️⃣ **Prezzo Vecchio (es: 1499.00 o 1.499,00):**", reply_markup=get_nav_markup("back_to_title"))
    bot.register_next_step_handler(msg, step_prezzo_old)

def ask_new_price(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "4️⃣ **Prezzo NUOVO (es: 1019.99 o 1.019,99):**", reply_markup=get_nav_markup("back_to_old"))
    bot.register_next_step_handler(msg, step_prezzo_new)

def ask_reviews(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "⭐ **Voto e Recensioni:**\nEs: `284 4.5`\n(Scrivi 'no' per saltare)", reply_markup=get_nav_markup("back_to_new"))
    bot.register_next_step_handler(msg, step_reviews)

def step_reviews(message):
    if message.text and message.text.startswith('/'): return
    txt = message.text.strip()
    if txt.lower() == "no":
        user_data[message.chat.id]['voto'] = None
    else:
        parti = txt.split()
        if len(parti) >= 2:
            num = parti[0]
            voto = parti[1]
            try: num = "{:,}".format(int(num.replace('.', '').replace(',', ''))).replace(',', '.')
            except: pass
            user_data[message.chat.id]['voto'] = f"⭐️ {num} Recensioni: {voto} / 5.0"
        else:
            user_data[message.chat.id]['voto'] = f"⭐️ {txt}"
            
    ask_description(message.chat.id) 

# --- STEP DESCRIZIONE AGGIORNATO (RIMOSSA IA - Invariato) ---
def ask_description(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    # 🛑 MESSAGGIO CORRETTO: Nessun riferimento all'IA
    msg = bot.send_message(chat_id, "5️⃣ **Descrizione Prodotto/Marketing**:\n(Max 4 righe brevi. Scrivi 'no' per saltare)", reply_markup=get_nav_markup("back_to_reviews"))
    bot.register_next_step_handler(msg, step_description)

def step_description(message):
    if message.text and message.text.startswith('/'): return
    
    chat_id = message.chat.id
    txt_grezzo = message.text.strip()
    
    # LOGICA UNIFICATA: accetta sempre l'input e salta l'IA
    
    # Salva e Sanifica il testo
    if txt_grezzo.lower() == "no":
        user_data[chat_id]['descrizione'] = None
        messaggio_anteprima = "✅ Descrizione saltata. Clicca Continua per le opzioni extra."
    else:
        # Manteniamo la sanificazione per pulire spazi doppi e ripetizioni
        sanitized_txt = sanitize_description(txt_grezzo) 
        user_data[chat_id]['descrizione'] = sanitized_txt
        messaggio_anteprima = f"✅ **Descrizione registrata:**\n`{sanitized_txt}`"

    # Crea i pulsanti di controllo per la navigazione
    markup_confirm = InlineKeyboardMarkup(row_width=2)
    markup_confirm.add(InlineKeyboardButton("✅ CONTINUA (Opzioni Extra)", callback_data="finish_description_step"))
    markup_confirm.add(InlineKeyboardButton("✍️ Modifica Descrizione", callback_data="back_to_description")) 
    markup_confirm.add(InlineKeyboardButton("❌ ANNULLA POST", callback_data="reset_all")) 

    # Invia il messaggio di controllo
    bot.send_message(chat_id, messaggio_anteprima, parse_mode='Markdown')
    bot.send_message(chat_id, "Se la descrizione è corretta, prosegui:", reply_markup=markup_confirm)

# --- FINE STEP DESCRIZIONE AGGIORNATO ---


def step_ask_extras(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "🚨 **Opzioni Extra (o Invia subito la FOTO):**", reply_markup=get_extra_markup(chat_id))
    bot.register_next_step_handler(msg, step_check_extras_photo)

def step_check_extras_photo(message):
    if message.content_type == 'photo':
        step_foto_process(message)
    else:
        msg = bot.send_message(message.chat.id, "❌ Per favore, invia la FOTO oppure clicca i pulsanti (es: CONTINUA).")
        bot.register_next_step_handler(msg, step_check_extras_photo)

def step_coupon_input(message):
    chat_id = message.chat.id
    valore = message.text
    if user_data.get(chat_id) is None: 
        # INCLUSIONE 'rapida': False
        user_data[chat_id] = {'extras': {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False}}
        
    if 'extras' not in user_data[chat_id]: 
        # INCLUSIONE 'rapida': False
        user_data[chat_id]['extras'] = {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False}
        
    user_data[chat_id]['extras']['coupon'] = valore
    msg = bot.send_message(chat_id, f"✅ Coupon '{valore}' salvato! Ora FOTO:", reply_markup=get_extra_markup(chat_id))
    bot.register_next_step_handler(msg, step_check_extras_photo)

def ask_photo(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    msg = bot.send_message(chat_id, "6️⃣ **Invia FOTO:**", reply_markup=get_nav_markup("back_to_description"))
    bot.register_next_step_handler(msg, step_foto_process)

# --- HANDLERS (Invariato) ---
def step_link(message):
    chat_id = message.chat.id
    if message.text and message.text.startswith('/'): return
    
    if chat_id not in user_data:
        # INCLUSIONE 'rapida': False
        user_data[chat_id] = {'extras': {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False}}
        
    link_raw = message.text
    
    if not link_raw or not "http" in link_raw:
        bot.send_message(chat_id, "❌ Per favor , incolla un link *valido* che contenga 'http'.")
        ask_link(chat_id) 
        return

    asin = "NO_ASIN"
    link_aff = link_raw 
    cart_link = link_raw
    
    try:
        asin_match = re.search(r'(B0[A-Z0-9]{8})', link_raw)
        if asin_match:
            asin = asin_match.group(1)
            link_aff = f"https://www.amazon.it/dp/{asin}?tag={AMAZON_TAG}"
            cart_link = f"https://www.amazon.it/gp/aws/cart/add.html?ASIN.1={asin}&Quantity.1=1&tag={AMAZON_TAG}"
            if is_gia_pubblicato(asin):
                bot.send_message(chat_id, f"⚠️ ATTENZIONE: Già pubblicato oggi!")
        else:
            sep = "&" if "?" in link_raw else "?"
            link_aff = f"{link_raw}{sep}tag={AMAZON_TAG}"
            cart_link = link_aff 
        
        if 'extras' not in user_data[chat_id]: 
            # INCLUSIONE 'rapida': False
            user_data[chat_id]['extras'] = {'prime': False, 'lampo': False, 'choice': False, 'coupon': None, 'rapida': False}

        user_data[chat_id]['link'] = link_aff
        user_data[chat_id]['cart_link'] = cart_link
        user_data[chat_id]['asin'] = asin
        ask_title(chat_id)
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Errore durante l'analisi del link: {e}. Riprova.")
        ask_link(chat_id)

# 🛑 MODIFICATA: Rimosso il 'typing' perché non usa l'IA.
def step_titolo_ai(message):
    if message.text and message.text.startswith('/'): return
    chat_id = message.chat.id
    # bot.send_chat_action(chat_id, 'typing') # RIMOSSO
    titolo = rewrite_with_ai(message.text) # Usa la versione che non chiama l'IA
    user_data[chat_id]['titolo'] = titolo
    
    # ✅ FIX: Invia il titolo completo come messaggio separato
    bot.send_message(chat_id, f"✨ {titolo}") 
    
    ask_old_price(chat_id)

def step_prezzo_old(message):
    if message.text and message.text.startswith('/'): return
    pulito = clean_input_price(message.text)
    val = clean_price_calc(pulito) 
    if val == 0.0 and pulito.strip() != "0" and pulito.strip() != "0,00":
        bot.send_message(message.chat.id, "❌ Prezzo Vecchio non valido. Riprova.")
        ask_old_price(message.chat.id)
        return

    user_data[message.chat.id]['old'] = val
    ask_new_price(message.chat.id)

def step_prezzo_new(message):
    if message.text and message.text.startswith('/'): return
    pulito = clean_input_price(message.text)
    val = clean_price_calc(pulito) 
    
    if val == 0.0 and pulito.strip() != "0" and pulito.strip() != "0,00":
        bot.send_message(message.chat.id, "❌ Prezzo Nuovo non valido. Riprova.")
        ask_new_price(message.chat.id)
        return
    
    chat_id = message.chat.id
    user_data[chat_id]['new'] = val
    
    extras = user_data.get(chat_id, {}).get('extras', {})
    
    try:
        old_val = user_data[chat_id]['old']
        new_val = val
        st = "N/D" 
        risp_str = "Nessun Risparmio"
        
        if old_val > new_val:
            perc = int(100 - (new_val / old_val * 100))
            st = f"-{perc}%"
            risp = old_val - new_val
            risp_str = f"RISPARMI: {format_price_euro(risp)}€" 
        
        elif old_val == new_val or old_val == 0:
            
            if extras.get('coupon'):
                st = "COUPON_MODE" 
                risp_str = "COUPON DISPONIBILE"
            else:
                st = "N/D" 
                risp_str = "Nessun Risparmio"
        
        elif old_val < new_val:
            st = "AUMENTO"
            risp_str = "Prezzo Aumentato"
            
        user_data[chat_id]['sconto'] = st
        user_data[chat_id]['risparmio'] = risp_str
        
    except Exception as e: 
         print(f"Errore calcolo sconto in step_prezzo_new: {e}")
         user_data[chat_id]['sconto'] = "N/D"
         user_data[chat_id]['risparmio'] = "Errore Calcolo"
    
    ask_reviews(chat_id)

def step_foto_process(message):
    if message.text and message.text.startswith('/'): return
    if not message.photo: 
        msg = bot.send_message(message.chat.id, "❌ Devi inviare la FOTO del prodotto per procedere.", reply_markup=get_nav_markup("back_to_description"))
        bot.register_next_step_handler(msg, step_foto_process)
        return

    msg_wait = bot.send_message(message.chat.id, "🎨 **Grafica...**")
    
    if not os.path.exists("template.png"):
        bot.send_message(message.chat.id, "❌ Manca template.png")
        return

    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    try:
        img_prod = Image.open(io.BytesIO(downloaded_file)).convert("RGBA")
        template = Image.open("template.png").convert("RGBA")
        W, H = template.size 
        
        target_w = int(W * 0.45) 
        target_h = int(H * 0.70)
        ratio = min(target_w / img_prod.width, target_h / img_prod.height)
        new_w = int(img_prod.width * ratio)
        new_h = int(img_prod.height * ratio)
        img_prod = img_prod.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        mask = Image.new("L", (new_w, new_h), 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.rounded_rectangle((0, 0, new_w, new_h), radius=40, fill=255)
        img_rounded = Image.new("RGBA", (new_w, new_h), (0,0,0,0))
        img_rounded.paste(img_prod, (0,0), mask)
        img_prod = img_rounded

        pos_x = 80 
        pos_y = (H - new_h) // 2 + 30
        template.paste(img_prod, (pos_x, pos_y), img_prod)

        draw = ImageDraw.Draw(template)
        
        try:
            font_main = FONT_NAME if os.path.exists(FONT_NAME) else "arial.ttf"
            font_price = ImageFont.truetype(font_main, 90)
            font_disc = ImageFont.truetype(font_main, 85)
            font_small = ImageFont.truetype(font_main, 55)
            font_brand = ImageFont.truetype(font_main, 40)
        except:
            font_price = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_disc = ImageFont.load_default()
            font_brand = ImageFont.load_default()

        dati = user_data[message.chat.id]
        extras = dati.get('extras', {})
        COLORE_SCONTO = (204, 0, 0)
        COLORE_PREZZO = (228, 121, 17) 
        COLORE_VECCHIO = (120, 120, 120)
        COLORE_RISPARMIO = (34, 139, 34)
        MARGIN_RIGHT = 80 

        # 1. RISPARMIO IN ALTO
        risparmio_txt = dati['risparmio']
        if extras.get('coupon'):
             risparmio_txt = "COUPON DISPONIBILE"
             
        try:
            bbox_r = draw.textbbox((0, 0), risparmio_txt, font=font_brand)
            w_r = bbox_r[2] - bbox_r[0]
            x_r = (W - w_r) / 2
            draw.text((x_r, 75), risparmio_txt, font=font_brand, fill=COLORE_RISPARMIO)
        except: pass

        # 2. SCONTO
        testo_sconto = dati['sconto']
        
        if testo_sconto == "COUPON_MODE":
             testo_sconto = extras.get('coupon', 'NOVITÀ')
        elif testo_sconto == "N/D":
             testo_sconto = "" 
        
        if testo_sconto and testo_sconto != "AUMENTO": 
            disc_bbox = draw.textbbox((0, 0), testo_sconto, font=font_disc)
            disc_w = disc_bbox[2] - disc_bbox[0]
            draw.text((W - disc_w - MARGIN_RIGHT, 380), testo_sconto, font=font_disc, fill=COLORE_SCONTO)

        # --- 3. PREZZO NUOVO (AUTO-RESIZE) ---
        val_new_str = format_price_euro(dati['new']) + "€"
        dati['new_fmt'] = val_new_str 
        dati['new_fmt_save'] = format_price_for_excel(dati['new']) 

        current_font_size = 90
        foto_end_x = pos_x + new_w + 30
        text_end_x = W - MARGIN_RIGHT
        max_available_width = text_end_x - foto_end_x

        while True:
            font_price = ImageFont.truetype(font_main, current_font_size)
            price_bbox = draw.textbbox((0, 0), val_new_str, font=font_price)
            price_width = price_bbox[2] - price_bbox[0]
            
            if price_width < max_available_width:
                break
            
            current_font_size -= 5
            if current_font_size < 40: break 

        draw.text((text_end_x - price_width, 490), val_new_str, font=font_price, fill=COLORE_PREZZO)
        
        # 4. PREZZO VECCHIO (MATEMATICAMENTE CENTRATO)
        if dati['old'] > 0 and dati['old'] != dati['new']:
            val_old_str = format_price_euro(dati['old']) + "€"
            dati['old_fmt'] = val_old_str
            dati['old_fmt_save'] = format_price_for_excel(dati['old']) 
            
            old_bbox = draw.textbbox((0, 0), val_old_str, font=font_small)
            old_width = old_bbox[2] - old_bbox[0]
            old_height = old_bbox[3] - old_bbox[1]
            
            start_x_old = W - old_width - MARGIN_RIGHT
            start_y_old = 610
            
            draw.text((start_x_old, start_y_old), val_old_str, font=font_small, fill=COLORE_VECCHIO)
            
            line_y = start_y_old + (old_height / 2) + 12 
            draw.line((start_x_old, line_y, start_x_old + old_width, line_y), fill=COLORE_VECCHIO, width=8)
        else:
            dati['old_fmt_save'] = format_price_for_excel(dati['old'])
            dati['new_fmt_save'] = format_price_for_excel(dati['new'])


        bio = io.BytesIO()
        template = template.convert("RGB")
        template.save(bio, 'JPEG', quality=95)
        bio.seek(0)
        bot.delete_message(message.chat.id, msg_wait.message_id)
        
        # --- CAPTION ---
        txt_descrizione = ""
        if dati.get('descrizione'):
            txt_descrizione = f"{dati['descrizione']}\n\n" 
            
        txt_prime = "🚚 _Venduto e spedito da Amazon_\n✅ _Spedizione Prime_\n" if extras.get('prime') else "🚚 _Venduto e spedito da Amazon_\n"
        txt_lampo = "⏳ **OFFERTA A TEMPO**\n" if extras.get('lampo') else ""
        txt_choice = "⭐ **SCELTA AMAZON**\n" if extras.get('choice') else ""
        txt_coupon = f"✂️ **COUPON DISPONIBILE: {extras.get('coupon')}**\n" if extras.get('coupon') else ""
        # NUOVA LINEA PER LA VENDITA RAPIDA
        txt_rapida = "🔥 **OFFERTA A VENDITA RAPIDA**\n" if extras.get('rapida') else "" 
        
        txt_reviews = ""
        if dati.get('voto'):
            txt_reviews = f"{dati['voto']}\n"
        
        titolo_prodotto = dati['titolo']
        emoji_prodotto = get_emoji_from_ia(titolo_prodotto)
        titolo_caption = f"{emoji_prodotto} {titolo_prodotto}"
        
        caption = (
            f"*{titolo_caption}*\n\n" 
            f"{txt_descrizione}" 
            f"ℹ️ _Dettagli su Amazon._\n\n" 
            f"{txt_lampo}"
            f"{txt_choice}"
            f"{txt_coupon}"
            f"{txt_rapida}" 
            f"{txt_prime}"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"{txt_reviews}"
            f"\nℹ️ [Disclaimer & Info]({LINK_INFO_POST})"
        )
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("✅ Acquista Ora su Amazon ✅", url=dati['link']))
        markup.add(InlineKeyboardButton("🛒 Aggiungi al carrello", url=dati['cart_link']))
        share_text = urllib.parse.quote(f"Guarda: {dati['titolo']} a {dati['new_fmt']}! {dati['link']}")
        share_url = f"https://t.me/share/url?url={dati['link']}&text={share_text}"
        markup.add(InlineKeyboardButton("😏 Invita un amico", url=share_url))
        
        user_data[message.chat.id]['file_id'] = message.photo[-1].file_id 
        user_data[message.chat.id]['img_bytes'] = bio.getvalue()
        user_data[message.chat.id]['caption'] = caption
        user_data[message.chat.id]['markup'] = markup
        
        conf = InlineKeyboardMarkup()
        conf.add(InlineKeyboardButton("✅ PUBBLICA", callback_data="pubblica_si"))
        conf.add(InlineKeyboardButton("🔙 Cambia Foto/Extra", callback_data="back_to_description")) 
        conf.add(InlineKeyboardButton("❌ ANNULLA", callback_data="reset_all"))
        
        bot.send_photo(message.chat.id, bio, caption=caption, reply_markup=markup, parse_mode='Markdown')
        bot.send_message(message.chat.id, "Anteprima perfetta! Sei pronto per pubblicare sul canale?", reply_markup=conf)

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Errore: {e}")

# show_riassunto e altre funzioni di pubblicazione riassunto (Invariato)
def show_riassunto(chat_id, call=None):
    testo_riassunto = get_riassunto_offerte() 
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚀 Inizia Nuovo Post", callback_data="url_libero"))
    
    if not testo_riassunto.startswith("⚠️") and not testo_riassunto.startswith("❌"):
         markup.add(InlineKeyboardButton("🌟 Pubblica Riassunto Migliori Offerte", callback_data="pubblica_riassunto"))
    
    markup.add(InlineKeyboardButton("☀️ Inizio Pubblicazioni per Oggi", callback_data="open_posts"), 
               InlineKeyboardButton("🌙 Chiudi Pubblicazioni di oggi", callback_data="close_posts"))
    markup.add(InlineKeyboardButton("🏠 Menu Principale (Start)", callback_data="reset_all")) 
    
    if call:
        bot.edit_message_text(testo_riassunto, chat_id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, testo_riassunto, reply_markup=markup, parse_mode='Markdown')

def pubblica_riassunto_handler(chat_id, call):
    adesso = datetime.now()
    dati_riassunto = get_riassunto_offerte()
    if dati_riassunto.startswith("⚠️") or dati_riassunto.startswith("❌"):
        bot.edit_message_text(dati_riassunto, chat_id, call.message.message_id, parse_mode='Markdown') 
        return

    caption_full = f"💣 **LE MIGLIORI OFFERTE DI OGGI** 💥\n\n"
    caption_full += dati_riassunto
    
    CAPTION_LIMIT = 990 
    caption_preview = caption_full
    
    if len(caption_full) > CAPTION_LIMIT:
        caption_preview = caption_full[:CAPTION_LIMIT]
        
        if caption_preview.count('**') % 2 != 0:
            caption_preview += '**'
            
        num_singoli_stelle = caption_preview.count('*') - caption_preview.count('**') * 2
        if num_singoli_stelle % 2 != 0:
            caption_preview += '*'

        if caption_preview.count('[') > caption_preview.count(']'):
             caption_preview = caption_preview.rsplit('\n', 1)[0] + "\n\n...(Riassunto troncato per l'anteprima)"
             
    
    bot.edit_message_text("Generazione Anteprima Riassunto... (Creazione Collage)", chat_id, call.message.message_id)
    try:
        bio = crea_collage_riassunto() 
        
    except Exception as e:
        img = Image.new('RGB', (600, 600), color = 'gray')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(FONT_NAME, 40)
        except:
            font = ImageFont.load_default()
        draw.text((10, 10), f"❌ ERRORE GRAFICA COLLAGE: {e}", fill=(255, 255, 255), font=font)
        bio = io.BytesIO()
        img.save(bio, 'JPEG', quality=90)
        bio.seek(0)
        
        bot.send_message(chat_id, f"❌ ERRORE GENERAZIONE COLLAGE: {e}")
        

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("✅ CONFERMA PUBBLICAZIONE SU CANALE", callback_data="confirma_pubblica_riassunto"))
    markup.add(InlineKeyboardButton("🌙 Chiudi Pubblicazioni di oggi", callback_data="close_posts")) 
    markup.add(InlineKeyboardButton("🏠 Menu Principale (Start)", callback_data="reset_all")) 
    markup.add(InlineKeyboardButton("❌ ANNULLA", callback_data="reset_all")) 
    
    if chat_id not in user_data: user_data[chat_id] = {}
    user_data[chat_id]['riassunto_caption'] = caption_full 
    user_data[chat_id]['riassunto_img_bytes'] = bio.getvalue()
    
    bot.edit_message_text("Generazione Anteprima Riassunto...", chat_id, call.message.message_id)
    bio.seek(0) 
    bot.send_photo(chat_id, bio, caption=caption_preview, parse_mode='Markdown')
    bot.send_message(chat_id, "Tutto pronto per pubblicare il riassunto sul canale?", reply_markup=markup)

def confirma_pubblica_riassunto(chat_id, call): 
    if chat_id in user_data and 'riassunto_img_bytes' in user_data[chat_id]:
        
        img_bytes = user_data[chat_id]['riassunto_img_bytes']
        img_stream = io.BytesIO(img_bytes) 
        caption_full = user_data[chat_id]['riassunto_caption']
        
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
        
        try:
            img_stream.seek(0) 
            
            markup_aff = InlineKeyboardMarkup(row_width=1)
            markup_aff.add(InlineKeyboardButton("🛒 Vai alle Offerte Amazon ✅", url=f"https://www.amazon.it/?tag={AMAZON_TAG}")) 

            msg_pubblicato = bot.send_photo(
                CHANNEL_ID, 
                img_stream, 
                caption=caption_full,
                parse_mode='Markdown',
                reply_markup=markup_aff 
            )
            
            try:
                bot.pin_chat_message(CHANNEL_ID, msg_pubblicato.message_id)
            except Exception as e:
                bot.send_message(chat_id, f"⚠️ Errore nel fissare il post: {e}")

            msg = "✅ **RIASSUNTO PUBBLICATO SUL CANALE!**"
            bot.send_message(chat_id, msg, parse_mode='Markdown')
            
            if 'riassunto_img_bytes' in user_data[chat_id]: del user_data[chat_id]['riassunto_img_bytes']
            if 'riassunto_caption' in user_data[chat_id]: del user_data[chat_id]['riassunto_caption']

            bot.send_message(chat_id, "Scegli cosa fare ora:", reply_markup=main_menu())
            
        except Exception as e:
            bot.send_message(chat_id, f"❌ Errore durante la pubblicazione finale: {e}")
            
    else:
        bot.edit_message_text("⚠️ Sessione Riassunto scaduta.", chat_id, call.message.message_id, parse_mode='Markdown')
        bot.send_message(chat_id, "Inizia di nuovo:", reply_markup=main_menu())

def close_posts_handler(chat_id, call):
    """Gestisce la pubblicazione del messaggio di chiusura."""
    
    closing_message = (
        "\n" 
        "🌙 Chiusura di giornata 🌙\n\n"
        "Grazie davvero per la compagnia e per la fiducia: oggi la selezione finisce qui.\n\n"
        "➡️ Domani vi aggiorno con nuove occasioni e ribassi interessanti.\n\n"
        "🔗 Link e prezzi sono affiliati e verificati al momento della pubblicazione.\n\n" 
        "\n" 
        "🛌 Buona notte da...."
        "\n"
        "** 👀 Radar Offerte 🚨 F. Devito" 
    )
    
    try:
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
            
        bot.send_message(
            CHANNEL_ID, 
            closing_message,
            parse_mode='Markdown'
        )
        
        msg = "✅ **MESSAGGIO DI CHIUSURA PUBBLICATO SUL CANALE!**"
        bot.send_message(chat_id, msg, parse_mode='Markdown')
        
        bot.send_message(chat_id, "Procediamo?", reply_markup=main_menu())
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Errore durante la pubblicazione del messaggio di chiusura: {e}")

def open_posts_handler(chat_id, call):
    """Gestisce la pubblicazione del messaggio di apertura."""

    opening_message = (
        "☀️ **Buongiorno dal Radar Offerte!** ☀️\n\n"
        "Siamo pronti per una nuova giornata di caccia alle migliori occasioni su Amazon.\n\n"
        "➡️ Restate sintonizzati per non perdere i ribassi più importanti e le offerte lampo!\n\n"
        "🔗 *Tutti i link sono affiliati e verificati al momento della pubblicazione.*\n\n"
        "\n"
        "Iniziamo la giornata con..."
        "\n"
        "** 👀 Radar Offerte 🚨 F. Devito"
    )

    try:
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
            
        bot.send_message(
            CHANNEL_ID, 
            opening_message,
            parse_mode='Markdown'
        )
        
        msg = "✅ **MESSAGGIO DI BUONGIORNO PUBBLICATO SUL CANALE!**"
        bot.send_message(chat_id, msg, parse_mode='Markdown')
        
        bot.send_message(chat_id, "Procediamo?", reply_markup=main_menu())
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Errore durante la pubblicazione del messaggio di apertura: {e}")

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'document'])
def global_gatekeeper(message):
    if message.text and message.text.startswith('/'): return
    
    bot.reply_to(message, "⛔️ **FERMO!**\nScegli cosa fare:", reply_markup=main_menu())

# --- MAIN LOOP AGGIORNATO (Gestione Errori) ---
if __name__ == '__main__':
    inizializza_db()
    
    # Loop infinito con gestione degli errori critici
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            # Cattura l'errore, notifica e poi aspetta prima di riprovare
            handle_critical_error(e) 
            time.sleep(5) # Attende 5 secondi prima di riavviare il polling