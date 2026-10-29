"""Approved text-to-speech helpers for silent product videos."""
import asyncio
import os
import re
from pathlib import Path
import edge_tts

_LOCALE_MAP = {"ar":"ar-SA","cs":"cs-CZ","da":"da-DK","de":"de-DE","el":"el-GR","en":"en-GB","es":"es-ES","fil":"fil-PH","fr":"fr-FR","he":"he-IL","id":"id-ID","it":"it-IT","ja":"ja-JP","ko":"ko-KR","lb":"ar-LB","my":"my-MM","pl":"pl-PL","ru":"ru-RU","sv":"sv-SE","th":"th-TH","tl":"fil-PH","tr":"tr-TR","ur":"ur-PK","vi":"vi-VN","zh":"zh-CN","zh-hant":"zh-HK"}

def language_code(value):
    value = str(value or "en").strip().lower().replace("_", "-")
    return "zh-hant" if value in {"zh-hk","zh-tw","zh-mo","zh-hant"} else value.split("-", 1)[0]

def preview_script(text, max_chars=360):
    clean = re.sub(r"https?://\S+|#[\w-]+", " ", str(text or ""))
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-") + "."

async def available_voices(language):
    locale = _LOCALE_MAP.get(language_code(language), "en-GB").lower()
    voices = await edge_tts.list_voices()
    exact = [v for v in voices if str(v.get("Locale", "")).lower() == locale]
    if exact:
        return exact
    base = locale.split("-", 1)[0]
    return [v for v in voices if str(v.get("Locale", "")).lower().startswith(base + "-")]

def choose_voices(language, count=3):
    voices = asyncio.run(available_voices(language))
    ordered = ([v for v in voices if str(v.get("Gender", "")).lower() == "female"][:2] +
               [v for v in voices if str(v.get("Gender", "")).lower() == "male"][:1])
    for voice in voices:
        if voice not in ordered:
            ordered.append(voice)
        if len(ordered) >= count:
            break
    return ordered[:count]

def synthesize(text, voice_name, output_path):
    text = preview_script(text)
    if not text:
        raise ValueError("Voice script is empty")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(edge_tts.Communicate(text, voice_name).save(output_path))
    if not Path(output_path).exists() or Path(output_path).stat().st_size < 1000:
        raise RuntimeError("Text-to-speech did not produce usable audio")
    return output_path

def enabled():
    return str(os.getenv("APPROVED_VOICEOVER_ENABLED", "true")).lower() in {"1","true","yes"}
