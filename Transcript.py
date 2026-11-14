import json
from faster_whisper import WhisperModel

# Load speech recognition model
model = WhisperModel("base", device="cpu", compute_type="int8")

# Input video
video_path = "lecture.mp4"

# Transcribe video
segments, info = model.transcribe(
    video_path,
    beam_size=5
)

# Store transcript
transcript = []

for segment in segments:
    transcript.append({
        "start": round(segment.start, 2),
        "end": round(segment.end, 2),
        "text": segment.text.strip()
    })

# Export JSON
with open("transcript.json", "w", encoding="utf-8") as f:
    json.dump(transcript, f, indent=2, ensure_ascii=False)

print("Transcription completed!")
print("Saved as transcript.json")
