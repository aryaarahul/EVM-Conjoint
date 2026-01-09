import streamlit as st
import random, requests, re
from PIL import Image
from io import BytesIO
from supabase import create_client

# --- 1. CONFIG & CONNECTION ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

# Initialize Session State
if "initialized" not in st.session_state:
    res = supabase.table("images").select("*").execute()
    st.session_state.local_images = {
        img['id']: {**img, "elo_rating": 1200.0} for img in res.data
    }
    st.session_state.participant_name = "User_" + str(random.randint(100, 999))
    st.session_state.count = 0
    st.session_state.max_votes = 45
    st.session_state.current_batch = []
    st.session_state.finished = False
    st.session_state.comparison_data = []
    st.session_state.initialized = True

# --- 2. DUAL-TRACK LOGIC ---
def process_vote_dual(winner_id, loser_ids, user):
    K = 32
    winner_local = st.session_state.local_images[winner_id]
    Ra_local = winner_local['elo_rating']
    for lid in loser_ids:
        loser_local = st.session_state.local_images[lid]
        Rb_local = loser_local['elo_rating']
        Ea_l = 1 / (1 + 10**((Rb_local - Ra_local) / 400))
        st.session_state.local_images[lid]['elo_rating'] = Rb_local + (K/3) * (0 - (1 / (1 + 10**((Ra_local - Rb_local) / 400))))
        Ra_local += (K/3) * (1 - Ea_l)
    st.session_state.local_images[winner_id]['elo_rating'] = Ra_local

    try:
        win_db = supabase.table("images").select("elo_rating, votes_count").eq("id", winner_id).single().execute().data
        Ra_db = win_db['elo_rating']
        for lid in loser_ids:
            los_db = supabase.table("images").select("elo_rating").eq("id", lid).single().execute().data
            Rb_db = los_db['elo_rating']
            Ea_db = 1 / (1 + 10**((Rb_db - Ra_db) / 400))
            new_rb_db = Rb_db + (K/3) * (0 - (1 / (1 + 10**((Ra_db - Rb_db) / 400))))
            supabase.table("images").update({"elo_rating": new_rb_db}).eq("id", lid).execute()
            Ra_db += (K/3) * (1 - Ea_db)
        
        supabase.table("images").update({"elo_rating": Ra_db, "votes_count": win_db['votes_count'] + 1}).eq("id", winner_id).execute()
        supabase.table("votes").insert({
            "user_name": user, "winner_id": winner_id, 
            "losers_ids": ", ".join([st.session_state.local_images[lid]['filename'] for lid in loser_ids])
        }).execute()
    except Exception as e:
        print(f"Vote Error: {e}")

def save_and_get_comparison(user):
    try:
        local_data = list(st.session_state.local_images.values())
        sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
        local_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
        fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
        
        row_data = {"user_name": user}
        for i, fname in enumerate(fixed_order):
            row_data[f"image_{i+1}_rank"] = local_ranks.get(fname.lower())
        
        supabase.table("user_rankings_fixed").insert(row_data).execute()
        
        global_res = supabase.table("images").select("filename, elo_rating").order("elo_rating", desc=True).execute()
        global_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(global_res.data)}

        comp_list = []
        for fname in fixed_order:
            comp_list.append({
                "Image": fname, "Your Rank": local_ranks.get(fname.lower()), "Global Rank": global_ranks.get(fname.lower())
            })
        return sorted(comp_list, key=lambda x: x['Your Rank'])
    except Exception as e:
        st.error(f"DB Error: {e}")
        return []

# --- 3. UI LAYOUT ---
st.set_page_config(page_title="Product Study", layout="wide", initial_sidebar_state="collapsed")

# Inject CSS to reduce white space even further
st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stVerticalBlock"] {gap: 0.5rem;}
    </style>
    """, unsafe_allow_html=True)

if not st.session_state.finished:
    # Use a column layout for the header to save space
    h1, h2 = st.columns([3, 1])
    with h1: st.subheader("Which product do you prefer?")
    with h2: st.write(f"**Round: {st.session_state.count + 1} / 45**")

    if not st.session_state.current_batch:
        all_ids = list(st.session_state.local_images.keys())
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(all_ids, 4)]

    # 4-COLUMN SINGLE ROW UI
    cols = st.columns(4)
    for i, img in enumerate(st.session_state.current_batch):
        with cols[i]:
            # Scale images to a fixed height using CSS/HTML for maximum control
            st.image(img['image_url'], use_container_width=True)
            if st.button(f"Select {chr(65+i)}", key=f"btn_{img['id']}_{st.session_state.count}", use_container_width=True):
                process_vote_dual(img['id'], [x['id'] for x in st.session_state.current_batch if x['id'] != img['id']], st.session_state.participant_name)
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    st.session_state.comparison_data = save_and_get_comparison(st.session_state.participant_name)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = []
                st.rerun()
    
    # Progress bar at the bottom
    st.progress(st.session_state.count / 45)

else:
    st.success(f"✅ Data saved for {st.session_state.participant_name}")
    c1, c2 = st.columns([1, 2])
    with c1: st.table(st.session_state.comparison_data[:5])
    with c2: st.dataframe(st.session_state.comparison_data, use_container_width=True)
    if st.button("Restart"):
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()
