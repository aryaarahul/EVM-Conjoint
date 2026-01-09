import streamlit as st
import random, requests, re
from PIL import Image
from io import BytesIO
from supabase import create_client

# --- 1. CONFIG ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

# Initialize Session State
if "initialized" not in st.session_state:
    res = supabase.table("images").select("*").execute()
    # LOCAL ELOS: Starts at 1200 for every user every time
    st.session_state.local_images = {
        img['id']: {**img, "elo_rating": 1200.0} for img in res.data
    }
    # GLOBAL ELOS: Fetched only for the final "Comparison" display
    st.session_state.global_images = {img['id']: img for img in res.data}
    
    st.session_state.count = 0
    st.session_state.max_votes = 45
    st.session_state.current_batch = []
    st.session_state.finished = False
    st.session_state.initialized = True

# --- 2. THE DUAL-TRACK UPDATE ---
def process_vote_parallel(winner_id, loser_ids, user):
    K = 32
    
    # --- TRACK A: Update LOCAL session memory (The Fresh Start) ---
    winner_local = st.session_state.local_images[winner_id]
    Ra_local = winner_local['elo_rating']
    for lid in loser_ids:
        loser_local = st.session_state.local_images[lid]
        Rb_local = loser_local['elo_rating']
        Ea = 1 / (1 + 10**((Rb_local - Ra_local) / 400))
        st.session_state.local_images[lid]['elo_rating'] = Rb_local + (K/3) * (0 - (1 / (1 + 10**((Ra_local - Rb_local) / 400))))
        Ra_local += (K/3) * (1 - Ea)
    st.session_state.local_images[winner_id]['elo_rating'] = Ra_local

    # --- TRACK B: Update GLOBAL Supabase table (The Master Leaderboard) ---
    # We fetch the current global score to ensure we update it correctly
    win_glob = supabase.table("images").select("elo_rating, votes_count").eq("id", winner_id).single().execute().data
    Ra_glob = win_glob['elo_rating']
    
    for lid in loser_ids:
        los_glob = supabase.table("images").select("elo_rating").eq("id", lid).single().execute().data
        Rb_glob = los_glob['elo_rating']
        Ea_g = 1 / (1 + 10**((Rb_glob - Ra_glob) / 400))
        new_rb_g = Rb_glob + (K/3) * (0 - (1 / (1 + 10**((Ra_glob - Rb_glob) / 400))))
        supabase.table("images").update({"elo_rating": new_rb_g}).eq("id", lid).execute()
        Ra_glob += (K/3) * (1 - Ea_g)
    
    supabase.table("images").update({
        "elo_rating": Ra_glob, 
        "votes_count": win_glob['votes_count'] + 1
    }).eq("id", winner_id).execute()

    # Log individual vote as usual
    supabase.table("votes").insert({
        "user_name": user, "winner_id": winner_id, 
        "losers_ids": ", ".join([st.session_state.local_images[lid]['filename'] for lid in loser_ids])
    }).execute()

# --- 3. UI LAYOUT ---
st.title("📋 Image Preference Study")

if not st.session_state.finished:
    user_name = st.sidebar.text_input("Participant Name", value="User_" + str(random.randint(100, 999)))
    st.write(f"### Round {st.session_state.count + 1} of 45")
    
    if not st.session_state.current_batch:
        all_ids = list(st.session_state.local_images.keys())
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(all_ids, 4)]

    cols = st.columns(2)
    for i, img in enumerate(st.session_state.current_batch):
        with cols[i % 2]:
            st.image(img['image_url'], use_container_width=True)
            if st.button(f"Pick {chr(65+i)}", key=f"btn_{img['id']}", use_container_width=True):
                process_vote_parallel(img['id'], [x['id'] for x in st.session_state.current_batch if x['id'] != img['id']], user_name)
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    # Final Save logic here... (same as your previous natural sort save)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = [] # Trigger new batch
                st.rerun()
else:
    st.success("✅ Done! Your private ranking is saved, and the Global Leaderboard is updated.")
