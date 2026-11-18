"""Local speech + translation: IndicConformer (STT), faster-whisper (STT), Indic Parler-TTS (TTS), IndicTrans2.

All models are loaded lazily on first use and run on CPU by default so the 6 GB GPU stays free for the
Ollama LLM/VLM. Set SPEECH_DEVICE=cuda to move them to the GPU if VRAM allows.
"""
