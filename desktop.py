import os
import socket
import numpy as np
import sounddevice as sd
import threading
import struct
import time
import tkinter as tk
from tkinter import messagebox
import platform
import psutil
import json
import dotenv
import compressor
from vosk import Model, KaldiRecognizer
import asyncio
from websockets.asyncio.client import connect
import uuid
import traceback
from tkinter import messagebox

recognizer_lock = threading.Lock()

# Load environment variables        
CHUNK_SIZE = 2048
CHANNELS = 1
SAMPLE_RATE = 16000
WEBSOCKET_ID = None
# Încarcă modelul (doar o dată, la startup)
model = Model("vosk-model-small-en-us-0.15")
recognizer = KaldiRecognizer(model, SAMPLE_RATE)
sock = None
connected = False
SERVER_IP = dotenv.get_key(key_to_get='SERVER_IP', dotenv_path='.env')
WEBSOCKET_SERVER_IP = dotenv.get_key(key_to_get='SERVER_WEBSOCKET', dotenv_path='.env')
compres = compressor.Compressor()
print(f"SERVER_IP: {SERVER_IP}")
main_loop = None
message_queue = None
last_sent = ""

def guess_network_type():
    stats = psutil.net_if_stats()
    for iface, data in stats.items():
        if data.isup:
            name = iface.lower()
            if "wlan" in name or "wi-fi" in name or "wifi" in name:
                return "WiFi"
            elif "eth" in name or "en" in name:
                return "Ethernet"
            elif "wwan" in name or "cell" in name or "lte" in name:
                return "Mobile (4G/5G)"
    return "Unknown"


def receive_audio():
    global connected
    try:
        with sd.OutputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, dtype='int16', channels=CHANNELS) as stream:
            update_status("[🔊] Ascultare activă...")
            while connected:
               data, _ = sock.recvfrom(65536)  # buffer mare să nu tai date
               if len(data) <= 16:
                    continue  # Pachet invalid sau doar header, ignoră
                
               audio_dataaudio_data_bytes = compres.decode(data)
               audio_data = np.frombuffer(audio_dataaudio_data_bytes, dtype=np.int16.astype('<i2'))
               stream.write(audio_data)
    except Exception as e:
        update_status(f"Eroare la recepție: {e}")


def transmit_audio():
    global connected
    global main_loop
    if not connected:
        update_status("❌ Nu ești conectat la un server!")
        return

    seq_number = 0
    try:
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, dtype='int16', channels=CHANNELS) as stream:
            update_status("[🎙️] Transmitere activă...")
            while push_to_talk_btn_pressed:
                print("Transmit loop running...")
                ws_uuid = uuid.UUID(WEBSOCKET_ID)
                ws_id_bytes = ws_uuid.bytes  # 16 bytes
                timestamp = int(time.time() * 1000)
                seq_number += 1
                header = struct.pack('!QQ', seq_number, timestamp) + ws_id_bytes
                
                audio_chunk, overflowed = stream.read(CHUNK_SIZE * 2)
                if overflowed:
                    print("⚠️ Buffer overflow!")
                    continue

                if not audio_chunk or len(audio_chunk) == 0:
                    continue

                # Conversie în bytes
                audio_transcript = transcript_audio_chunk(audio_chunk)
                global last_sent
                last_sent = audio_transcript
                compressed_data = compres.encode(bytearray(audio_chunk))
                print(f"Transmitting chunk of size {len(compressed_data)} bytes, seq: {seq_number}, timestamp: {timestamp}")
                packet = header + compressed_data
                sock.sendto(packet, (server_ip.get(), SERVER_PORT))
                time.sleep(0.050)  # limităm puțin viteza de trimitere
    except Exception as e:
        print(f"Transmit error: {e}")
        update_status(f"Eroare la transmitere: {e}")
    finally:
        update_status("[🛑] Transmitere oprită.")


def transcript_audio_chunk(audio_chunk) -> str:
    global recognizer_lock   
    audio_bytes = bytes(audio_chunk)
    with recognizer_lock:  # 🔒 protejăm accesul
        if recognizer.AcceptWaveform(audio_bytes):
            result = json.loads(recognizer.Result())
            return result.get("text", "")
        else:
            partial = json.loads(recognizer.PartialResult())
            return partial.get("partial", "")
async def producer(message):
    """Simulează transcripturile generate de aplicația ta."""
    msg = buildWebSocketMessage(message=message)
    global message_queue
    await message_queue.put(msg)  # bagă în coadă
    print(f"message ${message} appended to queue")

def buildWebSocketMessage(message: str):
    global WEBSOCKET_ID
    return {
        'type': 'MSG',
        'sender_id': WEBSOCKET_ID,
        'data': message
    }

async def connect_to_websocket_server(): #consumer
    global WEBSOCKET_ID
    print(WEBSOCKET_SERVER_IP)
    global message_queue
    try:
        async with connect(WEBSOCKET_SERVER_IP) as websocket:
            await websocket.send(json.dumps({ 'type': 'CONN'}))
            message = await websocket.recv()
            data = json.loads(message)
            print(f'websocket id received: ${data}')
            WEBSOCKET_ID = data['id']
            print(WEBSOCKET_ID)
            # Creează task-ul pentru receive (background, pe același websocket)
            receive_task = asyncio.create_task(websocket_receiver(websocket))
            while True:
                message = await message_queue.get()
                if (len(message) == 0):
                    return
                try:
                    await websocket.send(json.dumps(message))
                    print(f"📤 Sent: {message}")
                except Exception as e:
                    print("❌ Failed to send:", e)
                finally:
                    message_queue.task_done()
    except Exception as e:
        print("websocket connect failed " + str(e))
        traceback.print_exc()

async def websocket_receiver(websocket):  # Task separat pentru receive, pe websocket-ul comun
    """Ascultă mesaje incoming pe websocket-ul dat."""
    try:
        while True:
            try:
                message = await websocket.recv()
                data = json.loads(message)
                print(f"📥 Received: {data}")
                await handle_incoming_message(data)  # Procesează (ca înainte)
            except Exception as e:
                print(f"❌ Error receiving: {e}")
                await asyncio.sleep(1)  # Retry mic
    except asyncio.CancelledError:
        print("Receiver task cancelled – closing.")
    except Exception as e:
        print("Receiver failed: " + str(e))
        traceback.print_exc()

# handle_incoming_message rămâne la fel ca în propunerea anterioară
async def handle_incoming_message(data: dict):
    msg_type = data.get('type')
    global WEBSOCKET_ID
    if msg_type == 'WARN':
        sender_id = data.get('sender_id')
        message = data.get('message', '')
        actual_language_score = data.get('actual_score', 100)
        
        if sender_id == WEBSOCKET_ID:
            messagebox.showinfo("avertizare", f"scorul tau a fost scazutla {actual_language_score} puncte pt limbaj toxic")

            #root.after(0, lambda: update_status(f"📨 De la {sender_id}: {message}"))
            root.after(0, lambda: update_language_score(f' Language score: {actual_language_score}'))

        
    elif msg_type == 'TOXICITY_RESPONSE':
        # Parsează în ToxicityResponse dacă e JSON valid
        pass  # Extinde după nevoie
    else:
        print(f"Tip necunoscut: {msg_type}")

def start_ws_loop():
    global main_loop, message_queue
    # Cream loop-ul nou (pentru acel thread)
    main_loop = asyncio.new_event_loop()
    # setam loop-ul curent pentru acest thread (utile pentru asyncio intern)
    asyncio.set_event_loop(main_loop)
    # cream coada legata de acest loop
    message_queue = asyncio.Queue()
    # rulam consumerul (blocheaza acest thread atata timp cat serverul functioneaza)
    try:
        main_loop.run_until_complete(connect_to_websocket_server())
    finally:
        # curatare daca se opreste
        pending = asyncio.all_tasks(loop=main_loop)
        for t in pending:
            t.cancel()
        main_loop.run_until_complete(main_loop.shutdown_asyncgens())
        main_loop.close()

def connect_to_server():
    global sock, connected
    ip = server_ip.get()
    if not ip:
        update_status("❗ Introdu un IP valid.")
        return

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b'hello', (ip, SERVER_PORT))
        connected = True
        update_status(f"✅ Conectat la {ip} ({guess_network_type()})")

        # Start receiving thread
        threading.Thread(target=receive_audio, daemon=True).start()
    except Exception as e:
        update_status(f"❌ Conectare eșuată: {e}")
        connected = False


def disconnect_from_server(stars):
    global connected
    if connected and sock:
        message = 'DISCONNECT:' + guess_network_type() + ":" + str(stars)
        sock.sendto(message.encode('utf-8'), (server_ip.get(), SERVER_PORT))
        sock.close()
        update_status("🔌 Deconectat.")
        connected = False


def on_push_to_talk_press(event=None):
    global push_to_talk_btn_pressed
    global last_sent
    last_sent = ""
    push_to_talk_btn_pressed = True
    print("Push to Talk pressed")  # debug
    threading.Thread(target=transmit_audio, daemon=True).start()


def on_push_to_talk_release(event=None):
    global push_to_talk_btn_pressed, last_sent, main_loop, recognizer

    push_to_talk_btn_pressed = False
    print("Pushed to talk: STOP")

    try:
        with recognizer_lock:  # 🔒 blocăm accesul la recognizer
            final_result = json.loads(recognizer.FinalResult())
            final_text = final_result.get("text", "").strip()
            recognizer.Reset()

        if final_text:
            last_sent = final_text
            print(f"[🎤] Final recognized text: {final_text}")
            future = asyncio.run_coroutine_threadsafe(producer(last_sent), main_loop)
        else:
            print("[🎤] Nimic de trimis (rezultat gol).")

    except Exception as e:
        print(f"Vosk finalization error: {e}")

    finally:
        recognizer.Reset()
        last_sent = ""



def update_status(message):
    def setter():
        status_label.config(text=message)
    root.after(0, setter)

def update_language_score(score):
    def setter():
        language_score.config(text=score)
    root.after(0, setter)

def on_closing():
    if not connected:
        root.destroy()
        return
    
    rating_window = tk.Toplevel(root)
    rating_window.title("Feedback")
    rating_window.geometry("300x150")
    rating_window.grab_set()  # face fereastra modală
    tk.Label(rating_window, text="Cum ai evalua calitatea audio?", font=("Arial", 12)).pack(pady=10)
    selected_rating = tk.IntVar(value=0)


    def give_rating(stars):
        selected_rating.set(stars)
        print(f"Rating oferit: {stars} stele")
        disconnect_from_server(stars)
        rating_window.destroy()
        root.destroy()
        
    stars_frame = tk.Frame(rating_window)
    stars_frame.pack()
    for i in range(1, 6):
        btn = tk.Button(stars_frame, text="★", font=("Arial", 18),
                        command=lambda i=i: give_rating(i))
        btn.grid(row=0, column=i, padx=5)

    rating_window.protocol("WM_DELETE_WINDOW", lambda: None)  # prevenim închiderea fără rating

# 

SERVER_PORT = 41234


push_to_talk_btn_pressed = False

root = tk.Tk()
root.title("VoIP Client")
root.geometry("400x250")
root.resizable(False, False)

tk.Label(root, text="Server IP:").pack(pady=(20, 5))
server_ip = tk.StringVar()
server_ip.set(SERVER_IP if SERVER_IP else "")
ip_entry = tk.Entry(root, textvariable=server_ip, font=("Arial", 12), width=25)
ip_entry.pack()

tk.Button(root, text="Conectează-te", command=connect_to_server, bg="lightgreen").pack(pady=10)

push_to_talk_btn = tk.Button(root, text="Push to Talk", width=20, bg="lightblue")
push_to_talk_btn.pack(pady=10)
push_to_talk_btn.bind("<ButtonPress>", on_push_to_talk_press)
push_to_talk_btn.bind("<ButtonRelease>", on_push_to_talk_release)

status_label = tk.Label(root, text="Neconectat.", fg="gray")
language_score = tk.Label(root, text='Language score: 100', fg="red")
status_label.pack(pady=20)
language_score.pack(pady=5)  # Adaugă-l sub status, cu un pic de spațiu
root.protocol("WM_DELETE_WINDOW", on_closing)
threading.Thread(target=start_ws_loop, daemon=True).start()
root.mainloop()
