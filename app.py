from flask import Flask, render_template, request, jsonify
import random
from atproto import Client, models
import time
import re
from datetime import datetime
import sqlite3
import os
from dotenv import load_dotenv

# Muat environment variables dari file .env
load_dotenv()

app = Flask(__name__)
DATABASE = 'history.db'

# --- Fungsi Database (Tidak Berubah) ---
def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    if not os.path.exists(DATABASE):
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''CREATE TABLE draws (id INTEGER PRIMARY KEY, post_url TEXT, draw_time TEXT, winner_count INTEGER)''')
        cursor.execute('''CREATE TABLE winners (id INTEGER PRIMARY KEY, draw_id INTEGER, handle TEXT, avatar TEXT, FOREIGN KEY(draw_id) REFERENCES draws(id))''')
        db.commit()
        db.close()

# --- Fungsi Bantuan Bluesky ---

def get_followers(client, handle):
    followers_set = set()
    cursor = None
    while True:
        try:
            response = client.app.bsky.graph.get_followers(params={'actor': handle, 'cursor': cursor, 'limit': 100})
            if not response.followers: break
            for follower in response.followers: followers_set.add(follower.handle)
            cursor = response.cursor
            if not cursor: break
        except Exception as e:
            if 'ActorNotFound' in str(e): raise ValueError(f"Akun '{handle}' tidak ditemukan.")
            break 
    return followers_set

def get_all_participants_data(client, post_url, filter_repost, filter_comments, filter_likes):
    try:
        parts = post_url.split('/')
        post_handle = parts[4]
        post_id = parts[-1]
        profile_response = client.app.bsky.actor.get_profile(params={'actor': post_handle})
        post_did = profile_response.did
        post_uri = f"at://{post_did}/app.bsky.feed.post/{post_id}"
    except Exception:
        raise ValueError("URL postingan tidak valid atau tidak dapat diakses.")

    participants = {}

    if filter_repost:
        cursor = None
        while True:
            response = client.app.bsky.feed.get_reposted_by(params={'uri': post_uri, 'cursor': cursor, 'limit': 100})
            if not response.reposted_by: break
            for user in response.reposted_by:
                participants[user.handle] = {'handle': user.handle, 'avatar': user.avatar or ''}
            cursor = response.cursor
            if not cursor: break
            
    if filter_likes:
        cursor = None
        while True:
            response = client.app.bsky.feed.get_likes(params={'uri': post_uri, 'cursor': cursor, 'limit': 100})
            if not response.likes: break
            for like in response.likes:
                user = like.actor
                participants[user.handle] = {'handle': user.handle, 'avatar': user.avatar or ''}
            cursor = response.cursor
            if not cursor: break

    if filter_comments:
        try:
            response = client.app.bsky.feed.get_post_thread(params={'uri': post_uri, 'depth': 1})
            if response.thread and isinstance(response.thread, models.AppBskyFeedDefs.ThreadViewPost) and response.thread.replies:
                for reply_view in response.thread.replies:
                    if isinstance(reply_view, models.AppBskyFeedDefs.ThreadViewPost) and reply_view.post:
                        user = reply_view.post.author
                        participants[user.handle] = {'handle': user.handle, 'avatar': user.avatar or ''}
        except Exception as e:
            print(f"Gagal mengambil komentar: {e}")

    return {'participants': list(participants.values()), 'post_owner': post_handle}

# --- Rute Flask ---

@app.route('/')
def index():
    return render_template('picker.html')

@app.route('/pick_winner', methods=['POST'])
def pick_winner():
    BSKY_USERNAME = os.environ.get('BSKY_USERNAME')
    BSKY_APP_PASSWORD = os.environ.get('BSKY_APP_PASSWORD')
    if not BSKY_USERNAME or not BSKY_APP_PASSWORD:
        return jsonify(success=False, error="Kredensial server belum diatur.")

    data = request.get_json()
    post_url = data.get('post_url')
    num_winners = int(data.get('num_winners', 1))
    filter_repost = data.get('filter_repost', False)
    filter_comments = data.get('filter_comments', False)
    filter_likes = data.get('filter_likes', False)
    filter_followers = data.get('filter_followers', False)
    follower_check_handle = data.get('follower_check_handle', '').strip()
    exclude_winners = data.get('exclude_winners', []) 

    if not post_url: return jsonify(success=False, error="URL Postingan harus diisi.")
    if not any([filter_repost, filter_comments, filter_likes]): return jsonify(success=False, error="Pilih setidaknya satu kriteria.")
    if filter_followers and not follower_check_handle: return jsonify(success=False, error="Harap masukkan username untuk cek follower.")

    try:
        client = Client()
        client.login(BSKY_USERNAME, BSKY_APP_PASSWORD)
        
        if 'participants' in data and data['participants']:
            all_participants_list = data['participants']
            post_owner = data.get('post_owner', '')
        else:
            all_data = get_all_participants_data(client, post_url, filter_repost, filter_comments, filter_likes)
            all_participants_list = all_data['participants']
            post_owner = all_data['post_owner']

        followers = get_followers(client, follower_check_handle) if filter_followers else None
        
        peserta_valid = []
        for p in all_participants_list:
            handle = p['handle']
            
            # ===== PERUBAHAN DI SINI =====
            # Hanya mengecualikan pemilik postingan
            if handle == post_owner:
                continue
            
            if handle in exclude_winners:
                continue

            if filter_followers and handle not in followers:
                continue
            
            peserta_valid.append(p)
        
        if not peserta_valid: return jsonify(success=False, error="Tidak ada peserta yang valid.")
        if num_winners > len(peserta_valid): num_winners = len(peserta_valid)

        pemenang_obj = random.sample(peserta_valid, k=num_winners)
        draw_time = datetime.now().isoformat()

        if not exclude_winners:
            db = get_db()
            cursor = db.cursor()
            cursor.execute("INSERT INTO draws (post_url, draw_time, winner_count) VALUES (?, ?, ?)",(post_url, draw_time, len(pemenang_obj)))
            draw_id = cursor.lastrowid
            for winner in pemenang_obj:
                cursor.execute("INSERT INTO winners (draw_id, handle, avatar) VALUES (?, ?, ?)", (draw_id, winner['handle'], winner['avatar']))
            db.commit()
            db.close()

        return jsonify(
            success=True, winner=pemenang_obj,
            participant_count=len(peserta_valid),
            participants=all_participants_list, 
            post_owner=post_owner,
            draw_time=draw_time
        )
        
    except Exception as e:
        return jsonify(success=False, error=str(e))

if __name__ == '__main__':
    init_db() 
    app.run(debug=True)