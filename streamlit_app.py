import streamlit as st
import random, re
from supabase import create_client

# --- 1. CONFIG & CONNECTION ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

@st.cache_data(ttl=3600)
def fetch_master_image_list():
    res = supabase.table("images").select("*").execute()
    return res.data

# Initialize Session State
if "initialized" not in st.session_state:
    raw_images = fetch_master_image_list()
    st.session_state.local_images = {
        img['id']: {**img, "elo_rating": 1200.0} for img in raw_images
    }
    st.session_state.vote_queue = [] # Store raw votes here to save at the end
    st.session_state.participant_name = ""
    st.session_state.count = 0
    st.session_state.max_votes = 30 
    st.session_state.current_batch = []
    st.session_state.finished = False
    st.session_state.initialized = True

# --- 2. INSTANT LOGIC (LOCAL ONLY) ---
def record_vote_locally(winner_id, loser_ids):
    K = 32
    # Update LOCAL Track A (Instant)
    winner_local = st.session_state.local_images[winner_id]
    Ra = winner_local['elo_rating']
    for lid in loser_ids:
        loser_local = st.session_state.local_images[lid]
        Rb = loser_local['elo_rating']
        Ea = 1 / (1 + 10**((Rb - Ra) / 400))
        st.session_state.local_images[lid]['elo_rating'] = Rb + (K/3) * (0 - (1 / (1 + 10**((Ra - Rb) / 400))))
        Ra += (K/3) * (1 - Ea)
    st.session_state.local_images[winner_id]['elo_rating'] = Ra
    
    # Queue the vote data for global update later
    st.session_state.vote_queue.append({
        "winner_id": winner_id,
        "loser_ids": loser_ids
    })

# --- 3. FINAL BACKGROUND SYNC ---
def sync_everything_to_supabase(user):
    with st.spinner("Finalizing rankings..."):
        # 1. Update Global Elo (Track B) - One-time bulk processing logic
        # For simplicity in this setup, we will process the queue and update DB
        for vote in st.session_state.vote_queue:
            # We still need to update global scores, but doing it now 
            # doesn't slow down the user during the 30 rounds.
            win_id = vote['winner_id']
            los_ids = vote['loser_ids']
            
            # (Note: In a high-traffic app, you'd use a Postgres Function here, 
            # but moving it to the end already solves your UI lag problem.)
            supabase.rpc('update_elo_parallel', {
                'win_id': win_id, 
                'los_ids': los_ids,
                'k_val': 32
            }).execute() # This requires a small SQL function in Supabase

        # 2. Save User Ranking Table
        local_data = list(st.session_state.local_images.values())
        sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
        local_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
        fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
        
        row_data = {"user_name": user}
        for i, fname in enumerate(fixed_order):
            row_data[f"image_{i+1}_rank"] = local_ranks.get(fname.lower())
        supabase.table("user_rankings_fixed").insert(row_data).execute()

# --- 4. UI LAYOUT ---
st.set_page_config(page_title="Preference Study", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem;}
    [data-testid="stImage"] img {max-height: 220px; width: auto; margin: auto; display: block;}
    </style>
    """, unsafe_allow_html=True)

if not st.session_state.participant_name:
    name = st.text_input("Enter Name:")
    if st.button("Start"):
        st.session_state.participant_name = name
        st.rerun()

elif not st.session_state.finished:
    if not st.session_state.current_batch:
        all_ids = list(st.session_state.local_images.keys())
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(all_ids, 4)]

    cols = st.columns(2)
    for i, img in enumerate(st.session_state.current_batch):
        with cols[i % 2]:
            st.image(img['image_url'])
            if st.button(f"Option {chr(65+i)}", key=f"b_{img['id']}_{st.session_state.count}", use_container_width=True):
                # INSTANT LOCAL UPDATE
                record_vote_locally(img['id'], [x['id'] for x in st.session_state.current_batch if x['id'] != img['id']])
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    sync_everything_to_supabase(st.session_state.participant_name)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = []
                st.rerun()
    
    st.progress(st.session_state.count / st.session_state.max_votes)

else:
    st.success("Ranking Complete!")
    st.write("Your results have been synced to the global leaderboard.")

    if st.button("Finish & Restart for New User"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
