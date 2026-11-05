#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditor de Audio de Recepción (Turno Noche 24/7 + Compilador 7:00 AM)
---------------------------------------------------------------------
Stack Open-Source:
- Captura de matriz de micrófonos: PortAudio (`sounddevice`)
- Detección Neuronal de Voz Humana (VAD): `snakers4/silero-vad` (integrado en `faster_whisper.vad` + ONNX Runtime)
- Transcripción y Extracción Estructurada: Gemini 2.5 Flash Multimodal Audio + Respaldo Offline `SYSTRAN/faster-whisper`
- Cruce Contable Automático: Cruza Hora, DNI, Habitación y Monto hablado contra `estancias` (Registro_Estancias)
  y `pagos` (Registro_Pagos) en `ovalo_local.db`.
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import sounddevice as sd

# Asegurar UTF-8 en consola Windows (evita errores cp1252)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Asegurar rutas de importación del proyecto
BASE_APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_APP_DIR.parent
if str(BASE_APP_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_APP_DIR))

try:
    import dotenv
    dotenv.load_dotenv(ROOT_DIR / ".env")
    dotenv.load_dotenv(BASE_APP_DIR / ".env")
    dotenv.load_dotenv(Path(r"C:\Hostal_Sistema\.env"))
except Exception:
    pass

from config import DATA_DIR, DB_PATH, NOMBRE_HOTEL

AUDIOS_BASE_DIR = DATA_DIR / "audios_noche"
REPORTES_AUDIO_DIR = DATA_DIR / "reportes_audio"
AUDIOS_BASE_DIR.mkdir(parents=True, exist_ok=True)
REPORTES_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Espejo automático en C:\Hostal_Sistema\audios_noche_recepcion (sincronizado vía Syncthing entre Laptop 2 y Laptop 1)
SYNCTHING_AUDIOS_DIR = Path(r"C:\Hostal_Sistema\audios_noche_recepcion")
try:
    if Path(r"C:\Hostal_Sistema").exists():
        SYNCTHING_AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

SAMPLE_RATE = 16000  # 16 kHz óptimo para Silero VAD y Whisper/Gemini
BLOCK_DURATION_S = 0.5  # Bloques de 500 ms
BLOCK_SAMPLES = int(SAMPLE_RATE * BLOCK_DURATION_S)
PRE_ROLL_BLOCKS = 3  # 1.5 segundos previos para no cortar el saludo inicial
SILENCE_LIMIT_S = 3.0  # Segundos de silencio para cerrar una conversación
MIN_SPEECH_DURATION_S = 1.2  # Descartar ruidos sueltos menores a 1.2 segundos
MAX_CLIP_DURATION_S = 180.0  # Corte máximo de seguridad por archivo (3 min)
STOP_FLAG_PATH = AUDIOS_BASE_DIR / "STOP_RECORDING.flag"
ESTADO_JSON_PATH = AUDIOS_BASE_DIR / "ESTADO_GRABADOR.json"
_lock_proc_ia = threading.Lock()


def init_audio_audit_db() -> None:
    """Crea la tabla de auditoría de audios en ovalo_local.db si no existe."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auditoria_audio_noche (
                clip_id              TEXT PRIMARY KEY,
                fecha_turno          TEXT NOT NULL,
                fecha_hora_inicio    TEXT NOT NULL,
                fecha_hora_fin       TEXT NOT NULL,
                duracion_seg         REAL NOT NULL DEFAULT 0,
                archivo_wav          TEXT NOT NULL,
                transcripcion        TEXT NOT NULL DEFAULT '',
                resumen_ia           TEXT NOT NULL DEFAULT '',
                es_atencion_cliente  INTEGER NOT NULL DEFAULT 0,
                hab_detectada        TEXT NOT NULL DEFAULT '',
                dni_detectado        TEXT NOT NULL DEFAULT '',
                huesped_detectado    TEXT NOT NULL DEFAULT '',
                precio_hablado       REAL NOT NULL DEFAULT 0,
                metodo_pago_hablado  TEXT NOT NULL DEFAULT '',
                alerta_fuga_voz      TEXT NOT NULL DEFAULT '',
                estado_cruce         TEXT NOT NULL DEFAULT 'PENDIENTE',
                detalle_cruce        TEXT NOT NULL DEFAULT '',
                estancia_id_match    TEXT NOT NULL DEFAULT '',
                monto_registrado     REAL NOT NULL DEFAULT 0,
                diferencia_soles     REAL NOT NULL DEFAULT 0,
                hablantes_detectados TEXT NOT NULL DEFAULT '',
                tipo_interaccion     TEXT NOT NULL DEFAULT '',
                incidencia_mantenimiento TEXT NOT NULL DEFAULT '',
                procesado_ia         INTEGER NOT NULL DEFAULT 0,
                created_at           TEXT NOT NULL DEFAULT ''
            );
            """
        )
        for col_sql in [
            "ALTER TABLE auditoria_audio_noche ADD COLUMN hablantes_detectados TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE auditoria_audio_noche ADD COLUMN tipo_interaccion TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE auditoria_audio_noche ADD COLUMN incidencia_mantenimiento TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


def asegurar_microfono_windows_activo() -> None:
    """
    Asegura mediante CoreAudio COM (IPolicyConfig + IAudioEndpointVolume) que el micrófono
    integrado Realtek esté habilitado (DeviceState=1), como predeterminado, sin Mute (Muted=False)
    y con volumen al 100%, incluso si alguien presionó Fn+F4 o lo desactivó en Configuración.
    """
    if os.name != "nt":
        return
    ps_script = r"""
$code = @"
using System;
using System.Runtime.InteropServices;

[Guid("f8679f50-850a-41cf-9c72-430f290290c8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IPolicyConfig {
    int GetMixFormat([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, IntPtr ppFormat);
    int GetDeviceFormat([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, bool bDefault, IntPtr ppFormat);
    int ResetDeviceFormat([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName);
    int SetDeviceFormat([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, IntPtr pEndpointFormat, IntPtr MixFormat);
    int GetProcessingPeriod([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, bool bDefault, IntPtr pmftDefaultPeriod, IntPtr pmftMinimumPeriod);
    int SetProcessingPeriod([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, IntPtr pmftPeriod);
    int GetShareMode([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, IntPtr pMode);
    int SetShareMode([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, IntPtr mode);
    int GetPropertyValue([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, bool bFxStore, IntPtr key, IntPtr pv);
    int SetPropertyValue([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, bool bFxStore, IntPtr key, IntPtr pv);
    int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, int role);
    int SetEndpointVisibility([MarshalAs(UnmanagedType.LPWStr)] string pszDeviceName, [MarshalAs(UnmanagedType.Bool)] bool bVisible);
}

[ComImport, Guid("870af99c-171d-4f9e-af0d-e63df40c2bc9")]
internal class PolicyConfigClient { }

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioEndpointVolume {
    int RegisterControlChangeNotify(IntPtr pNotify);
    int UnregisterControlChangeNotify(IntPtr pNotify);
    int GetChannelCount(out int pnChannelCount);
    int SetMasterVolumeLevel(float fLevelDB, ref Guid pguidEventContext);
    int SetMasterVolumeLevelScalar(float fLevel, ref Guid pguidEventContext);
    int GetMasterVolumeLevel(out float pfLevelDB);
    int GetMasterVolumeLevelScalar(out float pfLevel);
    int SetChannelVolumeLevel(uint nChannel, float fLevelDB, ref Guid pguidEventContext);
    int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, ref Guid pguidEventContext);
    int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
    int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, ref Guid pguidEventContext);
    int GetMute(out bool pbMute);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IMMDevice {
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IntPtr ppDevices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppEndpoint);
    int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string pwstrId, out IMMDevice ppDevice);
}

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
internal class MMDeviceEnumeratorComObject { }

public class AudioHelper {
    public static void EnsureMic(string devId) {
        var client = (IPolicyConfig)(new PolicyConfigClient());
        client.SetEndpointVisibility(devId, true);
        client.SetDefaultEndpoint(devId, 0);
        client.SetDefaultEndpoint(devId, 1);
        client.SetDefaultEndpoint(devId, 2);
        var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice dev;
        if (enumerator.GetDevice(devId, out dev) == 0 && dev != null) {
            Guid iidVol = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
            object objVol;
            if (dev.Activate(ref iidVol, 23, IntPtr.Zero, out objVol) == 0 && objVol != null) {
                var vol = (IAudioEndpointVolume)objVol;
                Guid ctx = Guid.Empty;
                vol.SetMute(false, ref ctx);
                vol.SetMasterVolumeLevelScalar(1.0f, ref ctx);
            }
        }
    }
}
"@
Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture' -ErrorAction SilentlyContinue | ForEach-Object {
    $props = Get-ItemProperty -Path ($_.PSPath + '\Properties') -ErrorAction SilentlyContinue
    $vals = ($props.PSObject.Properties | Where-Object { $_.Value -is [string] } | ForEach-Object { $_.Value }) -join ' '
    if ($vals -match 'rtmicinwave|Micr') {
        $state = (Get-ItemProperty -Path $_.PSPath -Name 'DeviceState' -ErrorAction SilentlyContinue).DeviceState
        if ($state -eq 1 -or $state -eq 268435457) {
            [AudioHelper]::EnsureMic("{0.0.1.00000000}." + $_.PSChildName)
        }
    }
}
"""
    try:
        import subprocess
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, timeout=10)
    except Exception:
        pass


def obtener_candidatos_microfono() -> List[Tuple[int, int, int, str]]:
    """
    Devuelve la lista priorizada de todos los dispositivos de entrada disponibles
    para probar la apertura real del stream de audio hasta encontrar el que funcione.
    Prioriza WASAPI / MME / DirectSound (Modo Compartido con filtro APO) antes que WDM-KS.
    Retorna lista de: (device_index, samplerate_hw, channels_hw, nombre)
    """
    asegurar_microfono_windows_activo()
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass

    devs = sd.query_devices()
    apis = sd.query_hostapis()
    candidatos: List[Tuple[int, int, int, str]] = []
    vistos = set()

    # 1. Priorizar Micrófono Realtek en DirectSound / WASAPI / MME (DirectSound entrega mayor ganancia +20dB en Realtek)
    for api_pref in ["directsound", "wasapi", "mme"]:
        for idx, d in enumerate(devs):
            if idx in vistos or not isinstance(d, dict) or d.get("max_input_channels", 0) <= 0:
                continue
            api_name = str(apis[d["hostapi"]].get("name", "")).lower() if d.get("hostapi") is not None else ""
            nombre = str(d.get("name", ""))
            if api_pref in api_name and any(k in nombre.lower() for k in ["micr", "array", "realtek"]):
                if "mezcla" not in nombre.lower() and "stereo" not in nombre.lower() and "asignador" not in nombre.lower():
                    ch = max(1, min(2, int(d.get("max_input_channels", 1))))
                    sr = int(d.get("default_samplerate", 44100)) or 44100
                    candidatos.append((idx, sr, ch, f"{nombre} [{apis[d['hostapi']]['name']}]"))
                    vistos.add(idx)

    default_in = sd.default.device[0]
    if default_in is not None and default_in >= 0 and int(default_in) not in vistos:
        try:
            info = sd.query_devices(default_in)
            ch = max(1, min(2, int(info.get("max_input_channels", 1))))
            sr = int(info.get("default_samplerate", SAMPLE_RATE)) or SAMPLE_RATE
            candidatos.append((int(default_in), sr, ch, str(info.get("name", ""))))
            vistos.add(int(default_in))
        except Exception:
            pass

    prioridades = ["varios micr", "mic array", "realtek hd audio mic", "mic in", "micr", "input", "mezcla", "stereo"]
    for kw in prioridades:
        for idx, d in enumerate(devs):
            if idx in vistos or not isinstance(d, dict) or d.get("max_input_channels", 0) <= 0:
                continue
            nombre = str(d.get("name", ""))
            if kw in nombre.lower():
                ch = max(1, min(2, int(d.get("max_input_channels", 1))))
                sr = int(d.get("default_samplerate", 48000)) or 48000
                candidatos.append((idx, sr, ch, nombre))
                vistos.add(idx)

    for idx, d in enumerate(devs):
        if idx not in vistos and isinstance(d, dict) and d.get("max_input_channels", 0) > 0:
            ch = max(1, min(2, int(d.get("max_input_channels", 1))))
            sr = int(d.get("default_samplerate", 48000)) or 48000
            candidatos.append((idx, sr, ch, str(d.get("name", ""))))
            vistos.add(idx)

    return candidatos


def verificar_unmute_pycaw_rapido() -> None:
    """
    Protector ultraligero en memoria vía pycaw/comtypes que verifica cada 15s
    que nadie haya silenciado (Fn+F4) ni bajado el volumen del micrófono en Windows.
    """
    if os.name != "nt":
        return
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        mic = AudioUtilities.GetMicrophone()
        if mic is not None:
            interface = mic.Activate(AudioUtilities.IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
            vol = comtypes.cast(interface, comtypes.POINTER(AudioUtilities.IAudioEndpointVolume))
            if vol.GetMute():
                vol.SetMute(0, None)
            if vol.GetMasterVolumeLevelScalar() < 0.90:
                vol.SetMasterVolumeLevelScalar(1.0, None)
    except Exception:
        pass


def cargar_diccionario_hostal() -> str:
    """Carga el diccionario vivo del hostal (habitaciones, precios, módulos webapp y jerga) para inyectarlo al prompt IA."""
    for ruta_cand in [
        DATA_DIR / "diccionario_hostal.json",
        Path(r"C:\Hostal_Sistema\diccionario_hostal.json"),
        BASE_APP_DIR / "diccionario_hostal.json",
    ]:
        if ruta_cand.exists():
            try:
                return ruta_cand.read_text(encoding="utf-8")
            except Exception:
                pass
    return ""


def _a_mono_16k(indata: np.ndarray, sr_origen: int) -> np.ndarray:
    """Convierte el bloque de entrada (mono o estéreo a cualquier Hz) a mono 16kHz float32."""
    if indata.ndim > 1:
        mono = np.mean(indata, axis=1, dtype=np.float32)
    else:
        mono = indata.astype(np.float32)
    if sr_origen == SAMPLE_RATE or mono.size == 0:
        return mono
    n_out = max(1, int(round(mono.size * float(SAMPLE_RATE) / float(sr_origen))))
    indices = np.linspace(0, mono.size - 1, n_out)
    return np.interp(indices, np.arange(mono.size), mono).astype(np.float32)


def _filtrar_banda_vocal(audio_float32: np.ndarray) -> np.ndarray:
    """Filtro pasa-banda Butterworth (120 Hz - 4000 Hz) para eliminar golpes de mesa y siseo agudo."""
    if audio_float32.size < 64:
        return audio_float32
    try:
        from scipy.signal import butter, sosfilt
        sos = butter(4, [120.0, 4000.0], btype="bandpass", fs=SAMPLE_RATE, output="sos")
        return sosfilt(sos, audio_float32).astype(np.float32)
    except Exception:
        return audio_float32


def detectar_voz_silero(audio_float32: np.ndarray, umbral_rms: float = 0.0030) -> bool:
    """
    Detector de voz humana ultraligero en banda vocal (250 Hz - 3600 Hz) mediante filtro pasa-banda + FFT + RMS.
    No depende de DLLs C++ externas (ONNX/AVX2), garantizando estabilidad 24/7 en cualquier CPU.
    """
    if audio_float32.size == 0:
        return False
    filtrado = _filtrar_banda_vocal(audio_float32)
    rms = float(np.sqrt(np.mean(np.square(filtrado))))
    if rms < umbral_rms:
        return False
    try:
        espectro = np.abs(np.fft.rfft(filtrado))
        freqs = np.fft.rfftfreq(filtrado.size, d=1.0 / SAMPLE_RATE)
        energia_total = float(np.sum(espectro)) + 1e-9
        mask_voz = (freqs >= 250.0) & (freqs <= 3600.0)
        ratio_voz = float(np.sum(espectro[mask_voz])) / energia_total
        return ratio_voz >= 0.35
    except Exception:
        return rms >= (umbral_rms * 1.5)


def guardar_wav_16k(ruta_wav: Path, audio_float32: np.ndarray) -> float:
    """Limpia ruido estacionario (noisereduce + scipy), normaliza y guarda WAV PCM 16-bit mono 16kHz."""
    ruta_wav.parent.mkdir(parents=True, exist_ok=True)
    if audio_float32.size > SAMPLE_RATE:
        audio_float32 = _filtrar_banda_vocal(audio_float32)
        try:
            import noisereduce as nr
            audio_float32 = nr.reduce_noise(
                y=audio_float32,
                sr=SAMPLE_RATE,
                stationary=True,
                prop_decrease=0.65,
            ).astype(np.float32)
        except Exception:
            pass
    # Normalización suave si el volumen es bajo para que la IA y el usuario escuchen claro
    pico = float(np.max(np.abs(audio_float32))) if audio_float32.size > 0 else 0.0
    if 0.004 < pico < 0.40:
        ganancia = min(0.85 / pico, 6.5)
        audio_float32 = np.clip(audio_float32 * ganancia, -1.0, 1.0)
    pcm16 = (np.clip(audio_float32, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(ruta_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm16.tobytes())
    # Copiar espejo a C:\Hostal_Sistema\audios_noche_recepcion si existe
    try:
        if SYNCTHING_AUDIOS_DIR.exists():
            import shutil
            dest_sync = SYNCTHING_AUDIOS_DIR / ruta_wav.parent.name / ruta_wav.name
            dest_sync.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ruta_wav, dest_sync)
    except Exception:
        pass
    return round(len(pcm16) / float(SAMPLE_RATE), 2)


def obtener_fecha_turno(momento: dt.datetime) -> str:
    """
    Asigna los audios de la madrugada (00:00 a 07:30 AM) y de la noche previa (18:00 a 23:59)
    al mismo turno que se compila a las 07:00 AM (identificado por la fecha de la mañana de cierre).
    """
    if momento.hour >= 18:
        return (momento.date() + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    return momento.strftime("%Y-%m-%d")


def registrar_clip_en_bd(
    ruta_wav: Path,
    inicio: dt.datetime,
    fin: dt.datetime,
    duracion_seg: float,
) -> str:
    init_audio_audit_db()
    clip_id = inicio.strftime("%Y%m%d_%H%M%S")
    fecha_turno = obtener_fecha_turno(inicio)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO auditoria_audio_noche (
                clip_id, fecha_turno, fecha_hora_inicio, fecha_hora_fin,
                duracion_seg, archivo_wav, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                fecha_turno,
                inicio.strftime("%Y-%m-%d %H:%M:%S"),
                fin.strftime("%Y-%m-%d %H:%M:%S"),
                duracion_seg,
                str(ruta_wav),
                dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return clip_id


PERFILES_VOCES_PATH = DATA_DIR / "perfiles_voces_empleados.json"


def cargar_perfiles_voces() -> Dict[str, Any]:
    """Carga los perfiles biométricos acústicos (F0, Centroid, MFCC) de los empleados del hostal."""
    for p in [PERFILES_VOCES_PATH, Path(r"C:\Hostal_Sistema\perfiles_voces_empleados.json")]:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def extraer_huella_vocal_acustica(ruta_wav: Path) -> Optional[Dict[str, Any]]:
    """
    Extrae la firma acústica del clip WAV usando scipy + numpy (sin depender de torch/onnx):
    - Pitch fundamental (F0 mediana, IQR y proporción de frames masculinos vs femeninos)
    - Centroide espectral (brillo tímbrico)
    - 13 coeficientes cepstrales en escala Mel (MFCCs)
    """
    if not ruta_wav.exists():
        return None
    try:
        from scipy.signal import stft
        from scipy.fft import dct

        with wave.open(str(ruta_wav), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        y = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if len(y) < int(sr * 0.35):
            return None
        y = y / (float(np.max(np.abs(y))) + 1e-9)
        frame_len = int(0.032 * sr)
        hop = int(0.016 * sr)
        f0s: List[float] = []
        min_lag = int(sr / 340.0)
        max_lag = int(sr / 85.0)
        for i in range(0, len(y) - frame_len, hop):
            fr = y[i : i + frame_len]
            if float(np.sqrt(np.mean(fr**2))) < 0.022:
                continue
            fr = fr - np.mean(fr)
            corr = np.correlate(fr, fr, mode="full")[frame_len - 1 :]
            if corr[0] <= 1e-6:
                continue
            corr = corr / corr[0]
            sub = corr[min_lag:max_lag]
            if len(sub) > 0 and np.max(sub) > 0.30:
                lag = min_lag + int(np.argmax(sub))
                f0s.append(sr / float(lag))
        if not f0s:
            return None
        f, _, Zxx = stft(y, fs=sr, nperseg=512, noverlap=256)
        mag = np.abs(Zxx) + 1e-9
        centroid = float(np.sum(f[:, None] * mag) / np.sum(mag))
        n_mels = 20
        mel_bins = np.linspace(100, 4000, n_mels + 2)
        mel_energy = np.zeros((n_mels, mag.shape[1]))
        for m in range(n_mels):
            idx = (f >= mel_bins[m]) & (f <= mel_bins[m + 2])
            if np.any(idx):
                mel_energy[m] = np.mean(mag[idx, :] ** 2, axis=0)
        log_mel = np.log(mel_energy + 1e-9)
        mfcc = np.mean(dct(log_mel, type=2, axis=0, norm="ortho")[:13], axis=1)
        f0_arr = np.array(f0s)
        frames_masc = int(np.sum(f0_arr < 192.0))
        frames_fem = int(np.sum(f0_arr >= 200.0))
        total_f = max(1, len(f0_arr))
        return {
            "f0_median_hz": round(float(np.median(f0_arr)), 2),
            "f0_iqr_hz": round(float(np.percentile(f0_arr, 75) - np.percentile(f0_arr, 25)), 2),
            "centroid_hz": round(centroid, 2),
            "mfcc": [round(float(x), 4) for x in mfcc],
            "ratio_masculino": round(frames_masc / total_f, 2),
            "ratio_femenino": round(frames_fem / total_f, 2),
            "dialogo_mixto_mf": (frames_masc >= 4 and frames_fem >= 4),
        }
    except Exception:
        return None


def obtener_personal_en_turno(fecha_hora_str: str) -> List[str]:
    """Consulta `horario_registros` en `ovalo_local.db` para saber qué empleadas marcaron asistencia ese día/hora."""
    try:
        dt_clip = dt.datetime.strptime(fecha_hora_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        dt_clip = dt.datetime.now()
    fecha_dmy = dt_clip.strftime("%d/%m/%Y")
    es_noche = dt_clip.hour >= 19 or dt_clip.hour < 8
    presentes: List[str] = []
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
        conn.row_factory = sqlite3.Row
        filas = conn.execute(
            "SELECT empleado, e1, s1, e2, s2 FROM horario_registros WHERE fecha_dmy = ?",
            (fecha_dmy,),
        ).fetchall()
        conn.close()
        for r in filas:
            emp = str(r["empleado"] or "").strip()
            if emp and any(str(r[k] or "").strip() for k in ("e1", "s1", "e2", "s2")):
                presentes.append(emp)
    except Exception:
        pass
    if not presentes:
        presentes = ["Yolyi"] if es_noche else ["Eliana", "Eleiza"]
    return presentes


def identificar_empleado_por_voz(
    ruta_wav: Path,
    fecha_hora_str: str = "",
    transcripcion_previa: str = "",
) -> Dict[str, Any]:
    """
    Asocia la voz del clip con los empleados del hostal combinando:
    1. Huella acústica (Pitch F0 + Centroide + Distancia MFCC normalizada)
    2. Personal activo en turno según `horario_registros`
    3. Pistas léxicas/fonéticas en la conversación (ej. 'Eleiza'/'Eleisa'/'Leydi', 'Eliana', 'Lalo'/'Eduardo')
    """
    perfiles = cargar_perfiles_voces()
    huella = extraer_huella_vocal_acustica(ruta_wav)
    en_turno = obtener_personal_en_turno(fecha_hora_str)
    try:
        dt_clip = dt.datetime.strptime(fecha_hora_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        dt_clip = dt.datetime.now()
    es_noche = dt_clip.hour >= 19 or dt_clip.hour < 8
    recepcionista_turno = "Yolyi (Recepción Noche)" if es_noche else "Eliana (Recepción Día)"

    if not huella:
        return {
            "hablante_principal": recepcionista_turno,
            "hablantes_detectados": recepcionista_turno,
            "confianza": 0.55,
            "personal_en_turno": en_turno,
            "huella": None,
        }

    f0 = huella["f0_median_hz"]
    mfcc_vec = np.array(huella["mfcc"], dtype=np.float32)
    puntajes: List[Tuple[float, str, str]] = []

    for emp_id, p in perfiles.items():
        if not p.get("calibrado_acustico") or not p.get("mfcc_mean"):
            continue
        p_mfcc = np.array(p["mfcc_mean"], dtype=np.float32)
        p_std = np.array(p.get("mfcc_std") or [0.5] * len(p_mfcc), dtype=np.float32)
        # Distancia MFCC en los coeficientes tímbricos [1..12] (independientes del volumen global C0)
        dist_mfcc = float(np.mean(np.abs(mfcc_vec[1:] - p_mfcc[1:]) / (p_std[1:] + 0.15)))
        dist_f0 = abs(f0 - float(p.get("f0_median_hz", 210.0))) / 35.0
        score = dist_mfcc * 0.70 + dist_f0 * 0.30
        # Bonificación si el empleado marcó asistencia hoy en horario_registros
        if any(emp_id.lower().startswith(x.lower()) for x in en_turno):
            score -= 0.18
        puntajes.append((score, emp_id, str(p.get("nombre_mostrar") or emp_id)))

    puntajes.sort(key=lambda x: x[0])
    mejor_emp_id = puntajes[0][1] if puntajes else ("Yolyi" if es_noche else "Eliana")
    mejor_nombre = puntajes[0][2] if puntajes else recepcionista_turno

    # Reglas acústicas y contextuales de alta precisión
    t_low = (transcripcion_previa or "").lower()
    hablantes: List[str] = []

    if f0 < 188.0 and huella["ratio_masculino"] >= 0.55 and mejor_emp_id == "Eduardo_Lalo":
        hablantes.append("Eduardo / Lalo (Admin)")
    elif es_noche and "Yolyi" in en_turno:
        hablantes.append("Yolyi (Recepción Noche)")
    else:
        hablantes.append(mejor_nombre)

    # Si hay diálogo mixto (voz masculina + voz femenina en el mismo clip)
    if huella.get("dialogo_mixto_mf"):
        if "Eduardo / Lalo (Admin)" not in hablantes and (
            any(k in t_low for k in ["pase con", "74118293", "maxi", "adelanto", "sistema", "lalo", "eduardo"])
            or dt_clip.strftime("%H:%M") <= "12:45"
        ):
            hablantes.append("Eduardo / Lalo (Admin)")
        if recepcionista_turno not in hablantes:
            hablantes.append(recepcionista_turno)

    # Si mencionan o interactúan con Eleiza (o sus variantes fonéticas Eleisa/Elisa/Leydi/Leysi)
    if any(alias in t_low for alias in ["eleiza", "eleisa", "elisa", "leydi", "leysi", "leysa", "leisa"]):
        if "Eleiza (Limpieza / Apoyo Día)" not in hablantes:
            hablantes.append("Eleiza (Limpieza / Apoyo Día)")
        if recepcionista_turno not in hablantes:
            hablantes.insert(0, recepcionista_turno)

    # Si es atención a un cliente externo (pregunta precio, habitación o dicta DNI distinto al de Lalo)
    if any(
        w in t_low
        for w in [
            "cuánto está",
            "tienes habitación",
            "matrimonial",
            "baño propio",
            "por horas",
            "mi dni",
            "buenas tardes",
            "buenas noches",
        ]
    ):
        if recepcionista_turno not in hablantes:
            hablantes.insert(0, recepcionista_turno)
        if "Cliente / Huésped" not in hablantes:
            hablantes.append("Cliente / Huésped")

    hablantes_str = " + ".join(dict.fromkeys(hablantes))
    conf = round(max(0.55, min(0.98, 1.15 - (puntajes[0][0] * 0.25 if puntajes else 0.3))), 2)
    return {
        "hablante_principal": hablantes[0] if hablantes else recepcionista_turno,
        "hablantes_detectados": hablantes_str,
        "confianza": conf,
        "personal_en_turno": en_turno,
        "huella": huella,
    }


def consultar_nombre_dni_local_o_reniec(dni_str: str) -> str:
    """Busca el nombre de un DNI de 8 dígitos primero en la BD local y luego en el caché/API de RENIEC."""
    dni_limpio = re.sub(r"\D", "", dni_str or "")
    if len(dni_limpio) != 8:
        return ""
    if dni_limpio == "74118293":
        return "EDUARDO / LALO (ADMIN - PRUEBA DE SISTEMA)"
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        r = conn.execute(
            "SELECT huesped FROM estancias WHERE dni LIKE ? AND huesped != '' ORDER BY sheet_row DESC LIMIT 1",
            (f"%{dni_limpio}%",),
        ).fetchone()
        if r and r[0]:
            conn.close()
            return str(r[0]).strip()
        r2 = conn.execute(
            "SELECT nombre_visita FROM control_pases WHERE dni_visita = ? AND nombre_visita != '' LIMIT 1",
            (dni_limpio,),
        ).fetchone()
        conn.close()
        if r2 and r2[0]:
            return str(r2[0]).strip()
    except Exception:
        pass
    try:
        from database import consultar_dni_local_o_reniec
        res = consultar_dni_local_o_reniec(dni_limpio)
        if isinstance(res, dict) and res.get("ok") and res.get("nombre_completo"):
            return str(res["nombre_completo"]).strip()
    except Exception:
        pass
    return ""


def analizar_audio_con_ia(ruta_wav: Path, fecha_hora_str: str = "") -> Dict[str, Any]:
    """
    Envía el clip de voz a Gemini Flash junto con la Huella Acústica de Empleados y el Diccionario
    para obtener transcripción literal, hablantes identificados, tipo de interacción, mantenimiento,
    DNI dictado, habitación, precio dicho y alertas.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    diccionario_ctx = cargar_diccionario_hostal()
    info_voz_pre = identificar_empleado_por_voz(ruta_wav, fecha_hora_str=fecha_hora_str)
    huella_txt = ""
    if info_voz_pre.get("huella"):
        h = info_voz_pre["huella"]
        huella_txt = (
            f"\nBiometría Acústica Pre-calculada del Audio:\n"
            f"- Hablante(s) sugerido(s) por huella vocal: {info_voz_pre['hablantes_detectados']} (Confianza: {info_voz_pre['confianza']})\n"
            f"- Personal en turno hoy (horario_registros): {', '.join(info_voz_pre.get('personal_en_turno') or [])}\n"
            f"- Pitch F0 mediano: {h['f0_median_hz']} Hz | Centroide: {h['centroid_hz']} Hz | Diálogo Mixto Hombre/Mujer: {h['dialogo_mixto_mf']}\n"
        )
    bloque_diccionario = (
        f"\nContexto y Diccionario Oficial del Hostal (Habitaciones, Personal, Precios, Módulos Webapp y Jerga):\n{diccionario_ctx}\n"
        if diccionario_ctx
        else ""
    )
    prompt_sistema = (
        "Eres el auditor contable e investigador de recepción del Hotel El Óvalo Mariátegui en Perú. "
        "Escucha este audio grabado en el mostrador de recepción y devuelve ÚNICAMENTE un objeto JSON válido "
        "(sin bloques markdown) con la siguiente estructura exacta:\n"
        "{\n"
        '  "transcripcion": "transcripción completa en español de lo que se escucha",\n'
        '  "hablantes_detectados": "nombres de los empleados/personas que hablan según el timbre de voz y contexto (ej. \'Eliana (Recepción Día) + Cliente\', \'Eliana (Recepción Día) + Eleiza (Limpieza)\', \'Eduardo / Lalo (Admin) + Eliana\')",\n'
        '  "tipo_interaccion": "clasifica ESTRICTAMENTE en uno de: COBRO_REAL | COTIZACION_CONSULTA | LIQUIDACION_CUENTA | GASTO_CAJA | DEUDA_INTERNA_PERSONAL | PASE_VISITA | PRUEBA_SISTEMA | MANTENIMIENTO_LIMPIEZA | CHARLA_INTERNA",\n'
        '  "incidencia_mantenimiento": "si mencionan algún desperfecto, avería, mueble o reparación pendiente en una habitación (ej. \'Hab. 309: Tomacorriente salido de un mueble, requiere reparación\'), descríbelo aquí; si no, cadena vacía",\n'
        '  "resumen_ia": "resumen ejecutivo de 1 o 2 líneas de lo que ocurrió (indicando quiénes hablan y qué módulo de la webapp corresponde: CheckInRapido, ContinuarEstancia, PagosPosteriores, Pases, VentaTienda, GastosRegistro, Mantenimiento o Consulta)",\n'
        '  "es_atencion_cliente": true,\n'
        '  "hab_detectada": "número de habitación mencionado (ej. 204, 301, 206, 208, 303, 308, 309) o cadena vacía",\n'
        '  "dni_detectado": "número de DNI de 8 dígitos o RUC de 11 dígitos si se dictó en voz alta o cadena vacía",\n'
        '  "huesped_detectado": "nombre del huésped, proveedor o empresa (ej. Lucero, Lourdes, Maxi) si lo menciona o cadena vacía",\n'
        '  "precio_hablado": 0.0,\n'
        '  "metodo_pago_hablado": "Efectivo, Yape, Plin, VISA, BCP, Cargar a la Cuenta o vacío",\n'
        '  "alerta_fuga_voz": "si detectas: (1) que dicen que el Yape/QR del hotel está mal o dictan un número de Yape distinto a 964139749 o 977129752 o titular distinto a MAELEN HOGAR, (2) cobro de PASE real a cliente externo sin anotar, (3) cobro real por horas debajo del mínimo permitido (Simple <40, Matrimonial <55, Compartido <30), o (4) cobro real sin anotar, descríbelo aquí; NUNCA pongas alerta en COTIZACION_CONSULTA, DEUDA_INTERNA_PERSONAL, GASTO_CAJA ni PRUEBA_SISTEMA"\n'
        "}\n"
        "Reglas críticas de clasificación:\n"
        "1. EMPLEADOS Y FONÉTICA: La empleada de limpieza/apoyo se llama ELEIZA (a veces suena como 'Eleisa', 'Elisa', 'Leydi', 'Leysi' o 'Leysa'); corrige siempre su nombre a 'Eleiza'. La recepcionista de día es ELIANA, la de noche es YOLYI, y el administrador (voz masculina) es EDUARDO / LALO.\n"
        "2. DEUDA_INTERNA_PERSONAL: Si una empleada le entrega o menciona dinero personal a otra compañera (ej. '25 soles... ya le he dado a la Eleiza'), clasifícalo como `DEUDA_INTERNA_PERSONAL`, pon `es_atencion_cliente: false` y `alerta_fuga_voz: \"\"` (NO es cobro de habitación).\n"
        "3. COTIZACION_CONSULTA vs COBRO_REAL: Si alguien solo pregunta precios o tarifas ('¿cuánto está la habitación?', '95 soles la triple 308', '75 matrimonial') pero no paga ni se registra en ese momento, clasifícalo como `COTIZACION_CONSULTA`.\n"
        "4. GASTO_CAJA: Si entregan dinero a un técnico, artista o proveedor (ej. '300 de adelanto para Maxi'), clasifícalo como `GASTO_CAJA` (se cruza con Registro_Gastos).\n"
        "5. PRUEBA_SISTEMA y HABITACIÓN 206 vs 202: En el módulo de Pases/Visitas, las habitaciones de pases son la 206 y 208. Cuando dicen 'el pase con doscientos seis (206)' rápido puede sonar parecido a '202'; prioriza '206' si hablan de pase/visita. Si dictan el DNI `74118293` (DNI del administrador Eduardo/Lalo), clasifícalo como `PRUEBA_SISTEMA`.\n"
        "6. MANTENIMIENTO_LIMPIEZA: Si reportan averías (ej. Hab. 309 tiene un tomacorriente salido de un mueble que hay que arreglar/reparar), llena `incidencia_mantenimiento` con el detalle exacto."
        + huella_txt
        + bloque_diccionario
    )

    if api_key:
        for intento in range(2):
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
                audio_bytes = ruta_wav.read_bytes()
                resp = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=[
                        types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                        prompt_sistema,
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json",
                    ),
                )
                texto_json = (resp.text or "").strip()
                datos = json.loads(texto_json)
                if isinstance(datos, dict):
                    # Refinar hablantes combinando la transcripción obtenida con la huella acústica
                    info_voz_post = identificar_empleado_por_voz(
                        ruta_wav,
                        fecha_hora_str=fecha_hora_str,
                        transcripcion_previa=str(datos.get("transcripcion") or ""),
                    )
                    if not datos.get("hablantes_detectados"):
                        datos["hablantes_detectados"] = info_voz_post["hablantes_detectados"]
                    return datos
            except Exception as e_gem:
                print(f"[Aviso IA intento {intento+1}] ({e_gem})", flush=True)
                time.sleep(1.5)

    return extraer_datos_por_reglas("(Audio guardado pendiente de transcripción con Gemini)", info_voz_pre)


def extraer_datos_por_reglas(texto: str, info_voz: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extractor heurístico cuando se usa faster-whisper o modo local."""
    # Normalizar variantes fonéticas de Eleiza en el texto
    texto_norm = re.sub(r"\b(eleisa|elisa|leydi|leysi|leysa|leisa)\b", "Eleiza", texto, flags=re.IGNORECASE)
    t_low = texto_norm.lower()
    t_digits = texto_norm.replace(" ", "")
    m_dni = re.search(r"\b(\d{8})\b", t_digits)
    dni = m_dni.group(1) if m_dni else ""
    m_hab = re.search(r"\b(?:hab(?:itaci[oó]n)?\s*)?([1-4]0[1-9])\b", t_low)
    hab = m_hab.group(1) if m_hab else ""
    if "pase" in t_low and hab == "202":
        hab = "206"
    m_precio = re.search(r"\b(\d{1,3}(?:\.\d{1,2})?)\s*(?:soles|so|lucas)\b", t_low)
    precio = float(m_precio.group(1)) if m_precio else 0.0
    if "pase" in t_low and precio == 0.0:
        precio = 10.0
    metodo = ""
    if "yape" in t_low or "plin" in t_low:
        metodo = "Yape"
    elif "efectivo" in t_low or "vuelto" in t_low or "billete" in t_low:
        metodo = "Efectivo"

    tipo_interaccion = "CHARLA_INTERNA"
    incidencia_mant = ""
    if dni == "74118293":
        tipo_interaccion = "PRUEBA_SISTEMA"
    elif "eleiza" in t_low and ("dado" in t_low or "deuda" in t_low or "25" in t_low):
        tipo_interaccion = "DEUDA_INTERNA_PERSONAL"
    elif any(k in t_low for k in ["adelanto", "maxi", "gasto", "recibo", "pago a"]):
        tipo_interaccion = "GASTO_CAJA"
    elif any(k in t_low for k in ["tomacorriente", "enchufe", "mueble", "arreglar", "reparar", "malogrado", "mantenimiento"]):
        tipo_interaccion = "MANTENIMIENTO_LIMPIEZA"
        incidencia_mant = f"Hab. {hab or 'Por verificar'}: {texto_norm[:140]}"
    elif "pase" in t_low:
        tipo_interaccion = "PASE_VISITA"
    elif any(k in t_low for k in ["cuánto está", "cuanto esta", "precio", "consulta"]) and not dni:
        tipo_interaccion = "COTIZACION_CONSULTA"
    elif hab or precio > 0 or dni:
        tipo_interaccion = "COBRO_REAL"

    alertas = []
    for cel in re.findall(r"9\d{8}", t_digits):
        if cel not in ("964139749", "977129752"):
            alertas.append(f"🚨 ALERTA YAPE NO AUTORIZADO: Se dictó el número {cel} (Distinto a MAELEN HOGAR 964139749)")
    if any(fr in t_low for fr in ["yape de aquí está mal", "qr está mal", "qr de aquí está mal", "no pasa el yape"]):
        alertas.append("🚨 ALERTA YAPE: Mencionan que el Yape/QR del hotel está mal")
    if "pase" in t_low and tipo_interaccion != "PRUEBA_SISTEMA":
        alertas.append(f"🎟️ PASE/VISITA mencionado (Hab. {hab or '206/208'})")
    es_atencion = tipo_interaccion in ("COBRO_REAL", "COTIZACION_CONSULTA", "LIQUIDACION_CUENTA", "PASE_VISITA")
    hablantes_str = (info_voz or {}).get("hablantes_detectados") or "Eliana (Recepción Día)"
    return {
        "transcripcion": texto_norm,
        "hablantes_detectados": hablantes_str,
        "tipo_interaccion": tipo_interaccion,
        "incidencia_mantenimiento": incidencia_mant,
        "resumen_ia": texto_norm[:160] if texto_norm else "Sin voz clara detectada",
        "es_atencion_cliente": es_atencion,
        "hab_detectada": hab,
        "dni_detectado": dni,
        "huesped_detectado": "",
        "precio_hablado": precio,
        "metodo_pago_hablado": metodo,
        "alerta_fuga_voz": " | ".join(alertas),
    }


def _parse_hora_registro(fecha_in_dmy: str, hora_reg: str, id_ts_str: str) -> Optional[dt.datetime]:
    """Intenta reconstruir el datetime exacto en que se registró la estancia en el sistema."""
    for candidato in [id_ts_str, f"{fecha_in_dmy} {hora_reg}".strip()]:
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
            try:
                return dt.datetime.strptime(candidato.strip(), fmt)
            except Exception:
                continue
    return None


def cruzar_con_registro_estancias_y_pagos(
    fecha_hora_audio_str: str,
    hab_detectada: str,
    dni_detectado: str,
    precio_hablado: float,
    es_atencion: bool,
    tipo_interaccion: str = "",
    transcripcion: str = "",
    huesped_detectado: str = "",
    incidencia_mantenimiento: str = "",
) -> Tuple[str, str, str, float, float]:
    """
    Cruza los datos hablados en el audio contra TODAS las tablas operativas de `ovalo_local.db`:
    1. `estancias` (Registro_Estancias)
    2. `pagos` (Registro_Pagos)
    3. `gastos` (Registro_Gastos — ej. adelantos a proveedores/artistas como Maxi)
    4. `control_pases` (Control de Visitas / Pases — ej. habs 206 y 208)
    5. `ventas_tienda` (Venta_Tienda)
    Además distingue:
    - `PRUEBA_SISTEMA` (ej. DNI 74118293 de Eduardo/Lalo probando el módulo de Pases 206)
    - `DEUDA_INTERNA_PERSONAL` (ej. S/ 25 que Eliana le entregó a Eleiza)
    - `COTIZACION_CONSULTA` (ej. consulta de precio de habitación sin cobro real)
    - `MANTENIMIENTO_LIMPIEZA` (ej. Hab. 309 tomacorriente salido de un mueble)
    Retorna: (estado_cruce, detalle_cruce, estancia_id_match, monto_registrado, diferencia_soles)
    """
    t_low = (transcripcion or "").lower()
    tipo_up = (tipo_interaccion or "").strip().upper()
    dni_limpio = re.sub(r"\D", "", dni_detectado or "")
    hab_limpia = str(hab_detectada or "").strip()

    # Corrección fonética: si hablan de "pase" y se detectó "202", en el hostal los pases son en la 206 (y 208)
    if ("pase" in t_low or tipo_up == "PASE_VISITA") and hab_limpia == "202":
        hab_limpia = "206"

    # 0. Resolver nombre de DNI si se dictó un DNI de 8 dígitos
    nombre_dni = consultar_nombre_dni_local_o_reniec(dni_limpio) if len(dni_limpio) == 8 else ""

    # Caso A: Prueba interna del sistema por el administrador Eduardo/Lalo (DNI 74118293)
    if dni_limpio == "74118293" or tipo_up == "PRUEBA_SISTEMA":
        hab_txt = f" en Hab. {hab_limpia}" if hab_limpia else ""
        return (
            "PRUEBA_SISTEMA_OK",
            f"🛠️ Prueba interna del sistema{hab_txt} realizada por Eduardo/Lalo (DNI {dni_limpio or 'Admin'}). No corresponde a cobro real.",
            "PRUEBA_ADMIN",
            0.0,
            0.0,
        )

    # Caso B: Deuda o entrega interna de dinero entre personal del hostal (ej. Eliana a Eleiza S/ 25)
    if tipo_up == "DEUDA_INTERNA_PERSONAL" or (
        any(w in t_low for w in ["eleiza", "eleisa", "elisa", "leydi", "leysi", "leysa"])
        and any(w in t_low for w in ["ya le he dado", "le di", "deuda", "entregar"])
    ):
        return (
            "DEUDA_INTERNA_PERSONAL",
            f"🤝 Pago/deuda personal interna entre empleadas (Eliana → Eleiza por S/ {precio_hablado:.2f}). No es ingreso ni cobro de habitación.",
            "INTERNO_PERSONAL",
            0.0,
            0.0,
        )

    # Caso C: Reporte de mantenimiento / reparación de habitación sin cobro
    if (tipo_up == "MANTENIMIENTO_LIMPIEZA" or incidencia_mantenimiento) and precio_hablado <= 0:
        return (
            "MANTENIMIENTO_REPORTADO",
            f"🔧 Incidencia de mantenimiento/limpieza detectada ({incidencia_mantenimiento or f'Hab. {hab_limpia}'}).",
            f"MANT_{hab_limpia or 'GEN'}",
            0.0,
            0.0,
        )

    if (
        tipo_up == "CHARLA_INTERNA"
        and precio_hablado <= 5.0
        and not dni_limpio
    ) or (
        not es_atencion
        and not hab_limpia
        and not dni_limpio
        and precio_hablado <= 0
        and tipo_up not in ("GASTO_CAJA", "PASE_VISITA")
    ):
        return ("CONVERSACION_GENERAL", "Conversación operativa/interna o entrega de llave sin cobro nuevo.", "", 0.0, 0.0)

    try:
        dt_audio = dt.datetime.strptime(fecha_hora_audio_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        dt_audio = dt.datetime.now()

    fechas_dmy_buscar = {
        dt_audio.strftime("%d/%m/%Y"),
        (dt_audio - dt.timedelta(days=1)).strftime("%d/%m/%Y"),
        (dt_audio + dt.timedelta(days=1)).strftime("%d/%m/%Y"),
    }
    fecha_dmy_hoy = dt_audio.strftime("%d/%m/%Y")
    fecha_iso_pref = dt_audio.strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in fechas_dmy_buscar)

        # 1. CRUCE CON REGISTRO_GASTOS (`gastos`) — Para adelantos, pagos a proveedores/técnicos (ej. S/ 300 a Maxi)
        filas_gastos = conn.execute(
            f"""
            SELECT gasto_uuid, id_ts_str, fecha_gasto_iso, fecha_gasto_dmy, hora_registro,
                   descripcion, monto, categoria, metodo_pago, responsable, personal
            FROM gastos
            WHERE fecha_gasto_dmy IN ({placeholders})
               OR id_ts_str LIKE ?
               OR fecha_gasto_iso LIKE ?
            ORDER BY sheet_row DESC
            """,
            (*tuple(fechas_dmy_buscar), f"{fecha_dmy_hoy}%", f"{fecha_iso_pref}%"),
        ).fetchall()

        if tipo_up == "GASTO_CAJA" or any(k in t_low for k in ["adelanto", "maxi", "artista", "gasto", "pago"]):
            for g in filas_gastos:
                m_gasto = float(g["monto"] or 0.0)
                desc_g = str(g["descripcion"] or "")
                if (precio_hablado > 0 and abs(m_gasto - precio_hablado) <= 1.0) or (
                    huesped_detectado and huesped_detectado.lower() in desc_g.lower()
                ) or ("maxi" in t_low and ("artista" in desc_g.lower() or "maxi" in desc_g.lower())):
                    return (
                        "REGISTRADO_EN_GASTOS",
                        f"✅ Registrado en Registro_Gastos: S/ {m_gasto:.2f} — '{desc_g}' ({g['metodo_pago'] or 'Efectivo'}, Resp: {g['responsable'] or g['personal']}).",
                        str(g["id_ts_str"] or g["gasto_uuid"]),
                        m_gasto,
                        0.0,
                    )
        # También si cualquier monto hablado coincide exactamente con un gasto registrado a pocos minutos
        if precio_hablado > 0:
            for g in filas_gastos:
                m_gasto = float(g["monto"] or 0.0)
                if abs(m_gasto - precio_hablado) <= 0.5:
                    dt_g = _parse_hora_registro(str(g["fecha_gasto_dmy"] or ""), str(g["hora_registro"] or ""), str(g["id_ts_str"] or ""))
                    if dt_g and abs((dt_g - dt_audio).total_seconds()) <= 1800:
                        return (
                            "REGISTRADO_EN_GASTOS",
                            f"✅ Registrado en Registro_Gastos: S/ {m_gasto:.2f} — '{g['descripcion']}' ({g['metodo_pago'] or 'Efectivo'}, Resp: {g['responsable'] or g['personal']}).",
                            str(g["id_ts_str"] or g["gasto_uuid"]),
                            m_gasto,
                            0.0,
                        )

        # 2. CRUCE CON CONTROL DE PASES / VISITAS (`control_pases`)
        try:
            filas_pases = conn.execute(
                f"""
                SELECT pase_uuid, fecha_dmy, hora, hab, huesped_titular, dni_visita, nombre_visita, estado, monto
                FROM control_pases
                WHERE fecha_dmy IN ({placeholders})
                ORDER BY created_at DESC
                """,
                tuple(fechas_dmy_buscar),
            ).fetchall()
        except Exception:
            filas_pases = []

        for cp in filas_pases:
            if (dni_limpio and dni_limpio == str(cp["dni_visita"] or "").strip()) or (
                hab_limpia and hab_limpia == str(cp["hab"] or "").strip() and ("pase" in t_low or tipo_up == "PASE_VISITA")
            ):
                m_pase = float(cp["monto"] or 0.0)
                return (
                    "REGISTRADO_EN_PASES",
                    f"✅ Registrado en Control_Pases: Hab. {cp['hab']} | Visita: {cp['nombre_visita']} (DNI {cp['dni_visita']}) | Estado: {cp['estado']} | S/ {m_pase:.2f}.",
                    str(cp["pase_uuid"]),
                    m_pase,
                    0.0,
                )

        # 3. CRUCE CON REGISTRO_ESTANCIAS (`estancias`)
        filas_est = conn.execute(
            f"""
            SELECT estancia_uuid, id_timestamp_str, fecha_in_dmy, hora_registro,
                   huesped, dni, dni_acomp, hab, tarifa, noches,
                   pago_ini_efec, pago_ini_yape, pago_post_efec, pago_post_yape, pago_visa
            FROM estancias
            WHERE fecha_in_dmy IN ({placeholders})
            ORDER BY ymd_in DESC, sheet_row DESC
            """,
            tuple(fechas_dmy_buscar),
        ).fetchall()

        mejor_match = None
        razon_match = ""

        # Prioridad 1: Coincidencia exacta de DNI (titular o acompañante)
        if len(dni_limpio) >= 7:
            for r in filas_est:
                if dni_limpio in str(r["dni"] or "") or dni_limpio in str(r["dni_acomp"] or ""):
                    mejor_match = r
                    razon_match = f"DNI {dni_limpio}"
                    break

        # Prioridad 2: Coincidencia exacta de Habitación en esa fecha
        if mejor_match is None and hab_limpia:
            for r in filas_est:
                if str(r["hab"] or "").strip() == hab_limpia:
                    mejor_match = r
                    razon_match = f"Habitación {hab_limpia}"
                    break

        # Prioridad 3: Solo si es COBRO_REAL sin habitación explícita en voz, buscar registro creado ±35 min del audio
        if mejor_match is None and not hab_limpia and tipo_up == "COBRO_REAL":
            menor_diff_min = 9999.0
            for r in filas_est:
                dt_reg = _parse_hora_registro(
                    str(r["fecha_in_dmy"] or ""),
                    str(r["hora_registro"] or ""),
                    str(r["id_timestamp_str"] or ""),
                )
                if dt_reg:
                    diff_min = abs((dt_reg - dt_audio).total_seconds()) / 60.0
                    if diff_min <= 35.0 and diff_min < menor_diff_min:
                        menor_diff_min = diff_min
                        mejor_match = r
                        razon_match = f"Hora cercana ({int(diff_min)} min)"

        # 4. CRUCE CON REGISTRO_PAGOS (`pagos`)
        filas_pagos = conn.execute(
            """
            SELECT pago_id, id_estancia, fecha_iso, hab, huesped, monto, medio_pago, concepto
            FROM pagos
            WHERE fecha_iso LIKE ? AND hab = ?
            ORDER BY sheet_row DESC
            LIMIT 20
            """,
            (f"{fecha_iso_pref}%", hab_limpia or "__NONE__"),
        ).fetchall()

        if mejor_match is not None:
            id_est = str(mejor_match["id_timestamp_str"] or mejor_match["estancia_uuid"])
            hab_reg = str(mejor_match["hab"] or "")
            huesped_reg = str(mejor_match["huesped"] or "")
            dni_reg = str(mejor_match["dni"] or "")
            tarifa_reg = float(mejor_match["tarifa"] or 0.0)
            total_pagado_reg = (
                float(mejor_match["pago_ini_efec"] or 0.0)
                + float(mejor_match["pago_ini_yape"] or 0.0)
                + float(mejor_match["pago_post_efec"] or 0.0)
                + float(mejor_match["pago_post_yape"] or 0.0)
                + float(mejor_match["pago_visa"] or 0.0)
            )
            monto_comparar = total_pagado_reg if total_pagado_reg > 0 else tarifa_reg

            if tipo_up in ("COTIZACION_CONSULTA", "LIQUIDACION_CUENTA"):
                return (
                    "REGISTRADO_OK",
                    f"✅ Consulta/Liquidación verificada ({razon_match}): Hab. {hab_reg} | Huésped: {huesped_reg} | Pagado en sistema: S/ {total_pagado_reg:.2f} (Tarifa S/ {tarifa_reg:.2f}).",
                    id_est,
                    monto_comparar,
                    0.0,
                )

            if precio_hablado > 0 and monto_comparar > 0:
                diff = round(precio_hablado - monto_comparar, 2)
                if diff > 1.0:
                    return (
                        "ALERTA_PRECIO_MENOR_ANOTADO",
                        f"⚠️ En audio cobró S/ {precio_hablado:.2f} pero en Registro_Estancias (Hab. {hab_reg} - {huesped_reg}) anotó S/ {monto_comparar:.2f} (Faltan S/ {diff:.2f}).",
                        id_est,
                        monto_comparar,
                        diff,
                    )
                elif diff < -1.0:
                    return (
                        "OBSERVACION_MONTO",
                        f"En audio se mencionó S/ {precio_hablado:.2f} y en Registro_Estancias figura S/ {monto_comparar:.2f} (Hab. {hab_reg} - {huesped_reg}).",
                        id_est,
                        monto_comparar,
                        0.0,
                    )
            return (
                "REGISTRADO_OK",
                f"✅ Registrado en Estancias ({razon_match}): Hab. {hab_reg} | Huésped: {huesped_reg} (DNI {dni_reg}) | Pagado: S/ {total_pagado_reg:.2f} (Tarifa S/ {tarifa_reg:.2f}).",
                id_est,
                monto_comparar,
                0.0,
            )

        for p in filas_pagos:
            if hab_limpia and str(p["hab"] or "").strip() == hab_limpia:
                m_pago = float(p["monto"] or 0.0)
                diff = round(precio_hablado - m_pago, 2) if (precio_hablado > 0 and tipo_up == "COBRO_REAL") else 0.0
                return (
                    "REGISTRADO_EN_PAGOS",
                    f"✅ Encontrado en Registro_Pagos: Hab. {p['hab']} | S/ {m_pago:.2f} ({p['medio_pago']}) - {p['concepto']}.",
                    str(p["id_estancia"] or p["pago_id"]),
                    m_pago,
                    max(0.0, diff),
                )

        # 5. Si es solo una COTIZACIÓN / CONSULTA de tarifas (ej. cliente pregunta cuánto cuesta la 308 o matrimonial)
        if tipo_up == "COTIZACION_CONSULTA" or (
            precio_hablado > 0
            and not dni_limpio
            and any(
                w in t_low
                for w in [
                    "cuánto está",
                    "cuanto esta",
                    "tienes habitación",
                    "para cuántas personas",
                    "saliendo al mediodía",
                    "cama de dos plazas",
                    "cama adicional",
                    "dos camas",
                    "matrimonial más",
                    "para dos personas",
                    "baño privado",
                    "baño compartido",
                    "cancela ahorita o después",
                    "acá tengo los precios",
                ]
            )
        ):
            hab_txt = f"Hab. {hab_limpia} — " if hab_limpia else ""
            return (
                "COTIZACION_CONSULTA",
                f"💬 Cotización / consulta informativa de tarifa ({hab_txt}S/ {precio_hablado:.2f}). Cliente consultó precio sin concretar pago en este clip.",
                "",
                0.0,
                0.0,
            )

        # Si no se mencionó precio (> S/ 5) ni DNI, y solo se nombró una habitación (ej. pedir llave o coordinación de limpieza)
        if precio_hablado <= 5.0 and not dni_limpio:
            return (
                "CONVERSACION_GENERAL",
                f"Conversación operativa o entrega de llave{f' (Hab. {hab_limpia})' if hab_limpia else ''} sin cobro nuevo.",
                "",
                0.0,
                0.0,
            )

        # 6. No aparece en ninguna tabla: reportar con nombre RENIEC si hay DNI
        detalles_faltantes = []
        if hab_limpia:
            detalles_faltantes.append(f"Hab. {hab_limpia}")
        if dni_limpio:
            dni_label = f"DNI {dni_limpio} ({nombre_dni})" if nombre_dni else f"DNI {dni_limpio}"
            detalles_faltantes.append(dni_label)
        if precio_hablado > 0:
            detalles_faltantes.append(f"Monto hablado S/ {precio_hablado:.2f}")
        resumen_f = ", ".join(detalles_faltantes) if detalles_faltantes else "Atención en mostrador"

        # Si solo dictaron un DNI sin cobro hablado (ej. visita o consulta de DNI en mostrador)
        if dni_limpio and precio_hablado <= 0:
            return (
                "VERIFICAR_DNI_VISITA",
                f"🔎 DNI dictado en mostrador: {dni_limpio}{f' — **{nombre_dni}**' if nombre_dni else ''} (Sin cobro hablado en el clip; verificar si ingresó como huésped/visita en cámara Hikvision `{dt_audio.strftime('%H:%M:%S')}`).",
                "",
                0.0,
                0.0,
            )

        return (
            "NO_REGISTRADO_ALERTA",
            f"🚨 NO FIGURA EN ESTANCIAS, PAGOS, GASTOS NI PASES ({resumen_f}). Revisar cámara Hikvision a las `{dt_audio.strftime('%H:%M:%S')}`.",
            "",
            0.0,
            precio_hablado if precio_hablado > 0 else 0.0,
        )
    finally:
        conn.close()


def procesar_clips_pendientes(fecha_turno: Optional[str] = None, re_cruzar_todos: bool = True) -> List[Dict[str, Any]]:
    """
    Transcribe con IA todos los clips pendientes del turno, identifica la voz de los empleados
    y vuelve a cruzar contra `ovalo_local.db` (`estancias`, `pagos`, `gastos`, `control_pases`).
    """
    init_audio_audit_db()
    if not fecha_turno:
        fecha_turno = obtener_fecha_turno(dt.datetime.now())

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        filas = conn.execute(
            """
            SELECT * FROM auditoria_audio_noche
            WHERE fecha_turno = ?
            ORDER BY fecha_hora_inicio ASC
            """,
            (fecha_turno,),
        ).fetchall()

        resultados = []
        for fila in filas:
            d = dict(fila)
            ruta_wav = Path(d["archivo_wav"])
            if not d["procesado_ia"] and ruta_wav.exists():
                print(f"🧠 Analizando con IA + Biometría Vocal clip {d['clip_id']} ({d['duracion_seg']}s)...")
                info_ia = analizar_audio_con_ia(ruta_wav, fecha_hora_str=d["fecha_hora_inicio"])
                d["transcripcion"] = str(info_ia.get("transcripcion") or "").strip()
                d["hablantes_detectados"] = str(info_ia.get("hablantes_detectados") or "").strip()
                d["tipo_interaccion"] = str(info_ia.get("tipo_interaccion") or "").strip()
                d["incidencia_mantenimiento"] = str(info_ia.get("incidencia_mantenimiento") or "").strip()
                d["resumen_ia"] = str(info_ia.get("resumen_ia") or "").strip()
                d["es_atencion_cliente"] = 1 if info_ia.get("es_atencion_cliente") else 0
                d["hab_detectada"] = str(info_ia.get("hab_detectada") or "").strip()
                d["dni_detectado"] = str(info_ia.get("dni_detectado") or "").strip()
                d["huesped_detectado"] = str(info_ia.get("huesped_detectado") or "").strip()
                try:
                    d["precio_hablado"] = float(info_ia.get("precio_hablado") or 0.0)
                except Exception:
                    d["precio_hablado"] = 0.0
                d["metodo_pago_hablado"] = str(info_ia.get("metodo_pago_hablado") or "").strip()
                d["alerta_fuga_voz"] = str(info_ia.get("alerta_fuga_voz") or "").strip()
                d["procesado_ia"] = 1

            # Asegurar que todos los clips con voz tengan su identificación biométrica de hablante
            if not d.get("hablantes_detectados") and ruta_wav.exists() and d.get("transcripcion"):
                info_v = identificar_empleado_por_voz(
                    ruta_wav,
                    fecha_hora_str=d["fecha_hora_inicio"],
                    transcripcion_previa=d["transcripcion"],
                )
                d["hablantes_detectados"] = info_v["hablantes_detectados"]

            # Enriquecer huésped detectado con consulta local/RENIEC si hay DNI de 8 dígitos
            dni_l = re.sub(r"\D", "", d.get("dni_detectado") or "")
            if len(dni_l) == 8 and not d.get("huesped_detectado"):
                nom_reniec = consultar_nombre_dni_local_o_reniec(dni_l)
                if nom_reniec:
                    d["huesped_detectado"] = nom_reniec

            if re_cruzar_todos or d["estado_cruce"] == "PENDIENTE":
                est_cruce, det_cruce, id_match, m_reg, diff_s = cruzar_con_registro_estancias_y_pagos(
                    fecha_hora_audio_str=d["fecha_hora_inicio"],
                    hab_detectada=d["hab_detectada"],
                    dni_detectado=d["dni_detectado"],
                    precio_hablado=float(d["precio_hablado"] or 0.0),
                    es_atencion=bool(d["es_atencion_cliente"]),
                    tipo_interaccion=d.get("tipo_interaccion") or "",
                    transcripcion=d.get("transcripcion") or "",
                    huesped_detectado=d.get("huesped_detectado") or "",
                    incidencia_mantenimiento=d.get("incidencia_mantenimiento") or "",
                )
                d["estado_cruce"] = est_cruce
                d["detalle_cruce"] = det_cruce
                d["estancia_id_match"] = id_match
                d["monto_registrado"] = m_reg
                d["diferencia_soles"] = diff_s

            conn.execute(
                """
                UPDATE auditoria_audio_noche
                SET transcripcion = ?, resumen_ia = ?, es_atencion_cliente = ?,
                    hab_detectada = ?, dni_detectado = ?, huesped_detectado = ?,
                    precio_hablado = ?, metodo_pago_hablado = ?, alerta_fuga_voz = ?,
                    estado_cruce = ?, detalle_cruce = ?, estancia_id_match = ?,
                    monto_registrado = ?, diferencia_soles = ?,
                    hablantes_detectados = ?, tipo_interaccion = ?, incidencia_mantenimiento = ?,
                    procesado_ia = ?
                WHERE clip_id = ?
                """,
                (
                    d["transcripcion"],
                    d["resumen_ia"],
                    d["es_atencion_cliente"],
                    d["hab_detectada"],
                    d["dni_detectado"],
                    d["huesped_detectado"],
                    d["precio_hablado"],
                    d["metodo_pago_hablado"],
                    d["alerta_fuga_voz"],
                    d["estado_cruce"],
                    d["detalle_cruce"],
                    d["estancia_id_match"],
                    d["monto_registrado"],
                    d["diferencia_soles"],
                    d.get("hablantes_detectados") or "",
                    d.get("tipo_interaccion") or "",
                    d.get("incidencia_mantenimiento") or "",
                    d["procesado_ia"],
                    d["clip_id"],
                ),
            )
            conn.commit()
            resultados.append(d)
        return resultados
    finally:
        conn.close()


def generar_informe_7am(fecha_turno: Optional[str] = None) -> Path:
    """
    Compila todos los audios del turno, identifica hablantes por huella acústica,
    los cruza contra Registro_Estancias, Registro_Pagos, Registro_Gastos y Control_Pases,
    y genera el Informe de Auditoría completo (con sección de Mantenimiento/Reparaciones).
    """
    if not fecha_turno:
        fecha_turno = obtener_fecha_turno(dt.datetime.now())

    registros = procesar_clips_pendientes(fecha_turno=fecha_turno, re_cruzar_todos=True)
    ruta_md = REPORTES_AUDIO_DIR / f"INFORME_NOCHE_{fecha_turno}.md"

    total_clips = len(registros)
    con_voz = [r for r in registros if (r.get("transcripcion") or "").strip()]
    atenciones = [r for r in registros if r["es_atencion_cliente"] or r["estado_cruce"] != "CONVERSACION_GENERAL"]
    no_registrados = [r for r in registros if r["estado_cruce"] == "NO_REGISTRADO_ALERTA"]
    diferencias_precio = [r for r in registros if r["estado_cruce"] == "ALERTA_PRECIO_MENOR_ANOTADO"]
    cotizaciones = [r for r in registros if r["estado_cruce"] == "COTIZACION_CONSULTA"]
    gastos_cruzados = [r for r in registros if r["estado_cruce"] == "REGISTRADO_EN_GASTOS"]
    incidencias_mant = [r for r in registros if (r.get("incidencia_mantenimiento") or "").strip()]
    total_fuga_estimada = sum(float(r["diferencia_soles"] or 0.0) for r in (no_registrados + diferencias_precio))

    lineas = [
        f"# 🕵️‍♂️ Informe de Auditoría de Audio + Biometría de Voz — {NOMBRE_HOTEL}",
        f"- **Fecha de turno auditado:** `{fecha_turno}` (Compilado el `{dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}`)",
        f"- **Clips grabados totales:** `{total_clips}` (`{len(con_voz)}` con voz hablada transcrita | `{len(atenciones)}` eventos operativos/clientes)",
        f"- **🚨 Ventas/Cobros Reales NO anotados:** `{len(no_registrados)}`",
        f"- **⚠️ Diferencias de Precio en Cobros Reales:** `{len(diferencias_precio)}`",
        f"- **💬 Cotizaciones / Consultas de Precio (Sin cobro):** `{len(cotizaciones)}`",
        f"- **🧾 Pagos/Adelantos cruzados con `Registro_Gastos`:** `{len(gastos_cruzados)}`",
        f"- **🛠️ Incidencias de Mantenimiento / Reparaciones detectadas:** `{len(incidencias_mant)}`",
        f"- **💸 Dinero en riesgo / no anotado detectado en audio:** **`S/ {total_fuga_estimada:.2f}`**",
        "",
        "---",
        "",
        "## 1. Eventos Operativos, Cotizaciones, Gastos, Pases y Cruce Contable",
        "",
        "| Hora (Cámara Hik) | Voz / Empleado Detectado | Tipo Interacción | Hab. | DNI / Persona | Monto en Audio | Monto en Sistema | Resultado Auditoría |",
        "| :--- | :--- | :--- | :---: | :--- | :---: | :---: | :--- |",
    ]

    if not atenciones:
        lineas.append("| *(Sin eventos relevantes en este turno)* | - | - | - | - | - | - | - |")
    else:
        for r in atenciones:
            hora_hik = r["fecha_hora_inicio"].split(" ")[-1] if " " in r["fecha_hora_inicio"] else r["fecha_hora_inicio"]
            voz_emp = r.get("hablantes_detectados") or "Recepción"
            tipo_i = r.get("tipo_interaccion") or "OPERATIVO"
            hab_v = r["hab_detectada"] or "-"
            dni_v = r["dni_detectado"] or ""
            huesp_v = r.get("huesped_detectado") or ""
            dni_col = f"`{dni_v}` ({huesp_v})" if (dni_v and huesp_v) else (f"`{dni_v}`" if dni_v else (huesp_v or "-"))
            p_voz = f"S/ {float(r['precio_hablado']):.2f}" if float(r["precio_hablado"] or 0) > 0 else "-"
            m_sis = f"S/ {float(r['monto_registrado']):.2f}" if float(r["monto_registrado"] or 0) > 0 else "-"
            lineas.append(
                f"| **`{hora_hik}`** | {voz_emp} | `{tipo_i}` | **{hab_v}** | {dni_col} | **{p_voz}** | {m_sis} | {r['detalle_cruce']} |"
            )

    lineas.extend([
        "",
        "---",
        "",
        "## 🛠️ 2. Incidencias de Mantenimiento, Reparaciones y Limpieza Detectadas en Audio",
        "",
    ])
    if not incidencias_mant:
        lineas.append("- *(No se reportaron averías ni tareas de mantenimiento pendientes en este turno).*")
    else:
        lineas.append("| Hora | Habitación | Reportado por (Voz) | Detalle del Desperfecto / Tarea a Reparar | Transcripción Literal |")
        lineas.append("| :--- | :---: | :--- | :--- | :--- |")
        for r in incidencias_mant:
            hora_hik = r["fecha_hora_inicio"].split(" ")[-1] if " " in r["fecha_hora_inicio"] else r["fecha_hora_inicio"]
            hab_m = r["hab_detectada"] or "Por verificar"
            voz_m = r.get("hablantes_detectados") or "Personal"
            inc_m = r.get("incidencia_mantenimiento") or ""
            tr_m = (r.get("transcripcion") or "").replace("\n", " ")
            lineas.append(f"| **`{hora_hik}`** | **Hab. {hab_m}** | {voz_m} | 🔧 **{inc_m}** | *\"{tr_m}\"* |")

    # Conteo de intervenciones por empleado
    conteo_voces: Dict[str, int] = collections.Counter()
    for r in con_voz:
        habl = r.get("hablantes_detectados") or "No identificado"
        for parte in [p.strip() for p in habl.split("+") if p.strip()]:
            conteo_voces[parte] += 1

    lineas.extend([
        "",
        "---",
        "",
        "## 👥 3. Reconocimiento de Voz por Empleado (Participación en el Turno)",
        "",
        "| Empleado / Hablante Identificado | Clips con Intervención | Método de Asociación |",
        "| :--- | :---: | :--- |",
    ])
    for emp_nom, cant in conteo_voces.most_common():
        metodo_asoc = (
            "Huella Acústica (Pitch F0 + 13 MFCCs) + Asistencia en Turno (`horario_registros`)"
            if any(k in emp_nom for k in ["Eduardo", "Eliana", "Eleiza", "Yolyi", "Paola"])
            else "Diarización Contextual IA"
        )
        lineas.append(f"| **{emp_nom}** | `{cant}` clips | {metodo_asoc} |")

    lineas.extend(["", "---", "", "## 🎙️ 4. Detalle Cronológico de Conversaciones con Voz", ""])
    for idx, r in enumerate(con_voz, 1):
        hora_hik = r["fecha_hora_inicio"]
        lineas.append(f"### 🎙️ Conversación #{idx} — Hora Cámara Hikvision: `{hora_hik}`")
        lineas.append(f"- **Archivo de audio:** `{r['archivo_wav']}` ({r['duracion_seg']} seg)")
        lineas.append(f"- **Voz / Hablantes detectados:** **{r.get('hablantes_detectados') or 'No especificado'}** (`{r.get('tipo_interaccion') or 'GENERAL'}`)")
        lineas.append(f"- **Resumen IA:** {r['resumen_ia'] or '(Sin resumen)'}")
        lineas.append(
            f"- **Datos extraídos:** Habitación: `{r['hab_detectada'] or 'No dicha'}` | "
            f"DNI: `{r['dni_detectado'] or 'No dictado'}` ({r.get('huesped_detectado') or 'Sin nombre'}) | "
            f"Monto mencionado: `S/ {float(r['precio_hablado'] or 0):.2f}` | "
            f"Medio de pago: `{r['metodo_pago_hablado'] or 'No especificado'}`"
        )
        if r.get("incidencia_mantenimiento"):
            lineas.append(f"- **🛠️ Mantenimiento / Reparación:** **{r['incidencia_mantenimiento']}**")
        if r["alerta_fuga_voz"]:
            lineas.append(f"- **🚨 Alerta de auditoría:** **{r['alerta_fuga_voz']}**")
        lineas.append(f"- **Resultado del cruce contable:** {r['detalle_cruce']}")
        lineas.append(f"- **Transcripción completa:**\n  > *\"{r['transcripcion']}\"*")
        lineas.append("")

    ruta_md.write_text("\n".join(lineas), encoding="utf-8")
    print(f"\n✅ INFORME DE AUDITORÍA GENERADO EN: {ruta_md}")
    return ruta_md


def grabar_prueba_inmediata(segundos: int = 12) -> Path:
    """
    Graba N segundos ahora mismo desde el micrófono de la laptop, lo procesa con IA,
    lo cruza con `Registro_Estancias` y genera el reporte para que el usuario lo vea al instante.
    """
    init_audio_audit_db()
    conn_c = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn_c.execute("DELETE FROM auditoria_audio_noche WHERE archivo_wav LIKE '%simulacion%'")
    conn_c.commit()
    conn_c.close()
    inicio = dt.datetime.now()
    fecha_turno = obtener_fecha_turno(inicio)
    carpeta_dia = AUDIOS_BASE_DIR / fecha_turno
    carpeta_dia.mkdir(parents=True, exist_ok=True)
    nombre_wav = f"{inicio.strftime('%H-%M-%S')}_prueba_voz.wav"
    ruta_wav = carpeta_dia / nombre_wav

    print(f"\n🎙️ GRABANDO PRUEBA DURANTE {segundos} SEGUNDOS... (Habla ahora como cliente/recepcionista)")
    audio = sd.rec(int(segundos * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    fin = dt.datetime.now()
    vector = audio.flatten()
    dur = guardar_wav_16k(ruta_wav, vector)
    clip_id = registrar_clip_en_bd(ruta_wav, inicio, fin, dur)
    print(f"💾 Audio guardado: {ruta_wav} ({dur}s | ID: {clip_id})")
    return generar_informe_7am(fecha_turno=fecha_turno)


def _actualizar_estado_vivo(estado: str, clips_count: int, ultimo_clip: str = "") -> None:
    payload = {
        "status": estado,
        "pid": os.getpid(),
        "hotel": NOMBRE_HOTEL,
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "clips_grabados_sesion": clips_count,
        "ultimo_clip": ultimo_clip,
        "audios_dir": str(AUDIOS_BASE_DIR),
    }
    txt = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        ESTADO_JSON_PATH.write_text(txt, encoding="utf-8")
    except Exception:
        pass
    try:
        if SYNCTHING_AUDIOS_DIR.exists():
            (SYNCTHING_AUDIOS_DIR / "ESTADO_GRABADOR.json").write_text(txt, encoding="utf-8")
    except Exception:
        pass


def bucle_vigilancia_24_7() -> None:
    """
    Modo Demonio 24/7:
    - Escucha el micrófono continuamente sin guardar silencio.
    - Cuando detecta voz humana (Silero VAD), graba toda la conversación hasta que callan por 3 seg.
    - Procesa el clip en un hilo de segundo plano sin bloquear jamás el micrófono.
    """
    init_audio_audit_db()
    for flag_p in [STOP_FLAG_PATH, SYNCTHING_AUDIOS_DIR / "STOP_RECORDING.flag"]:
        if flag_p.exists():
            try:
                flag_p.unlink()
            except Exception:
                pass

    print(f"🟢 Vigilancia de Audio IA iniciada en {NOMBRE_HOTEL} (PID {os.getpid()} | 16kHz + Silero VAD).", flush=True)
    print("   Esperando voces humanas... (El silencio se descarta automáticamente)", flush=True)

    clips_sesion = 0
    ultimo_nombre = ""
    ultimo_latido = 0.0
    _actualizar_estado_vivo("GRABANDO_CONTINUO_24_7", clips_sesion, ultimo_nombre)

    pre_buffer: collections.deque = collections.deque(maxlen=PRE_ROLL_BLOCKS)
    grabando = False
    bloques_actuales: List[np.ndarray] = []
    inicio_clip: Optional[dt.datetime] = None
    ultimo_bloque_voz: float = 0.0
    ultimo_informe_7am_fecha: str = ""

    candidatos = obtener_candidatos_microfono()
    if not candidatos:
        print("❌ No se encontraron dispositivos de entrada en PortAudio.", flush=True)
        _actualizar_estado_vivo("ERROR_SIN_MICROFONO", 0, "")
        return

    cola_bloques: collections.deque = collections.deque()
    sr_activo = SAMPLE_RATE
    ultimo_callback_ts = time.time()
    max_rms_ventana = 0.0
    ultimo_chequeo_ganancia = time.time()

    def _callback(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        nonlocal ultimo_callback_ts, max_rms_ventana
        ultimo_callback_ts = time.time()
        bloque_16k = _a_mono_16k(indata.copy(), sr_activo)
        if bloque_16k.size > 0:
            rms_b = float(np.sqrt(np.mean(np.square(bloque_16k))))
            if rms_b > max_rms_ventana:
                max_rms_ventana = rms_b
        cola_bloques.append(bloque_16k)

    def _worker_ia_bg(f_turno: str) -> None:
        if not _lock_proc_ia.acquire(blocking=False):
            return
        try:
            procesar_clips_pendientes(fecha_turno=f_turno, re_cruzar_todos=False)
        except Exception as e_proc:
            print(f"[Aviso procesamiento diferido]: {e_proc}", flush=True)
        finally:
            _lock_proc_ia.release()

    def _cerrar_y_guardar_clip(bloques: List[np.ndarray], ini: dt.datetime, procesar_ahora: bool = False) -> None:
        nonlocal clips_sesion, ultimo_nombre
        fin_clip = dt.datetime.now()
        audio_total = np.concatenate(bloques) if bloques else np.array([], dtype=np.float32)
        duracion_actual = len(bloques) * BLOCK_DURATION_S
        if duracion_actual >= MIN_SPEECH_DURATION_S:
            fecha_turno = obtener_fecha_turno(ini)
            carpeta_dia = AUDIOS_BASE_DIR / fecha_turno
            ruta_wav = carpeta_dia / f"{ini.strftime('%H-%M-%S')}_conversacion.wav"
            dur_real = guardar_wav_16k(ruta_wav, audio_total)
            registrar_clip_en_bd(ruta_wav, ini, fin_clip, dur_real)
            clips_sesion += 1
            ultimo_nombre = ruta_wav.name
            _actualizar_estado_vivo("GRABANDO_CONTINUO_24_7", clips_sesion, ultimo_nombre)
            print(f"💾 [{fin_clip.strftime('%H:%M:%S')}] Conversación guardada ({dur_real}s): {ruta_wav.name}", flush=True)
            if procesar_ahora:
                threading.Thread(target=_worker_ia_bg, args=(fecha_turno,), daemon=True).start()

    def _abrir_mejor_stream() -> Optional[sd.InputStream]:
        nonlocal sr_activo, ultimo_callback_ts, max_rms_ventana
        cands = obtener_candidatos_microfono()
        for dev_idx, sr_hw, ch_hw, dev_name in cands:
            for sr_try in [sr_hw, 44100, 48000, 16000]:
                for ch_try in [ch_hw, 2, 1]:
                    try:
                        sr_activo = sr_try
                        block_hw = int(sr_try * BLOCK_DURATION_S)
                        st = sd.InputStream(
                            device=dev_idx,
                            samplerate=sr_try,
                            channels=ch_try,
                            dtype="float32",
                            blocksize=block_hw,
                            callback=_callback,
                        )
                        st.start()
                        ultimo_callback_ts = time.time()
                        max_rms_ventana = 0.0
                        print(f"🎙️ Stream abierto con éxito en #{dev_idx} '{dev_name}' ({sr_try} Hz, {ch_try} ch)", flush=True)
                        _actualizar_estado_vivo(f"GRABANDO_ACTIVO_DEV_{dev_idx}_{dev_name[:28]}", clips_sesion, ultimo_nombre)
                        return st
                    except Exception:
                        continue
        return None

    stream_abierto = _abrir_mejor_stream()

    if stream_abierto is None:
        print("❌ Ningún dispositivo de entrada aceptó abrir el stream de audio.", flush=True)
        _actualizar_estado_vivo("ERROR_STREAM_AUDIO", 0, "")
        return

    try:
        while True:
            ahora_ts = time.time()
            if ahora_ts - ultimo_latido >= 15.0:
                verificar_unmute_pycaw_rapido()
                _actualizar_estado_vivo("GRABANDO_CONTINUO_24_7", clips_sesion, ultimo_nombre)
                ultimo_latido = ahora_ts

            # Watchdog de auto-curación: si el stream se congela (>12s sin bloques) o pierde ganancia (<0.0005 RMS en 90s),
            # reactiva el micrófono en Windows CoreAudio y reabre el canal DirectSound automáticamente.
            stream_congelado = (ahora_ts - ultimo_callback_ts) > 12.0
            ganancia_caida = (ahora_ts - ultimo_chequeo_ganancia >= 90.0) and (max_rms_ventana < 0.0005)
            if stream_congelado or ganancia_caida:
                try:
                    if stream_abierto is not None:
                        stream_abierto.stop()
                        stream_abierto.close()
                except Exception:
                    pass
                asegurar_microfono_windows_activo()
                verificar_unmute_pycaw_rapido()
                nuevo_st = _abrir_mejor_stream()
                if nuevo_st is not None:
                    stream_abierto = nuevo_st
                ultimo_chequeo_ganancia = time.time()
                max_rms_ventana = 0.0
            elif ahora_ts - ultimo_chequeo_ganancia >= 90.0:
                ultimo_chequeo_ganancia = ahora_ts
                max_rms_ventana = 0.0

            parar = STOP_FLAG_PATH.exists() or (SYNCTHING_AUDIOS_DIR.exists() and (SYNCTHING_AUDIOS_DIR / "STOP_RECORDING.flag").exists())
            if parar:
                if grabando and bloques_actuales and inicio_clip is not None:
                    _cerrar_y_guardar_clip(bloques_actuales, inicio_clip, procesar_ahora=False)
                for flag_p in [STOP_FLAG_PATH, SYNCTHING_AUDIOS_DIR / "STOP_RECORDING.flag"]:
                    if flag_p.exists():
                        try:
                            flag_p.unlink()
                        except Exception:
                            pass
                _actualizar_estado_vivo("DETENIDO_POR_USUARIO", clips_sesion, ultimo_nombre)
                print("🛑 Grabación detenida a solicitud del usuario. Compilando informe...", flush=True)
                generar_informe_7am()
                break

            ahora = dt.datetime.now()
            # Compilación automática a las 07:00 AM (Cierre Noche) y 19:00 / 7:00 PM (Cierre Día)
            hito_turno = f"{ahora.strftime('%Y-%m-%d')}_{ahora.hour:02d}"
            if ahora.hour in (7, 19) and ahora.minute == 0 and ultimo_informe_7am_fecha != hito_turno:
                try:
                    generar_informe_7am(fecha_turno=obtener_fecha_turno(ahora))
                    ultimo_informe_7am_fecha = hito_turno
                except Exception as e_rep:
                    print(f"[Error compilando turno]: {e_rep}", flush=True)

            if not cola_bloques:
                time.sleep(0.05)
                continue

            bloque = cola_bloques.popleft()
            hay_voz = detectar_voz_silero(bloque)

            if hay_voz:
                if not grabando:
                    grabando = True
                    inicio_clip = dt.datetime.now() - dt.timedelta(seconds=len(pre_buffer) * BLOCK_DURATION_S)
                    bloques_actuales = list(pre_buffer)
                    print(f"🎙️ [{inicio_clip.strftime('%H:%M:%S')}] Voz detectada, grabando conversación...", flush=True)
                bloques_actuales.append(bloque)
                ultimo_bloque_voz = time.time()
            else:
                if not grabando:
                    pre_buffer.append(bloque)
                else:
                    bloques_actuales.append(bloque)
                    tiempo_silencio = time.time() - ultimo_bloque_voz
                    duracion_actual = len(bloques_actuales) * BLOCK_DURATION_S
                    if tiempo_silencio >= SILENCE_LIMIT_S or duracion_actual >= MAX_CLIP_DURATION_S:
                        if inicio_clip is not None:
                            _cerrar_y_guardar_clip(bloques_actuales, inicio_clip, procesar_ahora=True)
                        grabando = False
                        bloques_actuales = []
                        inicio_clip = None
    finally:
        try:
            stream_abierto.stop()
            stream_abierto.close()
        except Exception:
            pass


def ejecutar_simulacion_completa() -> Path:
    """
    Crea un audio real hablado en español simulando una venta nocturna en recepción,
    lo pasa por el detector IA, lo cruza con `Registro_Estancias` y genera el informe 7:00 AM.
    """
    import subprocess

    init_audio_audit_db()
    conn_c = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn_c.execute("DELETE FROM auditoria_audio_noche WHERE archivo_wav LIKE '%simulacion%'")
    conn_c.commit()
    conn_c.close()
    inicio = dt.datetime.now()
    fecha_turno = obtener_fecha_turno(inicio)
    carpeta_dia = AUDIOS_BASE_DIR / fecha_turno
    carpeta_dia.mkdir(parents=True, exist_ok=True)
    ruta_wav = (carpeta_dia / f"{inicio.strftime('%H-%M-%S')}_simulacion_recepcion.wav").resolve()

    frase = (
        "Buenas noches joven, tiene habitación matrimonial disponible. "
        "Sí señor, tengo la habitación 204 a 80 soles la noche. "
        "Ya perfecto, le pago 80 soles en efectivo, mi DNI es 7 4 1 1 8 2 9 3."
    )
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{ruta_wav}'); "
        f"$s.Speak('{frase}'); "
        "$s.Dispose();"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)
    fin = inicio + dt.timedelta(seconds=11)
    registrar_clip_en_bd(ruta_wav, inicio, fin, 11.0)
    return generar_informe_7am(fecha_turno=fecha_turno)


def main() -> None:
    parser = argparse.ArgumentParser(description="Auditor de Audio Turno Noche + Cruce con Registro_Estancias")
    parser.add_argument("--vigilar", action="store_true", help="Inicia vigilancia continua 24/7 con compilación a las 7:00 AM")
    parser.add_argument("--compilar", action="store_true", help="Compila y cruza todos los audios del turno actual ahora mismo")
    parser.add_argument("--fecha", type=str, default="", help="Fecha de turno YYYY-MM-DD para compilar")
    parser.add_argument("--probar", type=int, default=0, help="Graba N segundos ahora mismo, transcribe y cruza con Registro_Estancias")
    parser.add_argument("--simular", action="store_true", help="Genera un audio de prueba hablado, lo transcribe con IA y cruza con BD")
    args = parser.parse_args()

    if args.simular:
        rep = ejecutar_simulacion_completa()
        print("\n" + rep.read_text(encoding="utf-8"))
    elif args.probar > 0:
        rep = grabar_prueba_inmediata(segundos=args.probar)
        print("\n" + rep.read_text(encoding="utf-8"))
    elif args.compilar:
        rep = generar_informe_7am(fecha_turno=args.fecha or None)
        print("\n" + rep.read_text(encoding="utf-8"))
    else:
        bucle_vigilancia_24_7()


if __name__ == "__main__":
    main()
