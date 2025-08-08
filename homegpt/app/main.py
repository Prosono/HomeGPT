# homegpt/app/main.py
from collections import deque

# A global event buffer for tracking HA events
EVENT_BUFFER = deque(maxlen=100)

def add_event(event):
    EVENT_BUFFER.append(event)

def get_events():
    return list(EVENT_BUFFER)
