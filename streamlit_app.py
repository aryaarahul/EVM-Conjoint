import streamlit as st
import random, re
from supabase import create_client

# --- 1. CONFIG ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase = create_client(url, key)

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

@st.cache_data(ttl=3600)
def fetch_master_images():
    return supabase.table("images").select("*").execute().data

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
    st.session_state.initialized = True

# --- 2. THE SYNC ENGINE ---
def record_vote_locally(winner_id, loser_ids):
    K = 32
    # Local RAM update (Instant)
    win_loc = st.session_state.local_images[str(winner_id)]
    ra = win_loc['elo_rating']
    for lid in loser_ids:
        rb = st.session_state.local_images[str(lid)]['elo_rating']
        ea = 1.0 / (1.0 + pow(10.0, (rb - ra) / 400.0))
        st.session_state.local_images[str(lid)]['elo_rating'] = rb + (K/3.0) * (0.0 - (1.0 / (1.0 + pow(10.0, (ra - rb) / 400.0))))
        ra += (K/3.0) * (1.0 - ea)
    st.session_state.local_images[str(winner_id)]['elo_rating'] = ra
    
    # Store for final background sync
    st.session_state.vote_queue.append({
        "w_id": str(winner_id),
        "l_ids": [str(lid) for lid in loser_ids]
    })

def sync_results(user):
    progress = st.progress(0, text="Finalizing data sync...")
    total = len(st.session_state.vote_queue)
    
    for i, v in enumerate(st.session_state.vote_queue):
        try:
            # 1. Update Global Elo Scores via SQL RPC
            supabase.rpc('update_elo_parallel', {
                'win_id': v['w_id'], 'los_ids': v['l_ids'], 'k_val': 32
            }).execute()

            # 2. Log Raw Vote (Using only columns you confirmed exist)
            supabase.table("votes").insert({
                "user_name": user,
                "winner_id": v['w_id'],
                "losers_ids": ", ".join(v['l_ids'])
            }).execute()
        except Exception as e:
            print(f"Sync error on vote {i}: {e}")
        progress.progress((i+1)/total)

    # 3. Save User Ranking Table
    local_data = list(st.session_state.local_images.values())
    sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
    local_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
    fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
    
    row = {"user_name": user}
    for i, fname in enumerate(fixed_order):
        row[f"image_{i+1}_rank"] = local_ranks.get(fname.lower())
    supabase.table("user_rankings_fixed").insert(row).execute()

    # 4. Final Comparison for display
    global_data = supabase.table("images").select("filename, elo_rating").order("elo_rating", desc=True).execute().data
    global_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(global_data)}

    st.session_state.comparison_data = sorted([
        {"Image": f, "Your Rank": local_ranks.get(f.lower()), "Global Rank": global_ranks.get(f.lower()), "Diff": global_ranks.get(f.lower()) - local_ranks.get(f.lower())}
        for f in fixed_order
    ], key=lambda x: x['Your Rank'])

# --- 3. UI ---
st.set_page_config(page_title="Preference Study", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
    .block-container {padding-top: 1rem;}
    [data-testid="stImage"] img {max-height: 230px; width: auto; margin: auto; display: block;}
    .stButton button {height: 2.2rem; margin-top: -5px;}
</style>""", unsafe_allow_html=True)

if not st.session_state.name_confirmed:
    st.write("## Welcome")
    name = st.text_input("Participant Name:")
    if st.button("Start"):
        if name.strip():
            st.session_state.participant_name = name.strip()
            st.session_state.name_confirmed = True
            st.rerun()
elif not st.session_state.finished:
    if not st.session_state.current_batch:
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(list(st.session_state.local_images.keys()), 4)]
    
    st.write(f"### {st.session_state.participant_name}, which do you prefer?")
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
    st.progress(st.session_state.count / st.session_state.max_votes)
else:
    st.balloons()
    st.dataframe(st.session_state.comparison_data, use_container_width=True, hide_index=True)
    if st.button("Restart"):
        st.session_state.clear()
        st.rerun()
