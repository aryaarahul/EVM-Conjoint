import streamlit as st
import random, requests, re, time
from PIL import Image
from io import BytesIO
from supabase import create_client

# --- 1. SETUP & CONFIG ---
# Access secrets from Streamlit Cloud settings later
url = st.secrets["https://chxlkmjhytftwebosogv.supabase.co "]
key = st.secrets["sb_publishable_eXf0VW28BIEANJMe_QEemA_kd26RUDz "]
supabase = create_client(url, key)

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

# Initialize Session State
if "count" not in st.session_state:
    st.session_state.count = 0
    st.session_state.max_votes = 45
    st.session_state.current_batch = []
    st.session_state.finished = False

# --- 2. LOGIC FUNCTIONS ---
def get_batch():
    res = supabase.table("images").select("*").execute()
    return random.sample(res.data, 4)

def record_vote(winner, losers, user):
    K, Ra = 32, winner['elo_rating']
    # Log Vote
    supabase.table("votes").insert({
        "user_name": user, "winner_id": winner['id'], 
        "losers_ids": ", ".join([l['filename'] for l in losers])
    }).execute()
    # Update Elo
    for l in losers:
        Rb = l['elo_rating']
        Ea = 1 / (1 + 10**((Rb - Ra) / 400))
        supabase.table("images").update({"elo_rating": Rb + (K/3)*(0- (1/(1+10**((Ra-Rb)/400))))}).eq("id", l['id']).execute()
        Ra += (K/3) * (1 - Ea)
    supabase.table("images").update({"elo_rating": Ra, "votes_count": winner.get('votes_count',0)+1}).eq("id", winner['id']).execute()

def save_final(user):
    res = supabase.table("images").select("filename, elo_rating").order("elo_rating", desc=True).execute()
    rank_map = {item['filename'].lower(): i + 1 for i, item in enumerate(res.data)}
    all_res = supabase.table("images").select("filename").execute()
    fixed_order = sorted([f['filename'] for f in all_res.data], key=natural_sort_key)
    
    row = {"user_name": user}
    for i in range(14):
        fname = fixed_order[i] if i < len(fixed_order) else None
        row[f"image_{i+1}_rank"] = rank_map.get(fname.lower(), 0) if fname else None
    
    supabase.table("user_rankings_fixed").insert(row).execute()
    return [[f, rank_map.get(f.lower())] for f in fixed_order]

# --- 3. UI ---
st.title("📋 Image Preference Study")

if not st.session_state.finished:
    user_name = st.sidebar.text_input("Enter Name", value="Participant")
    st.write(f"### Round {st.session_state.count + 1} of {st.session_state.max_votes}")
    st.progress(st.session_state.count / st.session_state.max_votes)

    if not st.session_state.current_batch:
        st.session_state.current_batch = get_batch()

    cols = st.columns(2)
    for i, img_data in enumerate(st.session_state.current_batch):
        with cols[i % 2]:
            response = requests.get(img_data['image_url'])
            st.image(Image.open(BytesIO(response.content)), use_container_width=True)
            if st.button(f"Select Image {chr(65+i)}", key=f"btn_{img_data['id']}"):
                # Process Vote
                winner = img_data
                losers = [x for x in st.session_state.current_batch if x['id'] != winner['id']]
                record_vote(winner, losers, user_name)
                
                # Increment and Refresh
                st.session_state.count += 1
                if st.session_state.count >= st.session_state.max_votes:
                    st.session_state.final_results = save_final(user_name)
                    st.session_state.finished = True
                else:
                    st.session_state.current_batch = get_batch()
                st.rerun()

else:
    st.balloons()
    st.success("✅ Study Complete! Your data has been saved.")
    st.table(st.session_state.final_results)
