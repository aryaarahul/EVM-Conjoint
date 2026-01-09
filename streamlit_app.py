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
    st.session_state.vote_queue = [] 
    st.session_state.name_confirmed = False
    st.session_state.participant_name = ""
    st.session_state.count = 0
    st.session_state.max_votes = 30 
    st.session_state.current_batch = []
    st.session_state.finished = False
    st.session_state.comparison_data = []
    st.session_state.initialized = True

# --- 2. LOGIC FUNCTIONS ---
def record_vote_locally(winner_id, loser_ids):
    K = 32
    # Instant Local Elo Update (Track A)
    winner_local = st.session_state.local_images[winner_id]
    Ra = winner_local['elo_rating']
    for lid in loser_ids:
        loser_local = st.session_state.local_images[lid]
        Rb = loser_local['elo_rating']
        Ea = 1 / (1 + 10**((Rb - Ra) / 400))
        st.session_state.local_images[lid]['elo_rating'] = Rb + (K/3) * (0 - (1 / (1 + 10**((Ra - Rb) / 400))))
        Ra += (K/3) * (1 - Ea)
    st.session_state.local_images[winner_id]['elo_rating'] = Ra
    
    # Store for final batch sync
    st.session_state.vote_queue.append({
        "winner_id": winner_id,
        "loser_ids": loser_ids
    })

def sync_everything_to_supabase(user):
    with st.spinner("Finalizing results..."):
        # A. Batch Sync Global Elos via RPC
        for vote in st.session_state.vote_queue:
            try:
                supabase.rpc('update_elo_parallel', {
                    'win_id': str(vote['winner_id']), 
                    'los_ids': [str(lid) for lid in vote['loser_ids']],
                    'k_val': 32
                }).execute()
            except Exception as e:
                print(f"Sync error: {e}")

        # B. Save Final Individual Ranking Table
        local_data = list(st.session_state.local_images.values())
        sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
        local_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
        fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
        
        row_data = {"user_name": user}
        for i, fname in enumerate(fixed_order):
            row_data[f"image_{i+1}_rank"] = local_ranks.get(fname.lower())
        supabase.table("user_rankings_fixed").insert(row_data).execute()

        # C. Prepare Comparison Data
        global_res = supabase.table("images").select("filename, elo_rating").order("elo_rating", desc=True).execute()
        global_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(global_res.data)}

        comp_list = []
        for f in fixed_order:
            u_rank = local_ranks.get(f.lower())
            g_rank = global_ranks.get(f.lower())
            comp_list.append({
                "Image": f, 
                "Your Rank": u_rank, 
                "Global Rank": g_rank, 
                "Difference": g_rank - u_rank
            })
        st.session_state.comparison_data = sorted(comp_list, key=lambda x: x['Your Rank'])

# --- 3. UI LAYOUT ---
st.set_page_config(page_title="Preference Study", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stImage"] img {max-height: 230px; width: auto; margin: auto; display: block;}
    .stButton button {height: 2.2rem; margin-top: -5px;}
    .status-text {font-size: 14px; font-weight: bold; color: #555; margin-bottom: -5px;}
    </style>
    """, unsafe_allow_html=True)

# STAGE 1: Participant Name Popup
if not st.session_state.name_confirmed:
    st.write("## Welcome to the Study")
    name_input = st.text_input("Participant Name:", placeholder="Enter your full name")
    if st.button("Start Ranking Session"):
        if name_input.strip():
            st.session_state.participant_name = name_input.strip()
            st.session_state.name_confirmed = True
            st.rerun()
        else:
            st.warning("Please provide a name to proceed.")

# STAGE 2: 2x2 Voting Matrix
elif not st.session_state.finished:
    st.write(f"### Which do you prefer, {st.session_state.participant_name}?")

    if not st.session_state.current_batch:
        all_ids = list(st.session_state.local_images.keys())
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(all_ids, 4)]

    c1, c2 = st.columns(2)
    batch = st.session_state.current_batch
    for i in range(4):
        target_col = c1 if i < 2 else c2
        with target_col:
            st.image(batch[i]['image_url'])
            if st.button(f"Option {chr(65+i)}", key=f"b_{batch[i]['id']}_{st.session_state.count}", use_container_width=True):
                record_vote_locally(batch[i]['id'], [x['id'] for x in batch if x['id'] != batch[i]['id']])
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    sync_everything_to_supabase(st.session_state.participant_name)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = []
                st.rerun()
    
    # Progress Section
    st.markdown("---")
    st.markdown(f"<p class='status-text'>Progress: {st.session_state.count} / {st.session_state.max_votes} Rounds Completed</p>", unsafe_allow_html=True)
    st.progress(st.session_state.count / st.session_state.max_votes)

# STAGE 3: Full Comparison (1-14)
else:
    st.balloons()
    st.success(f"✅ Session complete for {st.session_state.participant_name}!")
    st.subheader("Final Ranking Comparison")
    st.dataframe(st.session_state.comparison_data, use_container_width=True, hide_index=True)
    
    if st.button("Finish and Restart"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
