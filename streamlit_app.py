import streamlit as st
import random, requests, re, time
from PIL import Image
from io import BytesIO
from supabase import create_client

# --- 1. SETUP & CONFIG ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

# Initialize Session State
if "initialized" not in st.session_state:
    # Fetch filenames once from DB
    res = supabase.table("images").select("*").execute()
    # Create a LOCAL copy of images all starting at 1200 Elo
    st.session_state.local_images = {
        img['id']: {**img, "elo_rating": 1200.0} for img in res.data
    }
    st.session_state.count = 0
    st.session_state.max_votes = 45
    st.session_state.current_batch = []
    st.session_state.finished = False
    st.session_state.initialized = True

# --- 2. LOGIC FUNCTIONS ---
def get_batch():
    # Pick 4 random IDs from our local session dictionary
    all_ids = list(st.session_state.local_images.keys())
    selected_ids = random.sample(all_ids, 4)
    return [st.session_state.local_images[sid] for sid in selected_ids]

def record_vote_local(winner_id, loser_ids, user):
    """Calculates Elo in memory and logs raw vote to DB."""
    K = 32
    # Reference the local memory for these images
    winner = st.session_state.local_images[winner_id]
    Ra = winner['elo_rating']
    
    # 1. PERMANENT LOG: Save the raw vote to Supabase
    try:
        loser_names = [st.session_state.local_images[lid]['filename'] for lid in loser_ids]
        supabase.table("votes").insert({
            "user_name": user, 
            "winner_id": winner_id, 
            "losers_ids": ", ".join(loser_names)
        }).execute()
    except Exception as e:
        st.error(f"Database Logging Error: {e}")

    # 2. LOCAL CALCULATION: Update Elo scores in session memory only
    for lid in loser_ids:
        loser = st.session_state.local_images[lid]
        Rb = loser['elo_rating']
        
        # Elo probability math
        Ea = 1 / (1 + 10**((Rb - Ra) / 400))
        Eb = 1 / (1 + 10**((Ra - Rb) / 400))
        
        # Update local loser score
        st.session_state.local_images[lid]['elo_rating'] = Rb + (K/3) * (0 - Eb)
        # Carry over winner's temporary gain for next comparison in the set
        Ra += (K/3) * (1 - Ea)
    
    # Finalize local winner score
    st.session_state.local_images[winner_id]['elo_rating'] = Ra

def save_final_ranking(user):
    """Saves the local result to user_rankings_fixed."""
    # 1. Get current standings from local session memory
    local_data = list(st.session_state.local_images.values())
    sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
    
    # Map filename -> Rank
    rank_map = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
    
    # 2. Fixed order image1 -> image14
    fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
    
    row = {"user_name": user}
    display_list = []

    for i in range(14):
        col = f"image_{i+1}_rank"
        if i < len(fixed_order):
            fname = fixed_order[i]
            r = rank_map.get(fname.lower(), 0)
            row[col] = r
            display_results = [fname, r]
            display_list.append(display_results)

    # 3. Save the independent session result to Supabase
    supabase.table("user_rankings_fixed").insert(row).execute()
    return display_list

# --- 3. UI LAYOUT ---
st.set_page_config(page_title="Product Ranking", layout="centered")
st.title("📋 Image Preference Study")

if not st.session_state.finished:
    # Sidebar for Progress
    st.sidebar.header("Study Status")
    user_name = st.sidebar.text_input("Enter Your Name", value="Participant")
    st.sidebar.write(f"Round: **{st.session_state.count + 1} / {st.session_state.max_votes}**")
    st.sidebar.progress(st.session_state.count / st.session_state.max_votes)
    
    st.write("Which of these four images do you prefer most?")

    # Initial Batch Load
    if not st.session_state.current_batch:
        st.session_state.current_batch = get_batch()

    # 2x2 Grid Layout
    cols = st.columns(2)
    for i, img_data in enumerate(st.session_state.current_batch):
        with cols[i % 2]:
            st.image(img_data['image_url'], use_container_width=True)
            if st.button(f"Select Image {chr(65+i)}", key=f"btn_{img_data['id']}", use_container_width=True):
                # Process the local vote
                winner_id = img_data['id']
                loser_ids = [x['id'] for x in st.session_state.current_batch if x['id'] != winner_id]
                
                record_vote_local(winner_id, loser_ids, user_name)
                
                # Increment Progress
                st.session_state.count += 1
                
                if st.session_state.count >= st.session_state.max_votes:
                    st.session_state.final_results = save_final_ranking(user_name)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = get_batch()
                st.rerun()

else:
    st.balloons()
    st.success(f"✅ Thank you, {user_name}! Your preferences have been recorded.")
    st.markdown("### Your Personal Ranking Results")
    st.table(st.session_state.final_results)
    if st.button("Start New Session"):
        for key in st.session_state.keys():
            del st.session_state[key]
        st.rerun()
