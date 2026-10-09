import time

from langchain_core.messages import AIMessage


def stream_print(text:str, delay: float = 0.01):
    for ch in text:
        print(ch, end='', flush=True)
        time.sleep(delay)
    print()