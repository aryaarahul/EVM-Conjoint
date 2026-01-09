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
def fetch_master_images():
    res = supabase.table("images").select("*").execute()
    return res.data

# Initialize Session State
if "initialized" not in st.session_state:
    raw = fetch_master_images()
    st.session_state.local_images = {str(img['id']): {**img, "elo_rating": 1200.0} for img in raw}
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
    win_loc = st.session_state.local_images[str(winner_id)]
    ra = win_loc['elo_rating']
    for lid in loser_ids:
        rb = st.session_state.local_images[str(lid)]['elo_rating']
        ea = 1.0 / (1.0 + pow(10.0, (rb - ra) / 400.0))
        st.session_state.local_images[str(lid)]['elo_rating'] = rb + (K/3.0) * (0.0 - (1.0 / (1.0 + pow(10.0, (ra - rb) / 400.0))))
        ra += (K/3.0) * (1.0 - ea)
    st.session_state.local_images[str(winner_id)]['elo_rating'] = ra
    st.session_state.vote_queue.append({"w_id": str(winner_id), "l_ids": [str(lid) for lid in loser_ids]})

def sync_results(user):
    # FIX 3: Progress message appears while saving
    status_msg = st.empty()
    status_msg.info(f"⏳ Saving results for {user}... Syncing with Global Leaderboard.")
    
    progress_bar = st.progress(0)
    total = len(st.session_state.vote_queue)
    
    for i, v in enumerate(st.session_state.vote_queue):
        try:
            # Sync Elo to Database
            supabase.rpc('update_elo_parallel', {'win_id': v['w_id'], 'los_ids': v['l_ids'], 'k_val': 32}).execute()
            # Log raw vote
            supabase.table("votes").insert({"user_name": user, "winner_id": v['w_id'], "losers_ids": ", ".join(v['l_ids'])}).execute()
        except Exception as e:
            print(f"Sync error: {e}")
        progress_bar.progress((i + 1) / total)

    # Save User Ranking Table
    local_data = list(st.session_state.local_images.values())
    sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
    local_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
    fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
    
    row = {"user_name": user}
    for i, fname in enumerate(fixed_order):
        row[f"image_{i+1}_rank"] = local_ranks.get(fname.lower())
    supabase.table("user_rankings_fixed").insert(row).execute()

    # Fetch Global Ranks
    global_data = supabase.table("images").select("filename, elo_rating").order("elo_rating", desc=True).execute().data
    global_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(global_data)}

    # FIX 2: Explicit labels for the final result table
    st.session_state.comparison_data = sorted([
        {
            "Product Image": f, 
            "Your Personal Rank": local_ranks.get(f.lower()), 
            "Global Community Rank": global_ranks.get(f.lower()), 
            "Rank Difference": global_ranks.get(f.lower()) - local_ranks.get(f.lower())
        }
        for f in fixed_order
    ], key=lambda x: x['Your Personal Rank'])
    
    status_msg.empty()
    progress_bar.empty()

# --- 3. UI LAYOUT ---
st.set_page_config(page_title="Preference Study", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stImage"] img {max-height: 230px; width: auto; margin: auto; display: block;}
    .stButton button {height: 2.2rem; margin-top: -5px;}
    .progress-label {font-size: 14px; font-weight: bold; color: #555;}
</style>""", unsafe_allow_html=True)

if not st.session_state.name_confirmed:
    st.write("## Product Preference Study")
    name = st.text_input("Participant Name:", placeholder="Enter your name to begin")
    if st.button("Start Ranking Session"):
        if name.strip():
            st.session_state.participant_name = name.strip()
            st.session_state.name_confirmed = True
            st.rerun()
        else: st.warning("Name is required.")

elif not st.session_state.finished:
    if not st.session_state.current_batch:
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(list(st.session_state.local_images.keys()), 4)]
    
    st.write(f"### {st.session_state.participant_name}, which product do you prefer?")
    c1, c2 = st.columns(2)
    batch = st.session_state.current_batch
    for i in range(4):
        with (c1 if i < 2 else c2):
            st.image(batch[i]['image_url'])
            if st.button(f"Option {chr(65+i)}", key=f"b_{batch[i]['id']}_{st.session_state.count}", use_container_width=True):
                record_vote_locally(batch[i]['id'], [x['id'] for x in batch if x['id'] != batch[i]['id']])
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    sync_results(st.session_state.participant_name)
                    st.session_state.finished = True
                else: st.session_state.current_batch = []
                st.rerun()
    
    # FIX 1: Progress bar with level indicator
    st.markdown("---")
    st.markdown(f"<p class='progress-label'>Rounds Completed: {st.session_state.count} of {st.session_state.max_votes}</p>", unsafe_allow_html=True)
    st.progress(st.session_state.count / st.session_state.max_votes)

else:
    st.balloons()
    st.success(f"✅ Session complete for {st.session_state.participant_name}!")
    st.subheader("📊 Ranking Comparison (Local vs. Global)")
    st.dataframe(st.session_state.comparison_data, use_container_width=True, hide_index=True)
    
    if st.button("Finish and Restart"):
        st.session_state.clear()
        st.rerun()
