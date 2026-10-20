@echo off
chcp 65001 >nul
echo Installo la trascrizione vocale locale (faster-whisper)...
python -m pip install --upgrade faster-whisper
echo.
echo Fatto. Chiudi la finestra di Aria e riavvia con:  python run.py
echo La prima volta scarica il modello vocale (circa 460 MB): serve internet, poi e' tutto offline.
pause
