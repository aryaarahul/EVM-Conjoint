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
    
    # --- TRACK A: Update LOCAL session memory ---
    winner_local = st.session_state.local_images[winner_id]
    Ra_local = winner_local['elo_rating']
    for lid in loser_ids:
        loser_local = st.session_state.local_images[lid]
        Rb_local = loser_local['elo_rating']
        Ea_l = 1 / (1 + 10**((Rb_local - Ra_local) / 400))
        st.session_state.local_images[lid]['elo_rating'] = Rb_local + (K/3) * (0 - (1 / (1 + 10**((Ra_local - Rb_local) / 400))))
        Ra_local += (K/3) * (1 - Ea_l)
    st.session_state.local_images[winner_id]['elo_rating'] = Ra_local

    # --- TRACK B: Update GLOBAL Supabase ---
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

        # Log individual vote
        supabase.table("votes").insert({
            "user_name": user, "winner_id": winner_id, 
            "losers_ids": ", ".join([st.session_state.local_images[lid]['filename'] for lid in loser_ids])
        }).execute()
    except Exception as e:
        print(f"Vote Log Error: {e}")

def save_and_get_comparison(user):
    try:
        # 1. Process Local Ranks (Individual)
        local_data = list(st.session_state.local_images.values())
        sorted_local = sorted(local_data, key=lambda x: x['elo_rating'], reverse=True)
        local_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(sorted_local)}
        
        # 2. Prepare Database Row
        fixed_order = sorted([img['filename'] for img in local_data], key=natural_sort_key)
        row_data = {"user_name": user}
        for i, fname in enumerate(fixed_order):
            row_data[f"image_{i+1}_rank"] = local_ranks.get(fname.lower())
        
        # --- CRITICAL: Save to User Ranking Table ---
        supabase.table("user_rankings_fixed").insert(row_data).execute()
        
        # 3. Fetch Global Standings for Comparison
        global_res = supabase.table("images").select("filename, elo_rating").order("elo_rating", desc=True).execute()
        global_ranks = {item['filename'].lower(): i + 1 for i, item in enumerate(global_res.data)}

        comp_list = []
        for fname in fixed_order:
            comp_list.append({
                "Image": fname,
                "Your Rank": local_ranks.get(fname.lower()),
                "Global Rank": global_ranks.get(fname.lower())
            })
        return sorted(comp_list, key=lambda x: x['Your Rank'])
    except Exception as e:
        st.error(f"Error saving results to database: {e}")
        return []

# --- 3. UI LAYOUT ---
st.set_page_config(page_title="Product Study", layout="wide")
st.title("📋 Image Preference Study")

if not st.session_state.finished:
    st.sidebar.header("Participant Info")
    current_user = st.sidebar.text_input("Name", value=st.session_state.participant_name)
    st.session_state.participant_name = current_user
    
    st.sidebar.write(f"### Round {st.session_state.count + 1} / 45")
    st.sidebar.progress(st.session_state.count / 45)

    if not st.session_state.current_batch:
        all_ids = list(st.session_state.local_images.keys())
        st.session_state.current_batch = [st.session_state.local_images[sid] for sid in random.sample(all_ids, 4)]

    st.write("### Which of these 4 do you prefer?")
    cols = st.columns(2)
    for i, img in enumerate(st.session_state.current_batch):
        with cols[i % 2]:
            st.image(img['image_url'], use_container_width=True)
            if st.button(f"Pick {chr(65+i)}", key=f"btn_{img['id']}_{st.session_state.count}", use_container_width=True):
                process_vote_dual(img['id'], [x['id'] for x in st.session_state.current_batch if x['id'] != img['id']], st.session_state.participant_name)
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    # Final Step: Pass Name and Save
                    st.session_state.comparison_data = save_and_get_comparison(st.session_state.participant_name)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = []
                st.rerun()

else:
    st.balloons()
    st.success(f"✅ Study Complete, {st.session_state.participant_name}! Data saved.")
    
    if st.session_state.comparison_data:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("Your Personal Top 5")
            st.table(st.session_state.comparison_data[:5])
        with col2:
            st.subheader("Full Comparison (1-14)")
            st.dataframe(st.session_state.comparison_data, use_container_width=True)
    
    if st.button("Start New Session"):
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()
