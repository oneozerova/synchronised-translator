чтобы запустить simul streaming tts 

~ backend
uvicorn src.main:app --host 0.0.0.0 --port 8005
~ SimulStreaming
python3 simulstreaming_whisper_server.py   --host 0.0.0.0   --port 43001   --language auto   --task 'translate'    --beams 6   --frame_threshold 2
~simulstream
python3 ws_to_tcp_proxy.py
~text-to-speech
uvicorn src.main:app --host 0.0.0.0 --port 8005


~frontend
connect to 8005 port