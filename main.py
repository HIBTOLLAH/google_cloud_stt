import os
from google.cloud import storage
from google.cloud import speech_v1
import subprocess
from pydub.utils import mediainfo
import datetime
from datetime import timedelta
import srt
import sys
from dotenv import load_dotenv
import logging
from pydub import AudioSegment

def main():
    try:
      
        channels, bit_rate, sample_rate = video_info(video_path)

       
        audio_filename = timestamp + "_audio.wav"
        blob_name = video_to_audio(video_path, audio_filename, channels, bit_rate, sample_rate)

       
        gcs_uri = f"gs://{BUCKET_NAME}/{blob_name}"

        
        response = long_running_recognize(gcs_uri, channels, sample_rate ,audio_filename)

       
        write_srt(response)
        write_txt(response)

        logging.info("Transcription completed successfully.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")


def upload_blob(bucket_name, source_file_name, destination_blob_name, timeout=None):
    """Uploads a file to the bucket."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    blob.upload_from_filename(source_file_name, timeout=timeout)

    print(
        "File {} uploaded to {}.".format(
            source_file_name, destination_blob_name
        )
    )


def video_info(video_filepath):
    """Returns number of channels, bit rate, and sample rate of the video."""
    video_data = mediainfo(video_filepath)
    channels = video_data.get("channels", 2)  # Default to 2 channels if not found
    bit_rate = video_data.get("bit_rate", 128000)  # Default to a reasonable bit rate
    sample_rate = video_data.get("sample_rate", 44100)  # Default to a standard sample rate

    return channels, bit_rate, sample_rate


def video_to_audio(video_filepath, audio_filename, video_channels, video_bit_rate, video_sample_rate):
    """Converts video into audio with enhanced quality settings for Turkish."""
    command = (
        f"ffmpeg -i {video_filepath} "
        f"-b:a {video_bit_rate} "
        f"-ac {video_channels} "
        f"-ar {video_sample_rate} "
        f"-af \"highpass=f=200, lowpass=f=3000, volume=2\" "  # Enhance audio quality
        f"-vn {audio_filename}"
    )
    subprocess.call(command, shell=True)
    blob_name = f"audios/{audio_filename}"
    upload_blob(BUCKET_NAME, audio_filename, blob_name)
    return blob_name


def long_running_recognize(storage_uri, channels, sample_rate ,audio_filename):
    """Transcribes the audio with optimized settings for Turkish."""
    client = speech_v1.SpeechClient()

    config = {
        "language_code": LANG,
        "sample_rate_hertz": int(sample_rate),
        "encoding": speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
        "audio_channel_count": int(channels),
        "enable_word_time_offsets": True,
        "model": "command_and_search",  
        "use_enhanced": True,  
        "speech_contexts": [
            {"phrases": ["umut", "ışık", "gözlerinde", "bekleyenim", "hatice", "sıcaklık",
                        "ellerinde", "yanarım", "aşkın", "sağolsun", "kapımı", "çal",
                        "ansızın", "gir", "içeri", "her zaman", "başımın üstünde",
                        "senin yerin", "sözcükler", "uçarsa", "aklımdan", "benim güzel",
                        "misafirim", "hep hoşgeldin", "sevda mısın", "yoksa", "yalan dolan",
                        "püsküllü", "belam", "olup", "derde salan", "yaşanmamışlık",
                        "özünde", "içimde", "uhde kalan", "biriktirdiğim", "aşkları",
                        "hiçe sayan"]}  
        ]
    }
    audio = {"uri": storage_uri}

    print(f"Using the config: {config}")
    print(f"Audio file location: {audio}")

    operation = client.long_running_recognize(config=config, audio=audio)

    print(u"Waiting for operation to complete...")
    response = operation.result(timeout=1000000)

    return process_response(response, audio_filename)




def process_response(response, audio_filename):
    subs = []
    previous_end_time = None
    max_duration_per_subtitle = 6 

    
    audio_segment = AudioSegment.from_wav(audio_filename)

    for result in response.results:
        alternative = result.alternatives[0]

        subs = break_sentences_by_time(subs, alternative, max_duration_per_subtitle)

        if previous_end_time is not None:
            current_start_time = (
                alternative.words[0].start_time.seconds
                + alternative.words[0].start_time.microseconds / 1e6
            )
            gap_duration = current_start_time - previous_end_time

            if gap_duration > 2:  
               
                start_ms = int(previous_end_time * 1000)
                end_ms = int(current_start_time * 1000)
                gap_audio = audio_segment[start_ms:end_ms]

                
                if gap_audio.rms > 50:  
                   
                    subs.append(
                        srt.Subtitle(
                            index=len(subs) + 1,
                            start=timedelta(seconds=previous_end_time),
                            end=timedelta(seconds=current_start_time),
                            content="",  
                        )
                    )

       
        previous_end_time = (
            alternative.words[-1].end_time.seconds
            + alternative.words[-1].end_time.microseconds / 1e6
        )

    return subs


def break_sentences_by_time(subs, alternative, max_duration_per_subtitle):
   
    idx = len(subs) + 1
    content = ""
    start = None
    end = None
    current_duration = 0

    for w in alternative.words:
        word = w.word.strip()

        if start is None:
           
            start = w.start_time
            current_duration = 0

        
        if content:
            content += " " 
        content += word

        end = w.end_time
        current_duration = (
            end.seconds + end.microseconds / 1e6
        ) - (start.seconds + start.microseconds / 1e6)

        if current_duration >= max_duration_per_subtitle:
            subs.append(
                srt.Subtitle(
                    index=idx,
                    start=timedelta(
                        seconds=start.seconds, microseconds=start.microseconds
                    ),
                    end=timedelta(seconds=end.seconds, microseconds=end.microseconds),
                    content=srt.make_legal_content(content.strip()),
                )
            )

           
            idx += 1
            content = ""
            start = None
            end = None

   
    if content and start is not None and end is not None:
        subs.append(
            srt.Subtitle(
                index=idx,
                start=timedelta(seconds=start.seconds, microseconds=start.microseconds),
                end=timedelta(seconds=end.seconds, microseconds=end.microseconds),
                content=srt.make_legal_content(content.strip()),
            )
        )

    return subs

def post_process_text(text):
    """Post-processes the transcribed text to fix common errors in Turkish."""
    corrections = {
        "umut": "umut",
        "yok": "yok",
        "ışık": "ışık",
        "gözlerinde": "gözlerinde",
        "bekleyenim": "bekleyenim",
        "hatice": "hatice",
        "sıcaklık": "sıcaklık",
        "ellerinde": "ellersinde",
        "yanarım": "yanarım",
        "aşkın": "aşkın",
        "sağolsun": "sağolsun",
        "kapımı": "kapımı",
        "çal": "çal",
        "ansızın": "ansızın",
        "gir": "gir",
        "içeri": "içeri",
        "her zaman": "her zaman",
        "başımın üstünde": "başımın üstünde",
        "senin yerin": "senin yerin",
        "sözcükler": "sözcükler",
        "uçarsa": "uçarsa",
        "aklımdan": "aklımdan",
        "benim güzel": "benim güzel",
        "misafirim": "misafirim",
        "hep hoşgeldin": "hep hoşgeldin",
        "sevda mısın": "sevda mısın",
        "yoksa": "yoksa",
        "yalan dolan": "yalan dolan",
        "püsküllü": "püsküllü",
        "belam": "belam",
        "olup": "olup",
        "derde salan": "derde salan",
        "yaşanmamışlık": "yaşanmamışlık",
        "özünde": "özünde",
        "içimde": "içimde",
        "uhde kalan": "uhde kalan",
        "biriktirdiğim": "biriktirdiğim",
        "aşkları": "aşkları",
        "hiçe sayan": "hiçe sayan"
    }

    words = text.split()
    corrected_words = []

    for word in words:
        stripped_word = word.strip(".,!?")
        if stripped_word in corrections:
            corrected_word = corrections[stripped_word]
        else:
            # Try to find a similar word (basic context-based matching)
            similar_word = next((key for key in corrections.keys() if stripped_word.startswith(key[:3])), None)
            corrected_word = corrections.get(similar_word, stripped_word) if similar_word else stripped_word
        corrected_words.append(corrected_word)

    corrected_text = " ".join(corrected_words)
    return corrected_text


def write_srt(subs):
    """Writes SRT file."""
    srt_file = timestamp + "_subtitles.srt"
    with open(srt_file, mode="w", encoding="utf-8") as f:
        f.writelines(srt.compose(subs))


def write_txt(subs):
    """Writes TXT file with post-processing for Turkish."""
    txt_file = timestamp + "_subtitles.txt"
    with open(txt_file, mode="w", encoding="utf-8") as f:
        for s in subs:
            content = post_process_text(s.content.strip()) + "\n"
            f.write(content)


def timestamp():
    """Gets current date and time."""
    current_datetime = datetime.datetime.now()
    str_current_datetime = str(current_datetime).replace(" ", "_").replace(":", "_")
    return str_current_datetime



load_dotenv()
BUCKET_NAME = str(os.getenv('BUCKET_NAME'))
MAX_CHARS = int(os.getenv('MAX_CHARS', 20))  # Default to 70 if not set
FFMPEG_LOCATION = str(os.getenv('FFMPEG_LOCATION'))
FFPROBE_LOCATION = str(os.getenv('FFPROBE_LOCATION'))


mediainfo.converter = FFMPEG_LOCATION
mediainfo.ffmpeg = FFMPEG_LOCATION
mediainfo.ffprobe = FFPROBE_LOCATION


if len(sys.argv) != 3:
    print("Missing command-line argument. Usage: python main.py example.mp4 tr-TR")
    exit(1)
video_path = sys.argv[1]
LANG = sys.argv[2]  

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ("client_service_key.json")


timestamp = timestamp()

logging.basicConfig(filename='transcription.log', level=logging.INFO)

if __name__ == "__main__":
    main()