import requests
import streamlit as st

st.title('QTP')

try:
    r = requests.get('http://localhost:8000/api/health', timeout=1)
    st.write('Backend:', r.json())
except:
    st.write('Backend unreachable')
